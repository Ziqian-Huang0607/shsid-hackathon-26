"""Reporting: penalty breakdown, baseline-vs-optimised comparison, and
exports (CSV timetables + metrics.json + report.md)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict

from . import timegrid as tg
from .cost import hard_violation_count, inst_detail
from .model import Instance

SOFT_KEYS = [
    "subject_alternation", "pe_timing", "subject_time_diversity",
    "self_study", "workload", "odd_even_stability", "teacher_mobility",
    "cognitive_load",
    "same_day_repeat", "overshoot", "student_overload", "prime_time_reward",
]


def summarize(inst: Instance) -> Dict:
    detail = inst_detail(inst)
    hard = hard_violation_count(inst)
    soft = {k: round(detail.get(k, 0.0), 2) for k in SOFT_KEYS}
    soft_total = round(sum(v for k, v in soft.items()
                           if k != "prime_time_reward"), 2)
    n_students = max(1, len(inst.students))
    return {
        "hard_violations": hard,
        "soft_components": soft,
        "soft_total": soft_total,
        "soft_per_student": round(soft_total / n_students, 3),
        "raw_detail": {k: round(v, 2) for k, v in detail.items()},
    }


def compare(baseline: Dict, optimised: Dict) -> str:
    rows = ["| Metric | Baseline (current-style) | Optimised | Change |",
            "|---|---|---|---|"]
    rows.append(f"| Hard violations | {baseline['hard_violations']} "
                f"| {optimised['hard_violations']} | "
                f"{optimised['hard_violations'] - baseline['hard_violations']:+d} |")
    keys = [k for k in SOFT_KEYS if k != "prime_time_reward"]
    for k in keys:
        b = baseline["soft_components"].get(k, 0.0)
        o = optimised["soft_components"].get(k, 0.0)
        delta = o - b
        pct = f"({delta / b:+.0%})" if b else ""
        rows.append(f"| {k} | {b:.1f} | {o:.1f} | {delta:+.1f} {pct} |")
    b, o = baseline["soft_total"], optimised["soft_total"]
    rows.append(f"| **Total soft penalty** | **{b:.1f}** | **{o:.1f}** "
                f"| **{o - b:+.1f} ({(o - b) / b:+.1%})** |")
    rows.append(f"| Soft penalty per student | {baseline['soft_per_student']:.2f} "
                f"| {optimised['soft_per_student']:.2f} | "
                f"{optimised['soft_per_student'] - baseline['soft_per_student']:+.2f} |")
    return "\n".join(rows)


# --------------------------------------------------------------- exports
def _section_at(inst: Instance, occ_owner_slots: Dict[int, int], slot: int):
    sid = occ_owner_slots.get(slot)
    return inst.sections[sid] if sid is not None else None


def export_student_csv(inst: Instance, path: Path) -> None:
    with path.open("w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["student", "grade", "week", "day", "period",
                       "course", "room", "building", "teacher"])
        for st in inst.students:
            occ = {}
            for sid in st.section_ids:
                for sl in inst.sections[sid].slots:
                    occ[sl] = sid
            for sl in sorted(occ):
                sec = _section_at(inst, occ, sl)
                c = inst.courses[sec.course_id]
                r = inst.rooms[sec.room_at(sl)]
                t = inst.teachers[sec.teacher_id]
                wcsv.writerow([st.name, st.grade, "AB"[tg.SLOT_WEEK[sl]],
                               tg.DAY_NAMES[tg.SLOT_DAY[sl]],
                               f"P{tg.SLOT_PERIOD[sl] + 1}",
                               c.name, r.name, r.building, t.name])


def export_teacher_csv(inst: Instance, path: Path) -> None:
    with path.open("w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["teacher", "week", "day", "period",
                       "course", "section", "room", "building"])
        for t in inst.teachers:
            for sid in inst.teach_secs.get(t.id, []):
                sec = inst.sections[sid]
                c = inst.courses[sec.course_id]
                for sl in sorted(sec.slots):
                    r = inst.rooms[sec.room_at(sl)]
                    wcsv.writerow([t.name, "AB"[tg.SLOT_WEEK[sl]],
                                   tg.DAY_NAMES[tg.SLOT_DAY[sl]],
                                   f"P{tg.SLOT_PERIOD[sl] + 1}",
                                   c.label, sid, r.name, r.building])


def export_sections_csv(inst: Instance, path: Path) -> None:
    with path.open("w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["section", "course", "grade", "teacher", "room",
                       "students", "required_minutes", "scheduled_minutes",
                       "sessions"])
        for sec in inst.sections:
            c = inst.courses[sec.course_id]
            home = inst.rooms[sec.room_id].name
            room = (home if not sec.slot_rooms
                    else f"{home} (+{len(sec.slot_rooms)} session overrides)")
            wcsv.writerow([sec.id, c.name, c.grade,
                           inst.teachers[sec.teacher_id].name,
                           room,
                           len(sec.students), c.minutes, sec.minutes(),
                           len(sec.slots)])


def export_all(inst: Instance, outdir: Path, metrics: Dict,
               comparison_md: str) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    export_student_csv(inst, outdir / "timetable_students.csv")
    export_teacher_csv(inst, outdir / "timetable_teachers.csv")
    export_sections_csv(inst, outdir / "sections.csv")
    (outdir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (outdir / "report.md").write_text(comparison_md)
