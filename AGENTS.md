# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project overview

This is the **HackSHSID 2026 · Track 1** project: an automatic course
timetabling engine for a school. It turns course requirements
(`data/curriculum.json`, originally extracted from the school's
`modules.xlsx`) into a full two-week (Week A / Week B) timetable for every
student, teacher and room, satisfying hard constraints H1–H11 and
minimising a weighted sum of soft penalties. `README.md` is the
authoritative design document — read it before making non-trivial changes;
it explains the structural facts (F1–F6), modelling decisions, and the
verification story that the codebase is built around.

**Runtime: pure Python 3.10+ standard library — zero runtime
dependencies.** `requirements.txt` lists only dev extras
(`pytest`, plus `pandas`/`openpyxl` used solely by
`scripts/import_modules.py`).

Key scale numbers (full school): 1000 students (250/grade), 607 sections,
~320 hire-on-demand teachers, 112 rooms, 0 hard violations, ~45 s per
optimisation pass.

## Repository layout

```
main.py                  # CLI entry: build → baseline → optimise → validate → export
data/
  curriculum.json        # grade programmes (G9–G12), course minutes, difficulty scores
  school.json            # rooms, capacity, penalty weights, thresholds (tune here, not in code)
scheduler/
  timegrid.py            # 2-week grid: 10 days × 7 periods, uneven durations 60/40/40/45/55/40/40
  planner.py             # per-cluster session-shape planning (minutes → 60/55/45/40-min sessions)
  instance.py            # students, cohort clusters, grade-wide sync groups, teacher/room assignment
  model.py               # domain dataclasses (Course/Section/Lesson/Teacher/Room/Instance)
  cost.py                # hard + soft penalty model (7 school penalties + cognitive-load extras)
  optimize.py            # greedy mirror construction, repair ladder, simulated annealing, room solver
  validate.py            # independent H1–H11 audit (does NOT share code with the optimiser)
  report.py              # metrics, baseline-vs-optimised comparison, CSV/JSON/MD exports
scripts/import_modules.py# re-extract minutes from modules.xlsx (needs pandas + openpyxl)
tests/
  run.py                 # zero-dependency test runner
  test_scheduler.py      # 9 test functions (test_*)
out/                     # run artefacts: CSVs, metrics.json, report.md, PNGs (generated, committed)
```

Pipeline flow: `instance.py` → `planner.py` → `optimize.py` (greedy →
repair ladder → annealing → per-slot room solve) → `validate.py` +
`report.py`. See README §3 for the architecture diagram.

## Build and run commands

There is no build step. Everything runs from the repo root:

```bash
python main.py run                                        # full pipeline, real scale (~2 min, best-of-3)
python main.py run --students 100 --iters 4000 --attempts 1   # fast demo
python tests/run.py                                       # run the 9 tests without pytest
python -m pytest tests/                                   # same tests via pytest (if installed)
python scripts/import_modules.py path/to/modules.xlsx     # re-derive curriculum minutes from source
```

CLI flags (all on `main.py run`): `--students N` (per grade, default 250),
`--iters N` (SA iterations per attempt, default 8000), `--attempts N`
(best-of-N restarts, default 3), `--seed N` (default 7), `--data DIR`
(default `data`), `--out DIR` (default `out`).

Outputs in `out/`: `timetable_students.csv`, `timetable_teachers.csv`,
`sections.csv`, `metrics.json`, `report.md`, plus PNG charts.

Note: above 320 students/grade the builder intentionally stops with a
diagnosis (grade-synchronised PE needs more venues than exist) — this is a
documented capacity limit (README F6), not a bug.

## Architectural invariants — do not break these

The design relies on invariants maintained by construction rather than
checked afterwards. Any change must preserve them:

- **Cohort blocking**: students are grouped into clusters of identical
  programmes (≤ `section_capacity` = 20); a *Lesson* is one cluster taking
  one course. Scheduling lessons (not sections) makes student
  double-booking impossible by construction.
- **Duration is bound to period**: 60-min sessions only in P1, 55 in P5,
  45 in P4, 40 in P2/P3/P6/P7 (`scheduler/timegrid.py`). All session
  shapes must decompose minutes exactly into these durations.
- **All SA moves preserve session counts and durations**, so H2 (exact
  minutes) is an invariant, never re-broken.
- **Grade-synchronised blocks** (PE, Optional): the lessons of one sync
  group share a single slot-set object, so H11 holds structurally.
- **Rooms are tracked during search only as per-slot demand ≤ supply per
  (building, room type)** — never breach this in a move; concrete rooms
  are assigned at the very end by `solve_rooms_per_slot`.
- **Teachers are building-loyal and hired on demand**; H10 (travel) holds
  by construction. Teacher caps are per-duration so no teacher is booked
  into more slots of a duration class than exist.
- **Hard violations are weighted ×100 000** (`hard_weight` in
  `school.json`) so the annealer treats them as forbidden.
- **`validate.py` must stay independent of the optimiser** — it re-walks
  the finished timetable from scratch. Never import cost/optimise logic
  into it.

## Code style guidelines

- Pure standard library, Python 3.10+; `from __future__ import annotations`
  at the top of every module; type hints throughout.
- Dataclasses for domain objects (`model.py`); plain functions elsewhere —
  no classes-with-state beyond `Instance`.
- No new dependencies: anything needed must be implemented with the stdlib.
- Module docstrings explain the *why* (see `planner.py`, `cost.py` for the
  house style); keep them accurate when you change behaviour.
- Comments and documentation are in English (a few Chinese terms like
  烧脑程度/精力 appear in the README's cognitive-load section — keep them
  as-is).
- Tunable numbers belong in `data/school.json` (penalty weights,
  thresholds, capacities) or as named constants at the top of the relevant
  module (e.g. `CLUSTER_CAPS` in `planner.py`) — not buried in logic.

## Testing instructions

- Run `python tests/run.py` (no dependencies) or `python -m pytest tests/`.
- Tests are plain `test_*` functions in `tests/test_scheduler.py`; the
  runner discovers them by name prefix.
- Existing tests cover: enrolment rules (IB/skip exemptions), grade-sync
  consolidation, capacity/sectioning, each major penalty detector
  (alternation, PE timing, cognitive load), minutes shortfall,
  planted-conflict detection by the validator, and an end-to-end
  "feasible and better than baseline" run.
- When changing penalty logic, add a detector-style test (build a small
  synthetic instance, plant the condition, assert the cost/validator
  catches it) — that is the established pattern.
- The end-to-end test is slow; keep it as the integration guard rather
  than adding more full-scale runs to the suite.

## Configuration and data

- `data/curriculum.json` — per-grade programmes; combined module totals
  are modelled as one course (README §2, "Modelling decisions").
- `data/school.json` — `penalty_weights`, `hard_weight`,
  `section_capacity` (20), `student_minute_budget` (3040),
  exemption fractions, and per-penalty thresholds (`workload`,
  `odd_even`, `diversity`, `teacher_mobility`, `energy`). Re-prioritising
  penalties = edit this file, not the code.
- `scripts/import_modules.py` only *prints* extracted minutes for review;
  the curated `curriculum.json` is edited by hand.

## Security considerations

- No network access, no secrets, no auth — the tool reads two local JSON
  files and writes CSV/JSON/MD reports.
- All randomness is seeded (`--seed`); do not introduce unseeded `random`
  usage — reproducibility is part of the deliverable.
- `scripts/import_modules.py` reads an arbitrary Excel path from argv;
  it is a dev-only utility, not part of the runtime pipeline.

## When changing behaviour

- Update `README.md` numbers/claims if results change (it contains
  measured tables), and keep this `AGENTS.md` in sync.
- Verify with: `python tests/run.py` then a fast pipeline run
  (`python main.py run --students 100 --iters 4000 --attempts 1`) and
  confirm the H1–H11 audit still reports 0 violations.
