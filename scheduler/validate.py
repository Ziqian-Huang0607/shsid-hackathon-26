"""Hard-constraint compliance checker — independent of the cost function.

Mapping to the school's constraint sheet ("Constraints and Penalties"):

  H1  students complete the required components, choose 6 (G11/G12) or 7
      (G9/G10) courses from the respective modules
      (school rule: G11/G12 students may drop the skippable Optional
      module, which lowers the count by one)
  H2  every section satisfies its two-week instructional minutes
  H3  no student / teacher / room is double-booked
  H4  section size does not exceed capacity (20)
  H5  courses are taught by eligible teachers (subject match) during
      their available periods
  H6  all sessions fall inside the school's teaching grid
  H7  every enrolled course has a section with an assigned teacher and a
      suitable classroom (room type matches the course requirement)
  H8  mandatory PE / Personal Growth / CAS are included
      (school rule: IB-track students in G11/G12 are exempt from PE)
  H9  session lengths use only the official period shapes 60/40/45/55
      and stay within the 7-period / 5-day week
  H10 teachers always have sufficient travel time: no teacher ever
      teaches in two different buildings in adjacent periods (in our
      instances this holds by construction — teachers are hired
      building-loyal)

H11 is an additional structural rule required by the school on top of the
published sheet: PE and Optional components are grade-synchronised — all
students of a grade who attend the component sit it at exactly the same
slots.  Exempt students simply do not attend.
"""
import json
from collections import defaultdict
from typing import Dict, List

from .model import Instance
from .timegrid import (NUM_SLOTS, SLOT_DAY, SLOT_MINUTES, SLOT_PERIOD)

MANDATORY_NONACADEMIC = {"pe", "pg", "cas"}


def validate(inst: Instance, curriculum: dict) -> Dict[str, List[str]]:
    v: Dict[str, List[str]] = {f"H{i}": [] for i in range(1, 11)}
    v["H11"] = []

    courses = {c.id: c for c in inst.courses}
    teachers = {t.id: t for t in inst.teachers}
    rooms = {r.id: r for r in inst.rooms}

    # ------------------------------------------------------------------ H1
    for st in inst.students:
        prog = curriculum["grades"][str(st.grade)]
        skippable_comps = {
            comp.get("module", comp.get("name"))
            for comp in prog["components"] if comp.get("skippable")
        }
        target = prog["academic_target"]
        mine = [courses[c] for c in st.course_ids]
        academic = sum(1 for c in mine if c.academic)
        if academic > target:
            v["H1"].append(f"{st.id}: {academic} academic > target {target}")
        # being under target is only legal by dropping a skippable module
        skipped = skippable_comps - {c.component for c in mine}
        deficit = target - academic
        if deficit > 1 or (deficit == 1 and not skipped):
            v["H1"].append(f"{st.id}: {academic} academic < target {target} "
                           "(no skippable module dropped)")
        for c in mine:
            if c.grade != st.grade:
                v["H1"].append(f"{st.id}: takes {c.id} (grade {c.grade})")

    # ------------------------------------------------------------------ H2
    for sec in inst.sections:
        minutes = sum(SLOT_MINUTES[s] for s in sec.slots)
        need = courses[sec.course_id].minutes
        if minutes != need:
            v["H2"].append(f"{sec.id}: {minutes} min != {need} min")

    # ------------------------------------------------------------------ H3
    for st in inst.students:
        occ = defaultdict(list)
        for sid in st.section_ids:
            for s in inst.sections[sid].slots:
                occ[s].append(sid)
        for slot, ids in occ.items():
            if len(ids) > 1:
                v["H3"].append(f"student {st.id}: double-booked slot {slot} {ids}")
    occ_t = defaultdict(list)
    occ_r = defaultdict(list)
    for sec in inst.sections:
        for s in sec.slots:
            occ_t[(sec.teacher_id, s)].append(sec.id)
            occ_r[(sec.room_at(s), s)].append(sec.id)
    for (t, s), ids in occ_t.items():
        if len(ids) > 1:
            v["H3"].append(f"teacher {t}: double-booked slot {s} {ids}")
    for (r, s), ids in occ_r.items():
        if len(ids) > 1:
            v["H3"].append(f"room {r}: double-booked slot {s} {ids}")

    # ------------------------------------------------------------------ H4
    for sec in inst.sections:
        if len(sec.students) > inst.section_capacity:
            v["H4"].append(f"{sec.id}: {len(sec.students)} > cap {inst.section_capacity}")

    # ------------------------------------------------------------------ H5
    for sec in inst.sections:
        t = teachers.get(sec.teacher_id)
        c = courses[sec.course_id]
        if t is None:
            v["H5"].append(f"{sec.id}: no teacher assigned")
            continue
        if c.subject not in t.subjects:
            v["H5"].append(f"{sec.id}: {t.name} not eligible for {c.subject}")
        for s in sec.slots:
            if not t.available(s):
                v["H5"].append(f"{sec.id}: {t.name} unavailable at slot {s}")

    # ------------------------------------------------------------------ H6
    for sec in inst.sections:
        for s in sec.slots:
            if not (0 <= s < NUM_SLOTS):
                v["H6"].append(f"{sec.id}: slot {s} outside teaching grid")

    # ------------------------------------------------------------------ H7
    enrolled = defaultdict(list)          # (student, course) -> sections
    for st in inst.students:
        for sid in st.section_ids:
            enrolled[(st.id, inst.sections[sid].course_id)].append(sid)
        for cid in st.course_ids:
            if not enrolled[(st.id, cid)]:
                v["H7"].append(f"{st.id}: no section for course {cid}")
    for sec in inst.sections:
        if sec.teacher_id < 0 or sec.teacher_id not in teachers:
            v["H7"].append(f"{sec.id}: no teacher")
        need = courses[sec.course_id].room_type
        for s in (sec.slots or {0}):
            room = rooms.get(sec.room_at(s))
            if room is None:
                v["H7"].append(f"{sec.id}: no room")
                break
            if need != "any" and room.room_type != need:
                v["H7"].append(
                    f"{sec.id}: {need} course in {room.room_type} room")
                break

    # ------------------------------------------------------------------ H8
    for st in inst.students:
        prog = curriculum["grades"][str(st.grade)]
        taken = {courses[c].subject for c in st.course_ids}
        for comp in prog["components"]:
            if comp["type"] != "fixed":
                continue
            subj = comp["subject"]
            if subj not in MANDATORY_NONACADEMIC:
                continue
            if subj == "pe" and comp.get("ib_exempt") and st.ib:
                continue                      # IB G11/G12 PE exemption
            if subj not in taken:
                v["H8"].append(f"{st.id}: mandatory {subj.upper()} missing")

    # ------------------------------------------------------------------ H9
    allowed = {60, 40, 45, 55}
    for sec in inst.sections:
        for s in sec.slots:
            if 0 <= s < NUM_SLOTS:
                if SLOT_MINUTES[s] not in allowed:
                    v["H9"].append(f"{sec.id}: illegal session length at slot {s}")
                if SLOT_PERIOD[s] >= 7 or SLOT_DAY[s] >= 5:
                    v["H9"].append(f"{sec.id}: outside 7-period/5-day week at {s}")

    # ----------------------------------------------------------------- H10
    for t in inst.teachers:
        per_day = defaultdict(list)
        for sid in inst.teach_secs.get(t.id, []):
            sec = inst.sections[sid]
            for s in sec.slots:
                per_day[s // 7].append((SLOT_PERIOD[s],
                                        rooms[sec.room_at(s)].building))
        for day, items in per_day.items():
            items.sort()
            for (p1, b1), (p2, b2) in zip(items, items[1:]):
                if p2 == p1 + 1 and b1 != b2:
                    v["H10"].append(f"{t.name}: {b1} -> {b2} with no travel "
                                    f"gap (day {day}, P{p1 + 1}->P{p2 + 1})")

    # ----------------------------------------------------------------- H11
    # grade-synchronised PE / Optional blocks (only components the
    # curriculum flags as grade_wide)
    grade_wide_comps = set()
    for grade_str, prog in curriculum["grades"].items():
        for comp in prog["components"]:
            if comp.get("grade_wide"):
                grade_wide_comps.add(
                    (int(grade_str), comp.get("module", comp.get("name"))))
    comp_of = {c.id: c.component for c in inst.courses}
    sync_comps = defaultdict(set)   # (grade, component) -> attending students
    st_slots = {}                   # (student, component) -> slot set
    for st in inst.students:
        per_comp = defaultdict(set)
        for sid in st.section_ids:
            comp = comp_of.get(inst.sections[sid].course_id, "")
            if (st.grade, comp) in grade_wide_comps:
                per_comp[comp] |= set(inst.sections[sid].slots)
        for comp, slots in per_comp.items():
            sync_comps[(st.grade, comp)].add(st.id)
            st_slots[(st.id, comp)] = frozenset(slots)
    for (grade, comp), members in sorted(sync_comps.items()):
        ref = None
        for sid in sorted(members):
            slots = st_slots[(sid, comp)]
            if ref is None:
                ref = slots
            elif slots != ref:
                v["H11"].append(
                    f"G{grade} {comp}: {sid} slots differ from grade block")

    return v


def violation_summary(viol: Dict[str, List[str]]) -> Dict[str, int]:
    return {k: len(v) for k, v in viol.items()}


def total_violations(viol: Dict[str, List[str]]) -> int:
    return sum(len(v) for v in viol.values())


def print_report(viol: Dict[str, List[str]]) -> bool:
    ok = True
    names = {
        "H1": "required courses / module counts",
        "H2": "two-week instructional minutes",
        "H3": "no double-booking (student/teacher/room)",
        "H4": "section capacity",
        "H5": "teacher eligibility & availability",
        "H6": "sessions inside teaching grid",
        "H7": "section, teacher & suitable room assigned",
        "H8": "mandatory PE / PG / CAS",
        "H9": "official session shapes 60/40/45/55",
        "H10": "teacher travel time between buildings",
        "H11": "grade-synchronised PE/Optional (school rule)",
    }
    for k in sorted(viol, key=lambda x: int(x[1:])):
        msgs = viol[k]
        status = "OK  " if not msgs else "FAIL"
        if msgs:
            ok = False
        print(f"  {k} {names[k]:<44} {status} ({len(msgs)} violations)")
        for m in msgs[:5]:
            print(f"       - {m}")
    return ok


def load_curriculum(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
