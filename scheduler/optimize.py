"""Optimisation engine over *lessons* (cohort blocks).

  greedy construction -> hard-conflict repair -> simulated annealing

Moves:
  * shift: move one session of a lesson to a new slot,
  * swap:  exchange one slot between two lessons (keeps both session
    counts and global occupancy - essential in a zero-slack grid).

All costs are sums over per-entity costs, so the exact delta of a move is
obtained by re-evaluating only the affected entities: the lesson's
sections, its cluster's students, and the involved teachers and rooms.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple

from . import timegrid as tg
from .cost import (hard_violation_count, inst_detail,
                   section_detail, student_detail, teacher_detail)
from .model import Instance, Lesson


# ------------------------------------------------------------- utilities
def _lesson_entities(inst: Instance, lesson: Lesson):
    secs = [inst.sections[sid] for sid in lesson.section_ids]
    students = set()
    for s in secs:
        students.update(s.students)     # covers grade-wide lessons too
    teachers = {s.teacher_id for s in secs}
    rooms = {s.room_id for s in secs}
    return secs, students, teachers, rooms


def _expand_sync(inst: Instance, lesson: Lesson) -> List[int]:
    """A synchronised (grade-wide) group shares one slot set: moving one
    member moves them all, so cost deltas must cover the whole group."""
    if lesson.sync_group >= 0:
        return inst.sync_groups[lesson.sync_group]
    return [lesson.id]


# Rooms are fungible within (building, room_type) and assigned PER
# SESSION at the end (solve_rooms_per_slot), so the search never tracks
# individual rooms -- only the demand-vs-supply invariant per
# (slot, building, room_type), maintained incrementally by these two
# helpers.  ALL slot-set mutations in this module go through them.
def _slots_add(inst: Instance, les: Lesson, s: int) -> None:
    if s in les.slots:
        return
    les.slots.add(s)
    dem = inst.room_demand[s]
    for lid in _expand_sync(inst, les):
        for sid in inst.lessons[lid].section_ids:
            bt = inst.sec_bt[sid]
            dem[bt] = dem.get(bt, 0) + 1


def _slots_discard(inst: Instance, les: Lesson, s: int) -> None:
    if s not in les.slots:
        return
    les.slots.discard(s)
    dem = inst.room_demand[s]
    for lid in _expand_sync(inst, les):
        for sid in inst.lessons[lid].section_ids:
            bt = inst.sec_bt[sid]
            dem[bt] -= 1


def _room_conflicts_group(inst: Instance, lesson: Lesson, slot: int) -> int:
    """Room infeasibility of placing the whole sync group at `slot`:
    sections that could not be housed because demand would exceed the
    (building, room_type) supply at that slot."""
    need: Dict[tuple, int] = {}
    for lid in _expand_sync(inst, lesson):
        for sid in inst.lessons[lid].section_ids:
            bt = inst.sec_bt[sid]
            need[bt] = need.get(bt, 0) + 1
    occupied = slot in lesson.slots
    n = 0
    for bt, k in need.items():
        d = inst.room_demand[slot].get(bt, 0)
        sup = inst.room_supply.get(bt, 0)
        if occupied:                    # demand already includes the group
            n += min(k, max(0, d - sup))
        elif d + k > sup:
            # a breach is unsolvable downstream: make the slot an
            # absolute barrier, not a tradeable conflict
            n += 10 ** 6
    return n


def _hard_conflicts_at(inst: Instance, lesson: Lesson, slot: int) -> int:
    """Student + teacher conflicts of placing `lesson` at `slot` (rooms
    use the group-level demand model -- see _room_conflicts_group)."""
    n = 0
    secs, students, _, _ = _lesson_entities(inst, lesson)
    own = set(lesson.section_ids)
    for st_id in students:
        for other in inst.students[st_id].section_ids:
            if other not in own and slot in inst.sections[other].slots:
                n += 1
                break
    for sec in secs:
        t = inst.teachers[sec.teacher_id]
        if not t.available(slot):
            n += 1
        for other in inst.teach_secs[sec.teacher_id]:
            if other not in own and slot in inst.sections[other].slots:
                n += 1
    return n


def _affected_cost(inst: Instance, lesson_ids: List[int],
                   slots_hint=()) -> float:
    secs, students, teachers = set(), set(), set()
    for lid in lesson_ids:
        s, st, t, _ = _lesson_entities(inst, inst.lessons[lid])
        secs.update(x.id for x in s)
        students.update(st)
        teachers.update(t)
    total = 0.0
    for sid in secs:
        total += sum(section_detail(inst, sid).values())
    for st in students:
        total += sum(student_detail(inst, st).values())
    for t in teachers:
        total += sum(teacher_detail(inst, t).values())
    # rooms: per-session demand excess over the touched slots
    touched = set(slots_hint)
    for lid in lesson_ids:
        touched |= inst.lessons[lid].slots
    total += (sum(inst.room_excess(s) for s in touched)
              * inst.hard_weight)
    return total


# -------------------------------------------------------- greedy building
def _period_options(duration: int, day: int) -> List[int]:
    """0-based periods on `day` that can host a session of `duration`
    minutes (Friday has no P6/P7, so 40-min sessions have fewer options
    there)."""
    return [p for p in range(tg.PERIODS_BY_DAY[day])
            if tg.PERIOD_MINUTES[p] == duration]


def greedy_init(inst: Instance, rng: random.Random,
                traditional: bool = False, mirror: bool = True,
                candidates: int = 40, alt_weight: float = 8.0) -> None:
    """Assign slots to every lesson according to its planned session
    shapes.

    mirror=True (default): sessions are placed in Week-A/Week-B PAIRS at
    the same (day, period), so the two weeks of the cycle are near
    identical by construction (odd/even-stability penalty ~0).  Unpaired
    "extra" sessions (odd counts) are placed singly.

    traditional=True simulates current practice: a strong preference for
    putting a course at the SAME period every day, no week mirroring --
    used as the comparison baseline.
    """
    # cluster occupancy map, maintained while placing: slot -> category
    cluster_occ: Dict[int, Dict[int, str]] = {
        cid: {} for cid in inst.clusters}
    grade_clusters: Dict[int, List[int]] = {}
    for cid_, members in inst.clusters.items():
        grade_clusters.setdefault(inst.students[members[0]].grade,
                                  []).append(cid_)

    # per-(building, room_type) pressure: rooms are fungible within a
    # building-type pool and assigned per session, so the construction
    # must keep demand below the pool's supply at every slot -- a wing's
    # 16 classrooms saturate long before the school's 64 do.
    def room_pressure(les: Lesson, course, slot: int) -> float:
        if course.room_type == "any" or not les.section_ids:
            return 0.0
        bt = inst.sec_bt[les.section_ids[0]]
        k = len(les.section_ids)          # a grade-wide block books ~13
        load = inst.room_demand[slot].get(bt, 0) + k
        cap = inst.room_supply.get(bt, 0)
        if cap == 0:
            return 0.0
        if load > cap:
            # breaching the pool is unsolvable downstream -- treat it on
            # par with a student clash so construction avoids it whenever
            # any alternative slot exists
            return 1000.0 + (load - cap) * 100.0
        if load < cap * 0.75:
            return 0.0
        return (load - cap * 0.75 + 1.0) * 60.0

    def neighbor_same_category(les: Lesson, category: str, slot: int) -> int:
        """Same-category academic lessons adjacent to `slot` (within one
        segment, i.e. lunch resets) for this lesson's cluster."""
        if category not in ("STEM", "HUM"):
            return 0
        if les.cluster_id >= 0:
            occ = cluster_occ[les.cluster_id]
        else:
            # grade-wide lesson: all clusters of the grade carry the same
            # grade-wide marks (grade-wide lessons are placed first), so
            # any one of them is a correct occupancy view
            grade = inst.courses[les.course_id].grade
            members = grade_clusters.get(grade, [])
            occ = cluster_occ[members[0]] if members else {}
        p = tg.SLOT_PERIOD[slot]
        n = 0
        for lo, hi in tg.SEGMENTS:
            if lo <= p <= hi:
                for nb_p in (p - 1, p + 1):
                    if lo <= nb_p <= hi:
                        nb = slot - (p - nb_p)
                        if occ.get(nb) == category:
                            n += 1
        return n

    def score_slot(les: Lesson, course, slot: int,
                   preferred_period: int) -> float:
        s = _hard_conflicts_at(inst, les, slot) * 1000.0
        if any(tg.same_day(slot, x) for x in les.slots):
            s += 3.0
        if traditional and tg.SLOT_PERIOD[slot] != preferred_period:
            s += 50.0
        if not traditional:
            if course.demanding:
                s -= tg.PERIOD_WEIGHT[tg.SLOT_PERIOD[slot]] * 10
            # alternation-aware construction: the published penalty is
            # 10/same-category adjacency and one placement can create two
            # (left+right), so price it near its true marginal cost
            s += alt_weight * neighbor_same_category(les, course.category, slot)
        if course.is_pe and tg.SLOT_PERIOD[slot] in (0, 4):
            s += 5.0
        if not traditional:
            s += room_pressure(les, course, slot)
        return s + rng.random()

    def mark(les: Lesson, category: str, slot: int) -> None:
        # room demand is maintained by _slots_add; here only the
        # cluster-occupancy view (for the alternation heuristic)
        if les.cluster_id >= 0:
            cluster_occ[les.cluster_id][slot] = category
        else:                               # grade-wide: mark the grade
            for cid_ in grade_clusters.get(
                    inst.courses[les.course_id].grade, []):
                cluster_occ[cid_][slot] = category

    def place_single(les: Lesson, course, duration: int,
                     preferred_period: int) -> None:
        best_slot, best_score = None, None
        for _ in range(candidates):
            day = rng.randrange(tg.DAYS_PER_WEEK)
            period = rng.choice(_period_options(duration, day))
            week = rng.randrange(tg.WEEKS)
            slot = tg.slot_index(week, day, period)
            if slot in les.slots:
                continue
            sc = score_slot(les, course, slot, preferred_period)
            if best_score is None or sc < best_score:
                best_slot, best_score = slot, sc
        if best_slot is None:
            same_dur = [s for s in range(tg.NUM_SLOTS)
                        if s not in les.slots
                        and tg.SLOT_MINUTES[s] == duration]
            if same_dur:
                best_slot = min(
                    same_dur,
                    key=lambda s: score_slot(les, course, s,
                                             preferred_period))
        if best_slot is not None:
            _slots_add(inst, les, best_slot)
            mark(les, course.category, best_slot)

    order = sorted(inst.lessons,
                   key=lambda l: (not l.grade_wide,
                                  -inst.courses[l.course_id].minutes))
    for les in order:
        if les.slots:
            continue            # already placed via its sync group
        course = inst.courses[les.course_id]
        plan = les.plan or tg.decompose_minutes(course.minutes)
        preferred_period = rng.randrange(tg.PERIODS_PER_DAY)

        # group plan into (duration -> count)
        counts: Dict[int, int] = {}
        for d in plan:
            counts[d] = counts.get(d, 0) + 1

        for duration in sorted(counts, reverse=True):
            n = counts[duration]
            n_pairs = n // 2 if mirror and not traditional else 0
            n_extra = n - 2 * n_pairs
            # ---- paired sessions: same (day, period) in both weeks ----
            for _ in range(n_pairs):
                best, best_score = None, None
                for _ in range(candidates):
                    day = rng.randrange(tg.DAYS_PER_WEEK)
                    period = rng.choice(_period_options(duration, day))
                    s0 = tg.slot_index(0, day, period)
                    s1 = tg.slot_index(1, day, period)
                    if s0 in les.slots or s1 in les.slots:
                        continue
                    sc = (score_slot(les, course, s0, preferred_period)
                          + score_slot(les, course, s1, preferred_period))
                    if best_score is None or sc < best_score:
                        best, best_score = (s0, s1), sc
                if best is None:        # no pair slot left: place singly
                    n_extra += 2
                    continue
                _slots_add(inst, les, best[0])
                _slots_add(inst, les, best[1])
                mark(les, course.category, best[0])
                mark(les, course.category, best[1])
            # ---- unpaired sessions ------------------------------------
            for _ in range(n_extra):
                place_single(les, course, duration, preferred_period)

    # ---- minute top-up safety net --------------------------------------
    # never drop a session: if a lesson is short of its requirement,
    # place the missing minutes even at the cost of (repairable) conflicts
    for les in inst.lessons:
        course = inst.courses[les.course_id]
        guard = 0
        while les.minutes() < course.minutes and guard < 25:
            guard += 1
            shortage = course.minutes - les.minutes()
            progressed = False
            for duration in tg.decompose_minutes(shortage):
                same_dur = [s for s in range(tg.NUM_SLOTS)
                            if s not in les.slots
                            and tg.SLOT_MINUTES[s] == duration]
                if same_dur:
                    chosen = min(same_dur, key=lambda s: score_slot(
                        les, course, s, -1))
                    _slots_add(inst, les, chosen)
                    mark(les, course.category, chosen)
                    progressed = True
                # if a duration class is exhausted cluster-wide, the next
                # guard round re-decomposes the remaining shortage
            if not progressed:
                break


# ------------------------------------------------------------ repair loop
def _group_conflicts_at(inst: Instance, lesson: Lesson, slot: int) -> int:
    """Hard conflicts of placing a lesson at `slot`, summed over its whole
    sync group (a shared slot set moves every member, so every member's
    teachers and students must be checked; members have disjoint teachers
    and students, so the sum double-counts nothing).  Rooms are added once
    per group via the demand-vs-supply model."""
    return (sum(_hard_conflicts_at(inst, inst.lessons[lid], slot)
                for lid in _expand_sync(inst, lesson))
            + _room_conflicts_group(inst, lesson, slot))


def _student_conflicts_at(inst: Instance, lesson: Lesson, slot: int) -> int:
    """Student double-bookings only -- the one resource that cannot be
    re-bound (a student's presence is fixed)."""
    n = 0
    _, students, _, _ = _lesson_entities(inst, lesson)
    own = set(lesson.section_ids)
    for st_id in students:
        for other in inst.students[st_id].section_ids:
            if other not in own and slot in inst.sections[other].slots:
                n += 1
                break
    return n


def _try_flex_shift(inst: Instance, rng: random.Random,
                    les: Lesson, old: int) -> bool:
    """Endgame move: shift a session to a same-duration slot AND re-bind
    the moved sections' rooms and teachers in one atomic step.

    The plain shift fails when the cluster has free slots but the
    section's teacher is busy at every candidate (rooms are fungible per
    session, so they never block a slot -- only their demand-vs-supply
    invariant must hold).  Teachers are hired on demand, so a fresh hire
    always exists.  The only truly scarce resource is the students' time:
    a candidate slot must be student-clean; everything else is solved
    around it.
    """
    dur = tg.SLOT_MINUTES[old]
    group = [inst.lessons[lid] for lid in _expand_sync(inst, les)]
    group_secs = [inst.sections[sid] for m in group for sid in m.section_ids]
    own = {sec.id for sec in group_secs}

    # busy slots per teacher, excluding the group's own sections
    teach_busy: Dict[int, set] = {}
    for sec in inst.sections:
        if sec.id in own:
            continue
        for s in sec.slots:
            teach_busy.setdefault(sec.teacher_id, set()).add(s)

    # room demand the group would add to the candidate slot
    need: Dict[tuple, int] = {}
    for sec in group_secs:
        bt = inst.sec_bt[sec.id]
        need[bt] = need.get(bt, 0) + 1

    cands = [s for s in range(tg.NUM_SLOTS)
             if s not in les.slots and tg.SLOT_MINUTES[s] == dur]
    rng.shuffle(cands)
    for cand in cands:
        # students are the hard currency: the slot must be free for them
        if sum(_student_conflicts_at(inst, m, cand) for m in group) > 0:
            continue
        # room-pool invariant: demand must stay within supply
        if any(inst.room_demand[cand].get(bt, 0) + k
               > inst.room_supply.get(bt, 0) for bt, k in need.items()):
            continue
        post = (set(les.slots) - {old}) | {cand}
        teach_of: Dict[int, int] = {}
        taken_teachers: set = set()
        for sec in group_secs:
            course = inst.courses[sec.course_id]
            building = inst.rooms[sec.room_id].building
            # ---- teacher: keep, or re-bind, or hire on demand
            tid = sec.teacher_id
            t = inst.teachers[tid]
            if (tid not in taken_teachers
                    and all(t.available(s) for s in post)
                    and not (post & teach_busy.get(tid, set()))):
                teach_of[sec.id] = tid
                taken_teachers.add(tid)
            else:
                found = None
                for t2 in inst.teachers:
                    if (course.subject not in t2.subjects
                            or t2.home_building not in ("", building)
                            or t2.id in taken_teachers):
                        continue
                    if (all(t2.available(s) for s in post)
                            and not (post & teach_busy.get(t2.id, set()))):
                        found = t2.id
                        break
                if found is None:
                    # hire-on-demand: a fresh teacher for exactly these
                    # slots (unavailability sampled only on 40-min slots
                    # the section does not use)
                    from .model import Teacher
                    found = len(inst.teachers)
                    unavailable = {s for s in range(tg.NUM_SLOTS)
                                   if tg.SLOT_MINUTES[s] == 40
                                   and s not in post
                                   and rng.random() < 0.10}
                    idx = sum(1 for x in inst.teachers
                              if course.subject in x.subjects) + 1
                    inst.teachers.append(Teacher(
                        id=found, name=f"T-{course.subject.upper()}-{idx}",
                        subjects={course.subject}, home_building=building,
                        unavailable=unavailable))
                teach_of[sec.id] = found
                taken_teachers.add(found)
        # commit: shift the shared slot set, re-bind teachers
        _slots_discard(inst, les, old)
        _slots_add(inst, les, cand)
        for sec in group_secs:
            sec.teacher_id = teach_of[sec.id]
        inst.build_indexes()
        return True
    return False


def repair(inst: Instance, rng: random.Random,
           max_passes: int = 60, tries: int = 60) -> int:
    """Greedily eliminate residual hard conflicts. Returns remaining count.

    Two move types:
      * shift: move a session to a free same-duration slot;
      * swap-repair: exchange a session with a *cluster-mate* lesson's
        same-duration session.  Same-cluster swaps can never create
        student conflicts (the cluster is busy at both slots either
        way), so they are the safe way to untangle teacher/room clashes
        in a zero-slack grid.  Grade-wide lessons swap among the
        grade-wide blocks of their own grade for the same reason.
    """
    cluster_lessons: Dict[int, List[Lesson]] = {}
    for les in inst.lessons:
        cluster_lessons.setdefault(les.cluster_id, []).append(les)

    def swap_mates(les: Lesson) -> List[Lesson]:
        own_group = set(_expand_sync(inst, les))
        if les.cluster_id >= 0:
            pool = cluster_lessons[les.cluster_id]
        else:                       # grade-wide: same grade's blocks only
            grade = inst.courses[les.course_id].grade
            pool = [m for m in cluster_lessons[-1]
                    if inst.courses[m.course_id].grade == grade]
        return [m for m in pool if m.id not in own_group]

    for _ in range(max_passes):
        moved = False
        for les in inst.lessons:
            for old in [s for s in les.slots
                        if _group_conflicts_at(inst, les, s) > 0]:
                best_slot, best_conf = None, _group_conflicts_at(inst, les, old)
                # exhaustive shift scan over every same-duration slot
                # (the endgame has few conflicts left -- thoroughness
                # beats sampling here)
                cands = [s for s in range(tg.NUM_SLOTS)
                         if s not in les.slots
                         and tg.SLOT_MINUTES[s] == tg.SLOT_MINUTES[old]]
                rng.shuffle(cands)
                for slot in cands:
                    c = _group_conflicts_at(inst, les, slot)
                    if c < best_conf:
                        best_slot, best_conf = slot, c
                        if c == 0:
                            break
                if best_slot is not None:
                    _slots_discard(inst, les, old)
                    _slots_add(inst, les, best_slot)
                    moved = True
                    continue
                # ---- shift + resource-flex ----------------------------
                # the plain shift found nothing because each candidate's
                # blocker was the section's FIXED room or teacher; try
                # shifting with a simultaneous room/teacher re-bind
                if _try_flex_shift(inst, rng, les, old):
                    moved = True
                    continue
                # ---- swap-repair --------------------------------------
                done = False
                mates = swap_mates(les)
                rng.shuffle(mates)
                # fallback pool: every lesson holding a same-duration
                # slot (cross-cluster swaps are evaluated exactly, so
                # they are accepted only when truly improving)
                dur = tg.SLOT_MINUTES[old]
                pool = [m for m in inst.lessons
                        if m.id != les.id and m.slots is not les.slots
                        and any(tg.SLOT_MINUTES[s] == dur
                                and s not in les.slots and s != old
                                for s in m.slots)]
                rng.shuffle(pool)
                candidates = mates + pool[:tries]
                for other in candidates:
                    # `old in other.slots` would silently steal a session
                    # from `other` (sets are idempotent)
                    if (other.id == les.id or old in other.slots
                            or other.slots is les.slots):
                        continue
                    cand = [s for s in other.slots
                            if tg.SLOT_MINUTES[s] == tg.SLOT_MINUTES[old]
                            and s not in les.slots]
                    if not cand:
                        continue
                    rng.shuffle(cand)
                    # exhaustive over the mate's same-duration slots
                    for b in cand:
                        before = (_group_conflicts_at(inst, les, old)
                                  + _group_conflicts_at(inst, other, b))
                        _slots_discard(inst, les, old)
                        _slots_discard(inst, other, b)
                        after = (_group_conflicts_at(inst, les, b)
                                 + _group_conflicts_at(inst, other, old))
                        # same-cluster sideways swaps (after == before)
                        # are allowed: they reshuffle which course owns a
                        # slot and let later passes escape plateaus
                        if after < before or (after == before
                                              and other in mates):
                            _slots_add(inst, les, b)
                            _slots_add(inst, other, old)
                            moved = True
                            done = True
                            break
                        _slots_add(inst, les, old)
                        _slots_add(inst, other, b)
                    if done:
                        break
                if done:
                    continue
                # ---- displacement chain (endgame) ---------------------
                # vacate a same-duration slot by shifting ITS occupant to
                # a third slot, then take it: les: old -> y, occupant: y -> z
                for other in swap_mates(les):
                    if other.slots is les.slots:
                        continue
                    cand = [s for s in other.slots
                            if tg.SLOT_MINUTES[s] == tg.SLOT_MINUTES[old]
                            and s not in les.slots]
                    rng.shuffle(cand)
                    for y in cand:
                        _slots_discard(inst, other, y)   # tentatively vacate
                        conf_here = _group_conflicts_at(inst, les, y)
                        if conf_here >= best_conf:
                            _slots_add(inst, other, y)
                            continue
                        # find z: any same-duration slot the occupant
                        # can take with zero new conflicts
                        zs = [s for s in range(tg.NUM_SLOTS)
                              if s not in other.slots and s != old
                              and tg.SLOT_MINUTES[s] == tg.SLOT_MINUTES[y]]
                        rng.shuffle(zs)
                        placed = None
                        for z in zs:
                            if _group_conflicts_at(inst, other, z) == 0:
                                placed = z
                                break
                        if placed is not None:
                            _slots_discard(inst, les, old)
                            _slots_add(inst, les, y)
                            _slots_add(inst, other, placed)
                            moved = True
                            done = True
                            break
                        _slots_add(inst, other, y)      # undo vacate
                    if done:
                        break
                if done:
                    continue
                # ---- reshape escape (deepest) -------------------------
                if _reshape_escape(inst, rng, les, old):
                    moved = True
        if not moved:
            break
    return hard_violation_count(inst)


# ------------------------------------------------------ reshape escape
def _reshape_escape(inst: Instance, rng: random.Random,
                    les: Lesson, old: int) -> bool:
    """Last-resort move for a session trapped by a 100%-full duration
    class: re-decompose one lesson of the cluster (including `les`
    itself) into a session shape with FEWER sessions of the trapped
    duration, freeing a slot of that duration, then re-place the lesson
    into the cluster's free slots.  Returns True on strict improvement.
    """
    from collections import Counter
    from .planner import decompose_variants
    if les.cluster_id < 0:
        return False
    cid = les.cluster_id
    d = tg.SLOT_MINUTES[old]

    mates = [l for l in inst.lessons if l.cluster_id == cid]

    def cluster_conflicts() -> int:
        # local metric: conflicts counted from each cluster lesson's
        # side; teacher/room/student clashes are symmetric, so the delta
        # of this sum equals the global hard-count delta for moves that
        # only touch this cluster's lessons
        return sum(_group_conflicts_at(inst, m, s)
                   for m in mates for s in m.slots)

    base = cluster_conflicts()

    rest = [m for m in mates if m.id != les.id]
    rng.shuffle(rest)
    order = [les] + rest
    for m in order:
        if not m.slots:
            continue
        cur = Counter(tg.SLOT_MINUTES[s] for s in m.slots)
        if cur[d] == 0:
            continue
        course = inst.courses[m.course_id]
        lp = 3.0 if course.is_pe else (1.5 if course.room_type == "lab"
                                       else 0.0)
        variants = decompose_variants(course.minutes, is_pe=course.is_pe,
                                      long_penalty=lp)[:60]
        rng.shuffle(variants)
        for v in variants:
            alt = {40: v[0], 45: v[1], 55: v[2], 60: v[3]}
            if alt.get(d, 0) >= cur[d]:
                continue                    # must free a d-slot
            saved = set(m.slots)
            for s in list(m.slots):
                _slots_discard(inst, m, s)
            used_by_mates: set = set()
            for o in mates:
                if o.id != m.id:
                    used_by_mates |= o.slots
            free: Dict[int, List[int]] = {}
            for s in range(tg.NUM_SLOTS):
                if s not in used_by_mates:
                    free.setdefault(tg.SLOT_MINUTES[s], []).append(s)
            # grade-wide blocks the cluster attends are not in
            # used_by_mates; they show up as student conflicts in
            # _group_conflicts_at, which the placement below avoids
            ok = True
            placed: set = set()
            for dur in sorted(alt, reverse=True):
                need = alt[dur]
                if need == 0:
                    continue
                pool = [s for s in free.get(dur, []) if s not in placed]
                if len(pool) < need:
                    ok = False
                    break
                # pick the `need` least-conflicting slots for m
                pool.sort(key=lambda s: _group_conflicts_at(inst, m, s))
                chosen = pool[:need]
                if any(_group_conflicts_at(inst, m, s) > 0
                       for s in chosen):
                    ok = False
                    break
                placed.update(chosen)
            if ok and len(placed) == sum(alt.values()):
                for s in placed:
                    _slots_add(inst, m, s)
                new_plan = [dur for dur in (60, 55, 45, 40)
                            for _ in range(alt[dur])]
                if m.id == les.id:
                    # the trapped lesson itself was reshaped: its old
                    # conflicted session is gone with the old shape
                    if cluster_conflicts() < base:
                        m.plan = new_plan
                        return True
                else:
                    # m's reshape freed at least one d-slot: move the
                    # trapped session of `les` into the best free one
                    _slots_discard(inst, les, old)
                    freed = [s for s in free.get(d, [])
                             if s not in placed and s != old]
                    best_s, best_c = None, None
                    for s in freed:
                        c = _group_conflicts_at(inst, les, s)
                        if best_c is None or c < best_c:
                            best_s, best_c = s, c
                    if best_s is not None:
                        _slots_add(inst, les, best_s)
                        if cluster_conflicts() < base:
                            m.plan = new_plan
                            return True
                        _slots_discard(inst, les, best_s)
                    _slots_add(inst, les, old)
            for s in list(m.slots):
                _slots_discard(inst, m, s)
            for s in saved:
                _slots_add(inst, m, s)
    return False


# ------------------------------------------------------ teacher rebinding
def rebind_teachers(inst: Instance, rng: random.Random) -> int:
    """Dissolve teacher-side hard conflicts (double-booking, unavailability)
    by reassigning the affected section to another eligible teacher -- or
    hiring a fresh one, which the school's hire-on-demand rule allows.

    A section's slots are already fixed at this stage, so feasibility of a
    (re)assignment is checked directly: subject match, building loyalty,
    available at every slot, and no overlap with the teacher's other
    sections.  Returns remaining teacher-side conflicts.
    """
    def sec_teacher_conflicts(sec, tid: int) -> int:
        t = inst.teachers[tid]
        n = 0
        for s in sec.slots:
            if not t.available(s):
                n += 1
            for other in inst.teach_secs.get(tid, []):
                if other != sec.id and s in inst.sections[other].slots:
                    n += 1
        return n

    for sec in inst.sections:
        cur = sec_teacher_conflicts(sec, sec.teacher_id)
        if cur == 0:
            continue
        course = inst.courses[sec.course_id]
        building = inst.rooms[sec.room_id].building
        best_tid, best = None, cur
        for t in inst.teachers:
            if course.subject not in t.subjects:
                continue
            if t.home_building not in ("", building):
                continue                    # building loyalty (H10)
            c = sec_teacher_conflicts(sec, t.id)
            if c < best:
                best_tid, best = t.id, c
                if c == 0:
                    break
        if best_tid is None or best > 0:
            # hire a fresh teacher who can work exactly these slots
            tid = len(inst.teachers)
            from .model import Teacher
            unavailable = {s for s in range(tg.NUM_SLOTS)
                           if tg.SLOT_MINUTES[s] == 40
                           and s not in sec.slots
                           and rng.random() < 0.10}
            idx = sum(1 for t in inst.teachers
                      if course.subject in t.subjects) + 1
            inst.teachers.append(Teacher(
                id=tid, name=f"T-{course.subject.upper()}-{idx}",
                subjects={course.subject}, home_building=building,
                unavailable=unavailable))
            best_tid = tid
        sec.teacher_id = best_tid
    return sum(sec_teacher_conflicts(sec, sec.teacher_id)
               for sec in inst.sections)


# --------------------------------------------------------- room rebinding
def rebind_rooms(inst: Instance, rng: random.Random,
                 max_passes: int = 40) -> int:
    """Resolve room double-bookings by moving sections to a different
    same-type room that is free at all of the section's slots.

    Rooms are fungible (same type, same capacity), so rebinding is always
    safe for students and teachers -- it only touches the room index.
    Returns the number of remaining room clashes.
    """
    occ: Dict[int, Dict[int, List[int]]] = {
        r.id: {} for r in inst.rooms}          # room -> slot -> section ids
    for sec in inst.sections:
        for s in sec.slots:
            occ[sec.room_id].setdefault(s, []).append(sec.id)

    def clashes(sec, rid: int, skip: int) -> int:
        return sum(1 for s in sec.slots
                   if any(x != skip for x in occ[rid].get(s, [])))

    by_type: Dict[str, List[int]] = {}
    for r in inst.rooms:
        by_type.setdefault(r.room_type, []).append(r.id)

    for _ in range(max_passes):
        moved = False
        order = list(inst.sections)
        rng.shuffle(order)
        for sec in order:
            cur = clashes(sec, sec.room_id, sec.id)
            if cur == 0:
                continue
            # stay inside the section's current building: teachers are
            # hired building-loyal, so a cross-building rebind would
            # create teacher travel-gap violations (H10)
            building = inst.rooms[sec.room_id].building
            cands = [r for r in by_type[inst.rooms[sec.room_id].room_type]
                     if inst.rooms[r].building == building]
            rng.shuffle(cands)
            best_rid, best = sec.room_id, cur
            for rid in cands:
                if rid == sec.room_id:
                    continue
                c = clashes(sec, rid, sec.id)
                if c < best:
                    best_rid, best = rid, c
                    if c == 0:
                        break
            if best_rid != sec.room_id:
                for s in sec.slots:
                    occ[sec.room_id][s].remove(sec.id)
                    occ[best_rid].setdefault(s, []).append(sec.id)
                sec.room_id = best_rid
                moved = True
        if not moved:
            break
    # sync teach/room indexes afterwards (done by caller via build_indexes)
    return sum(clashes(sec, sec.room_id, sec.id) for sec in inst.sections)


# ------------------------------------------------------- simulated anneal
def anneal(inst: Instance, rng: random.Random, iterations: int = 20000,
           t_start: float = 150.0, t_end: float = 0.1,
           swap_prob: float = 0.25, pair_prob: float = 0.25,
           match_prob: float = 0.15,
           reheat_peaks: Tuple[float, ...] = (),
           log_every: int = 0) -> List[Tuple[int, float]]:
    lessons = inst.lessons
    # slots grouped by duration: every move stays within one duration
    # class, so each lesson's minutes never change (H2 invariant).
    slots_by_duration: Dict[int, List[int]] = {}
    for s in range(tg.NUM_SLOTS):
        slots_by_duration.setdefault(tg.SLOT_MINUTES[s], []).append(s)

    history: List[Tuple[int, float]] = []
    current_total = sum(inst_detail(inst).values())
    best_total = current_total
    best_snapshot = {l.id: set(l.slots) for l in lessons}

    # temperature schedule: monotone cooling, or reheat cycles with
    # decaying peaks (each cycle re-melts just enough to escape the local
    # optimum the previous cycle cooled into; the global best snapshot is
    # kept across cycles, so reheating can only help)
    if reheat_peaks:
        cycle_len = max(1, iterations // len(reheat_peaks))

        def temp_at(it: int) -> float:
            cyc = min(len(reheat_peaks) - 1, it // cycle_len)
            frac = (it - cyc * cycle_len) / max(1, cycle_len - 1)
            peak = reheat_peaks[cyc]
            return peak * (t_end / peak) ** frac
    else:
        def temp_at(it: int) -> float:
            return t_start * (t_end / t_start) ** (it / max(1, iterations - 1))

    def _lesson_key(l: Lesson):
        return (l.cluster_id if l.cluster_id >= 0
                else ("gw", inst.courses[l.course_id].grade))

    hot: List[Lesson] = []        # lessons with hard conflicts (focused search)
    match_pool: Dict[object, set] = {}   # key -> mirrors of unpaired sessions
    for it in range(iterations):
        temp = temp_at(it)
        if it % 128 == 0:         # refresh the conflict focus list
            hot = [l for l in lessons
                   if any(_group_conflicts_at(inst, l, s) > 0 for s in l.slots)]
            match_pool = {}
            for l in lessons:
                pool = match_pool.setdefault(_lesson_key(l), set())
                for s in l.slots:
                    if tg.mirror_slot(s) not in l.slots:
                        pool.add(tg.mirror_slot(s))
        if hot and rng.random() < 0.6:
            les = rng.choice(hot)     # focus on fixing hard violations
        else:
            les = rng.choice(lessons)
        if not les.slots:
            continue

        roll = rng.random()
        if roll < pair_prob:                               # ---- pair move
            # relocate a mirrored Week-A/B pair to another (day, period):
            # optimises within the symmetry-preserving subspace
            paired = [s for s in les.slots
                      if s < tg.WEEK_STRIDE
                      and tg.mirror_slot(s) in les.slots]
            if not paired:
                continue
            old0 = rng.choice(paired)
            old1 = tg.mirror_slot(old0)
            dur = tg.SLOT_MINUTES[old0]
            day = rng.randrange(tg.DAYS_PER_WEEK)
            period = rng.choice(_period_options(dur, day))
            new0 = tg.slot_index(0, day, period)
            new1 = tg.slot_index(1, day, period)
            if new0 in les.slots or new1 in les.slots:
                continue
            ids = _expand_sync(inst, les)
            hint = (old0, old1, new0, new1)
            before = _affected_cost(inst, ids, hint)
            _slots_discard(inst, les, old0)
            _slots_discard(inst, les, old1)
            _slots_add(inst, les, new0)
            _slots_add(inst, les, new1)
            after = _affected_cost(inst, ids, hint)
            delta = after - before
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                current_total += delta
            else:
                _slots_discard(inst, les, new0)
                _slots_discard(inst, les, new1)
                _slots_add(inst, les, old0)
                _slots_add(inst, les, old1)
        elif roll < pair_prob + match_prob:                # ---- mirror-match
            # shift an unpaired session onto the mirror of another unpaired
            # session of the same cluster/grade: pairs two orphans at once,
            # a coordinated move a plain shift almost never finds
            unpaired = [s for s in les.slots
                        if tg.mirror_slot(s) not in les.slots]
            if not unpaired:
                continue
            old = rng.choice(unpaired)
            cands = [t for t in match_pool.get(_lesson_key(les), ())
                     if t not in les.slots
                     and tg.SLOT_MINUTES[t] == tg.SLOT_MINUTES[old]]
            if not cands:
                continue
            new = rng.choice(cands)
            if new == old:
                continue
            ids = _expand_sync(inst, les)
            before = _affected_cost(inst, ids, (old, new))
            _slots_discard(inst, les, old)
            _slots_add(inst, les, new)
            after = _affected_cost(inst, ids, (old, new))
            delta = after - before
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                current_total += delta
            else:
                _slots_discard(inst, les, new)
                _slots_add(inst, les, old)
        elif roll < pair_prob + match_prob + swap_prob:    # ---- swap
            other = rng.choice(lessons)
            if (other.id == les.id or not other.slots
                    or other.slots is les.slots):
                continue
            a = rng.choice(tuple(les.slots))
            b = rng.choice(tuple(other.slots))
            if (a == b or b in les.slots or a in other.slots
                    or tg.SLOT_MINUTES[a] != tg.SLOT_MINUTES[b]):
                continue
            pair = _expand_sync(inst, les) + _expand_sync(inst, other)
            before = _affected_cost(inst, pair, (a, b))
            _slots_discard(inst, les, a)
            _slots_discard(inst, other, b)
            _slots_add(inst, les, b)
            _slots_add(inst, other, a)
            after = _affected_cost(inst, pair, (a, b))
            delta = after - before
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                current_total += delta
            else:
                _slots_discard(inst, les, b)
                _slots_discard(inst, other, a)
                _slots_add(inst, les, a)
                _slots_add(inst, other, b)
        else:                                              # ---- shift
            old = rng.choice(tuple(les.slots))
            new = rng.choice(slots_by_duration[tg.SLOT_MINUTES[old]])
            if new in les.slots:
                continue
            ids = _expand_sync(inst, les)
            before = _affected_cost(inst, ids, (old, new))
            _slots_discard(inst, les, old)
            _slots_add(inst, les, new)
            after = _affected_cost(inst, ids, (old, new))
            delta = after - before
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                current_total += delta
            else:
                _slots_discard(inst, les, new)
                _slots_add(inst, les, old)

        if current_total < best_total:
            best_total = current_total
            best_snapshot = {l.id: set(l.slots) for l in lessons}
        if log_every and (it + 1) % log_every == 0:
            history.append((it + 1, current_total))

    for l in lessons:                          # restore best seen state
        for s in list(l.slots):
            _slots_discard(inst, l, s)
        for s in best_snapshot[l.id]:
            _slots_add(inst, l, s)
    return history


def _snapshot(inst: Instance) -> Dict[int, set]:
    return {l.id: set(l.slots) for l in inst.lessons}


def _restore(inst: Instance, snap: Dict[int, set]) -> None:
    for l in inst.lessons:
        for s in list(l.slots):
            _slots_discard(inst, l, s)
        for s in snap[l.id]:
            _slots_add(inst, l, s)


def _restart_clusters(inst: Instance, rng: random.Random,
                      cluster_ids) -> None:
    """Replan and re-place whole clusters from scratch.

    A trapped session is often a *shape* problem: the cluster's duration
    multiset fits its grid only one way, and that way clashes with a
    shared teacher or a full lab.  Replanning the cluster's session
    shapes (with the same per-duration caps used at build time) changes
    the duration mix and escapes the trap.  Teachers and rooms stay
    assigned; only slots and shapes change.
    """
    from .planner import plan_cluster
    for cid in cluster_ids:
        if cid < 0:
            continue
        les_ids = [l.id for l in inst.lessons if l.cluster_id == cid]
        if not les_ids:
            continue
        specs = [{"minutes": inst.courses[inst.lessons[lid].course_id].minutes,
                  "is_pe": inst.courses[inst.lessons[lid].course_id].is_pe,
                  "long_penalty": 1.5 if inst.courses[
                      inst.lessons[lid].course_id].room_type == "lab" else None}
                 for lid in les_ids]
        plans = plan_cluster(specs, rng,
                             caps=inst.cluster_caps.get(cid))
        for lid, plan in zip(les_ids, plans):
            les = inst.lessons[lid]
            les.plan = plan
            for s in list(les.slots):
                _slots_discard(inst, les, s)
    # greedy_init only places lessons with empty slot sets
    greedy_init(inst, rng)


def _conflicted_clusters(inst: Instance) -> List[int]:
    out = set()
    for les in inst.lessons:
        if les.cluster_id >= 0 and any(
                _group_conflicts_at(inst, les, s) > 0 for s in les.slots):
            out.add(les.cluster_id)
    return sorted(out)


# ------------------------------------------------- per-slot room solving
def solve_rooms_per_slot(inst: Instance, rng: random.Random) -> int:
    """Assign a concrete room to every (section, slot) session.

    Rooms of one (building, room_type) pool are fungible, so per slot the
    problem is trivial: if demand <= supply (the invariant the search
    maintains), greedy assignment succeeds.  Home rooms are kept wherever
    possible; deviations are stored as sparse per-slot overrides
    (Section.slot_rooms).  Returns the number of unhouseable sessions
    (0 whenever the demand invariant held throughout).
    """
    for sec in inst.sections:
        sec.slot_rooms = {}
    by_bt: Dict[tuple, List[int]] = {}
    for r in inst.rooms:
        by_bt.setdefault((r.building, r.room_type), []).append(r.id)
    active: Dict[int, List[int]] = {s: [] for s in range(tg.NUM_SLOTS)}
    for sec in inst.sections:
        for s in sec.slots:
            active[s].append(sec.id)

    unhoused = 0
    for s in range(tg.NUM_SLOTS):
        need: Dict[tuple, List[int]] = {}
        for sid in active[s]:
            need.setdefault(inst.sec_bt[sid], []).append(sid)
        for bt, sids in need.items():
            pool = by_bt.get(bt, [])
            free = set(pool)
            # first pass: keep home rooms (stability of room assignments)
            rest = []
            for sid in sids:
                home = inst.sections[sid].room_id
                if home in free:
                    free.discard(home)
                else:
                    rest.append(sid)
            # second pass: any free room of the pool
            rng.shuffle(rest)
            for sid in rest:
                if not free:
                    unhoused += 1
                    continue                # validator will flag it
                rid = free.pop()
                inst.sections[sid].slot_rooms[s] = rid
    return unhoused


def optimize(inst: Instance, rng: random.Random, iterations: int = 20000,
             log_every: int = 0, max_ils_rounds: int = 0) -> None:
    """greedy -> repair -> rebind -> anneal -> repair, with an optional
    iterated-local-search wrapper (cluster restarts) for stubborn cases."""
    greedy_init(inst, rng)
    repair(inst, rng)
    # teachers are fungible under hire-on-demand: dissolve double-bookings
    # by rebinding sections, then rebuild the indexes
    rebind_rooms(inst, rng)
    rebind_teachers(inst, rng)
    inst.build_indexes()
    repair(inst, rng, max_passes=20)
    anneal(inst, rng, iterations=iterations, log_every=log_every,
           reheat_peaks=(150.0, 40.0, 15.0))
    repair(inst, rng, max_passes=15)
    rebind_rooms(inst, rng)
    rebind_teachers(inst, rng)
    inst.build_indexes()
    repair(inst, rng, max_passes=40)

    from .cost import total_cost
    best_snap = _snapshot(inst)
    best_key = (hard_violation_count(inst), total_cost(inst))
    for _ in range(max_ils_rounds):
        if best_key[0] == 0:
            break
        _restore(inst, best_snap)
        bad = _conflicted_clusters(inst)
        if not bad:
            break
        _restart_clusters(inst, rng, bad)       # replan trapped clusters
        repair(inst, rng, max_passes=30)
        rebind_rooms(inst, rng)
        rebind_teachers(inst, rng)
        inst.build_indexes()
        anneal(inst, rng, iterations=max(1000, iterations // 3),
               t_start=30.0)
        repair(inst, rng, max_passes=30)
        rebind_rooms(inst, rng)
        rebind_teachers(inst, rng)
        inst.build_indexes()
        repair(inst, rng, max_passes=40)
        key = (hard_violation_count(inst), total_cost(inst))
        if key < best_key:
            best_key = key
            best_snap = _snapshot(inst)
    _restore(inst, best_snap)
    # concrete room per session (rooms are fungible per slot; the search
    # kept only the demand-vs-supply invariant)
    inst.build_indexes()
    solve_rooms_per_slot(inst, rng)
