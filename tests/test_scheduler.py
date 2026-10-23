"""Tests: penalty units, end-to-end feasibility, validator sensitivity."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler import optimize, validate                 # noqa: E402
from scheduler.cost import (hard_violation_count, student_detail,  # noqa: E402
                            section_detail)
from scheduler.instance import build_instance, load_config  # noqa: E402
from scheduler import timegrid as tg                       # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


def small_instance(students=12, seed=3):
    return build_instance(students, seed, DATA)


def test_enrolment_rules():
    """H1/H8: course counts, module picks, PE/CAS/PG with exemptions.

    New school rules honoured here:
      * IB-track students in G11/G12 may skip PE;
      * G11/G12 students may take no Optional course (skippable module),
        which lowers a G12 academic count by one.
    """
    curriculum, _ = load_config(DATA)
    inst = small_instance()
    for st in inst.students:
        prog = curriculum["grades"][str(st.grade)]
        target = prog["academic_target"]
        has_skippable = any(c.get("skippable") for c in prog["components"])
        academic = sum(1 for c in st.course_ids if inst.courses[c].academic)
        assert academic == target or (has_skippable and academic == target - 1)
        subs = {inst.courses[c].subject for c in st.course_ids}
        assert "cas" in subs
        if st.grade in (11, 12):
            assert "pg" in subs
        pe_exempt = st.ib and st.grade in (11, 12)
        if pe_exempt:
            assert "pe" not in subs, "IB G11/G12 student must not have PE"
        else:
            assert "pe" in subs, "non-exempt student must have PE"


def test_grade_sync_and_exemption_consolidation():
    """Exemptions must survive signature consolidation, and grade-wide
    components must be single synchronised blocks (H11 by construction)."""
    curriculum, _ = load_config(DATA)
    inst = small_instance(students=40, seed=5)   # forces consolidation
    for st in inst.students:
        if st.ib and st.grade in (11, 12):
            assert not any(inst.courses[c].is_pe for c in st.course_ids)
    # every grade-wide lesson shares its slot set across its sections
    for les in inst.lessons:
        if les.grade_wide:
            for sid in les.section_ids:
                assert inst.sections[sid].slots is les.slots
    # lessons in one sync group share one slot-set object
    for gid, lids in inst.sync_groups.items():
        first = inst.lessons[lids[0]].slots
        for lid in lids[1:]:
            assert inst.lessons[lid].slots is first
    # after a real schedule the validator must see H11 clean
    rng = random.Random(2)
    optimize.optimize(inst, rng, iterations=1500)
    v = validate.validate(inst, curriculum)
    assert v["H11"] == []


def test_capacity_and_sectioning():
    """H5: no section exceeds the 20-student capacity."""
    inst = small_instance(students=60)   # forces >= 3 sections per course
    assert inst.section_capacity == 20
    for sec in inst.sections:
        assert len(sec.students) <= inst.section_capacity
        assert sec.students, "every section must be non-empty"


def test_alternation_penalty_detected():
    """Two consecutive STEM blocks in one morning => penalty; lunch resets."""
    inst = small_instance()
    st = inst.students[0]
    # craft: put the student's first two sections at P1/P2 (consecutive)
    secs = st.section_ids[:2]
    s0 = tg.slot_index(0, 0, 0)
    s1 = tg.slot_index(0, 0, 1)
    inst.sections[secs[0]].slots = {s0}
    inst.sections[secs[1]].slots = {s1}
    # force both courses to STEM
    for sid in secs:
        inst.courses[inst.sections[sid].course_id].category = "STEM"
    d = student_detail(inst, st.id)
    assert d.get("subject_alternation", 0) > 0

    # P4 then P5 (across lunch) must NOT count as consecutive
    inst.sections[secs[1]].slots = {tg.slot_index(0, 0, 4)}
    inst.sections[secs[0]].slots = {tg.slot_index(0, 0, 3)}
    d = student_detail(inst, st.id)
    assert d.get("subject_alternation", 0) == 0


def test_pe_timing_penalty():
    inst = small_instance()
    pe = next(s for s in inst.sections
              if inst.courses[s.course_id].is_pe)
    pe.slots = {tg.slot_index(0, 1, 0)}          # P1 -> bad
    assert section_detail(inst, pe.id).get("pe_timing", 0) > 0
    pe.slots = {tg.slot_index(0, 1, 2)}          # P3 -> fine
    assert section_detail(inst, pe.id).get("pe_timing", 0) == 0


def test_cognitive_load_adjacency():
    """Two difficulty-5 courses back-to-back trigger cognitive_load;
    separating them (or lunch break) clears it."""
    inst = small_instance()
    st = inst.students[0]
    secs = st.section_ids[:2]
    for sid in secs:
        inst.courses[inst.sections[sid].course_id].difficulty = 5.0
        inst.courses[inst.sections[sid].course_id].category = "OTHER"
    inst.sections[secs[0]].slots = {tg.slot_index(0, 0, 1)}   # P2
    inst.sections[secs[1]].slots = {tg.slot_index(0, 0, 2)}   # P3 adjacent
    d = student_detail(inst, st.id)
    assert d.get("cognitive_load", 0) > 0
    # move second course across lunch (P5): no adjacency
    inst.sections[secs[1]].slots = {tg.slot_index(0, 0, 4)}
    d = student_detail(inst, st.id)
    adjacency_only = d.get("cognitive_load", 0)
    # remaining cognitive_load, if any, must come from the brutal-day
    # term only -- with just two sessions the day is not overloaded
    assert adjacency_only == 0


def test_minutes_shortfall_is_hard():
    inst = small_instance()
    sec = inst.sections[0]
    sec.slots = set()                            # no sessions at all
    d = section_detail(inst, sec.id)
    assert d.get("hard_minutes_shortfall", 0) >= inst.hard_weight


def test_validator_catches_planted_conflict():
    curriculum, _ = load_config(DATA)
    inst = small_instance()
    optimize.optimize(inst, random.Random(1), iterations=1500)
    v = validate.validate(inst, curriculum)
    assert validate.total_violations(v) == 0
    # plant a conflict: force two sections of the same student onto one slot
    st = inst.students[0]
    a, b = st.section_ids[:2]
    slot = next(iter(inst.sections[a].slots))
    inst.sections[b].slots = {slot}
    v = validate.validate(inst, curriculum)
    assert len(v["H3"]) >= 1


def test_end_to_end_feasible_and_better_than_baseline():
    rng = random.Random(11)
    inst = small_instance(students=16)
    optimize.greedy_init(inst, rng, traditional=True)
    optimize.repair(inst, rng)
    from scheduler.cost import total_cost
    baseline_soft = total_cost(inst)

    for sec in inst.sections:
        sec.slots.clear()
    optimize.optimize(inst, random.Random(12), iterations=4000)
    assert hard_violation_count(inst) == 0
    assert total_cost(inst) < baseline_soft
