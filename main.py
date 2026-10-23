#!/usr/bin/env python3
"""HackSHSID 2026 Track 1 - automatic course scheduling system.

Usage:
    python main.py run [--students N] [--iters N] [--seed N] [--attempts N]
                       [--data DIR] [--out DIR]

Pipeline:
    1. build instance from data/curriculum.json + data/school.json
    2. build a "current-style" baseline timetable (rigid block schedule)
    3. optimise: greedy construction -> repair -> simulated annealing
    4. independently validate all hard constraints (H1-H10 from the brief,
       plus the school's grade-synchronisation rule H11)
    5. export timetables + metrics + comparison report
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from scheduler import optimize, report, validate
from scheduler.instance import build_instance, load_config


def clear_schedule(inst) -> None:
    for les in inst.lessons:
        les.slots.clear()
    for sec in inst.sections:
        sec.slot_rooms = {}
    inst.build_indexes()           # rebuild slot-dependent room counters


def cmd_run(args) -> None:
    data_dir = Path(args.data)
    out_dir = Path(args.out)
    curriculum, _ = load_config(data_dir)

    print(f"[1/5] Building instance ({args.students} students/grade, "
          f"seed={args.seed}) ...")
    inst = build_instance(args.students, args.seed, data_dir)
    print(f"      {len(inst.students)} students in {len(inst.clusters)} cohort "
          f"clusters, {len(inst.courses)} courses, {len(inst.lessons)} lessons, "
          f"{len(inst.sections)} sections, {len(inst.teachers)} teachers, "
          f"{len(inst.rooms)} rooms")

    print("[2/5] Generating baseline (current-style rigid timetable) ...")
    rng = random.Random(args.seed + 1)
    optimize.greedy_init(inst, rng, traditional=True)
    optimize.repair(inst, rng)
    baseline = report.summarize(inst)
    print(f"      baseline: hard={baseline['hard_violations']}  "
          f"soft={baseline['soft_total']}")

    print("[3/5] Optimising (greedy + repair + simulated annealing) ...")
    best_state, best_teachers, best_key, optimised = None, None, None, None
    t0 = time.time()
    # best-of-N restarts: feasibility is essentially guaranteed by the
    # repair ladder, so all attempts run to completion and the lowest
    # soft-penalty feasible timetable wins (hard violations dominate)
    for attempt in range(args.attempts):
        clear_schedule(inst)
        rng = random.Random(args.seed + 2 + attempt * 1000)
        optimize.optimize(inst, rng, iterations=args.iters,
                          log_every=max(1, args.iters // 4))
        result = report.summarize(inst)
        key = (result["hard_violations"], result["soft_total"])
        print(f"      attempt {attempt + 1}: hard={key[0]}  soft={key[1]}")
        if best_key is None or key < best_key:
            best_key = key
            best_state = {l.id: set(l.slots) for l in inst.lessons}
            # teacher bindings are mutated by later attempts (flex-shift
            # re-binds/hires), so they must be restored alongside slots
            best_teachers = [s.teacher_id for s in inst.sections]
            optimised = result
    for l in inst.lessons:                 # restore best attempt
        l.slots.clear()
        l.slots.update(best_state[l.id])
    for s, tid in zip(inst.sections, best_teachers):
        s.teacher_id = tid
    inst.build_indexes()
    optimize.solve_rooms_per_slot(inst, random.Random(args.seed))
    optimised = report.summarize(inst)
    print(f"      done in {time.time() - t0:.1f}s: "
          f"hard={optimised['hard_violations']}  soft={optimised['soft_total']}")

    print("[4/5] Validating hard constraints H1-H11 ...")
    violations = validate.validate(inst, curriculum)
    summary = validate.violation_summary(violations)
    total = validate.total_violations(violations)
    for hid, n in summary.items():
        print(f"      {hid}: {'OK' if n == 0 else f'{n} VIOLATIONS'}")
    if total:
        for hid, items in violations.items():
            for item in items[:5]:
                print(f"        e.g. [{hid}] {item}")

    print("[5/5] Exporting ...")
    comparison = report.compare(baseline, optimised)
    print("\n" + comparison + "\n")
    metrics = {
        "config": {k: v for k, v in vars(args).items() if k != "func"},
        "baseline": baseline,
        "optimised": optimised,
        "hard_constraint_check": summary,
    }
    header = (
        "# Timetable Optimisation Report\n\n"
        f"- Students: {len(inst.students)} | Sections: {len(inst.sections)} "
        f"| Teachers: {len(inst.teachers)} | Rooms: {len(inst.rooms)}\n"
        f"- Hard-constraint violations after optimisation: **{total}**\n"
        f"- Total soft penalty: {baseline['soft_total']:.1f} -> "
        f"**{optimised['soft_total']:.1f}** "
        f"({(optimised['soft_total'] - baseline['soft_total']) / max(1.0, baseline['soft_total']):+.1%})\n\n"
        "## Baseline vs Optimised\n\n" + comparison + "\n"
    )
    report.export_all(inst, out_dir, metrics, header)
    print(f"      written to {out_dir}/ "
          "(timetable_students.csv, timetable_teachers.csv, sections.csv, "
          "metrics.json, report.md)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Automatic course scheduling system")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="build, optimise, validate, export")
    run.add_argument("--students", type=int, default=250,
                     help="students per grade (default 250)")
    run.add_argument("--iters", type=int, default=8000,
                     help="simulated-annealing iterations (default 8000)")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--attempts", type=int, default=3,
                     help="best-of-N restarts, lowest soft penalty wins "
                          "(default 3)")
    run.add_argument("--data", default="data")
    run.add_argument("--out", default="out")
    run.set_defaults(func=cmd_run)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
