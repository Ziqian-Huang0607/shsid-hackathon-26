"""Cost model.

Every entity (student / teacher / room / section) produces a *detail dict*
of named cost components.  Hard-constraint violations are multiplied by
`hard_weight` so they dominate any soft penalty; soft components implement
the school's published penalty list:

  1. subject_alternation   - consecutive same-category (STEM/HUM) blocks,
                             consecutiveness resets at lunch (P4->P5).
  2. pe_timing             - PE scheduled in P1 or P5.
  3. subject_time_diversity- same subject at the same period >2x per week.
  4. self_study            - free period first in the morning, or an
                             isolated ("fragmented") free period.
  5. workload              - >=4 demanding classes in a day, on >2 days
                             of the same week.
  6. odd_even_stability    - Week-A vs Week-B pattern changes >3, excluding
                             positions occupied by mandatory CAS in either week.
  7. teacher_mobility      - teacher switches buildings >=2 times in a day.

Plus two modelling extras: same_day_repeat (two sessions of one course on
the same day), overshoot (minutes beyond the requirement waste student
budget), and prime_time_reward (negative cost for demanding courses placed
in the high-weight periods P1/P5 from the provided weight table).
"""

from __future__ import annotations

from typing import Dict

from . import timegrid as tg
from .model import Instance

PE_BAD_PERIODS = (0, 4)          # P1 and P5 (0-based)
ALTERNATION_CATS = ("STEM", "HUM")


# ---------------------------------------------------------------- section
def section_detail(inst: Instance, sid: int) -> Dict[str, float]:
    sec = inst.sections[sid]
    course = inst.courses[sec.course_id]
    w = inst.weights
    d: Dict[str, float] = {}

    got = sec.minutes()
    if got < course.minutes:                      # Hard constraint 2
        missing_sessions = -(-(course.minutes - got) // 40)
        d["hard_minutes_shortfall"] = missing_sessions * inst.hard_weight
    if course.subject == "cas" and not tg.is_adjacent_80_block(sec.slots):
        d["hard_cas_double_block"] = inst.hard_weight
    over = got - course.minutes
    if over > 0:
        d["overshoot"] = over * w["overshoot_per_minute"]

    if course.is_pe:
        bad = sum(1 for s in sec.slots if tg.SLOT_PERIOD[s] in PE_BAD_PERIODS)
        if bad:
            d["pe_timing"] = bad * w["pe_timing"]

    days: Dict[int, int] = {}
    for s in sec.slots:
        key = tg.SLOT_ABS_DAY[s]
        days[key] = days.get(key, 0) + 1
    extra = sum(v - 1 for v in days.values() if v > 1)
    if extra:
        d["same_day_repeat"] = extra * w["same_day_repeat"]
    return d


# ---------------------------------------------------------------- student
def student_detail(inst: Instance, st_id: int) -> Dict[str, float]:
    st = inst.students[st_id]
    w = inst.weights
    d: Dict[str, float] = {}

    # occupation map: slot -> section (conflicts detected on collision)
    occ: Dict[int, int] = {}
    minutes = 0
    conflicts = 0
    for sid in st.section_ids:
        for sl in inst.sections[sid].slots:
            minutes += tg.SLOT_MINUTES[sl]
            if sl in occ:
                conflicts += 1                  # Hard constraint 3
            else:
                occ[sl] = sid
    if conflicts:
        d["hard_student_conflict"] = conflicts * inst.hard_weight
    if minutes > tg.STUDENT_MINUTE_BUDGET:
        d["student_overload"] = (minutes - tg.STUDENT_MINUTE_BUDGET) * w["overshoot_per_minute"]

    # per-day sequences ------------------------------------------------
    # day_slot[d] = list of (period, course) sorted by period
    day_seq = [[] for _ in range(tg.NUM_DAYS)]
    week_period_course: Dict[tuple, int] = {}
    for sl, sid in occ.items():
        c = inst.courses[inst.sections[sid].course_id]
        day = tg.SLOT_ABS_DAY[sl]
        day_seq[day].append((tg.SLOT_PERIOD[sl], c))
        week_period_course[(tg.SLOT_WEEK[sl], tg.SLOT_DAY[sl],
                            tg.SLOT_PERIOD[sl])] = c.id

    # 1. subject alternation + 4. self-study + 5. workload + prime time
    #    + 8. cognitive load (difficulty-aware, low priority)
    wl = inst.cfg["workload"]
    en = inst.cfg.get("energy", {})
    hard_adj_min = en.get("hard_adjacency_min_difficulty", 4.0)
    overload_factor = en.get("day_overload_factor", 1.25)
    w_cog = w.get("cognitive_load", 2.0)
    day_energy = [0.0] * tg.NUM_DAYS
    for day in range(tg.NUM_DAYS):
        seq = sorted(day_seq[day])
        if not seq:
            continue
        periods = [p for p, _ in seq]
        # alternation within each segment (lunch resets)
        for lo, hi in tg.SEGMENTS:
            seg = [(p, c) for p, c in seq if lo <= p <= hi]
            for (p1, c1), (p2, c2) in zip(seg, seg[1:]):
                if (p2 == p1 + 1 and c1.category == c2.category
                        and c1.category in ALTERNATION_CATS):
                    d["subject_alternation"] = (d.get("subject_alternation", 0)
                                                + w["subject_alternation"])
                # cognitive load: two brain-burning classes back to back
                if (p2 == p1 + 1 and c1.difficulty >= hard_adj_min
                        and c2.difficulty >= hard_adj_min):
                    d["cognitive_load"] = d.get("cognitive_load", 0) + w_cog
        # self-study: free P1, or isolated free period inside a segment
        occupied = set(periods)
        if 0 not in occupied:
            d["self_study"] = d.get("self_study", 0) + w["self_study"]
        for lo, hi in tg.SEGMENTS:
            for p in range(lo + 1, hi):
                if p not in occupied and (p - 1) in occupied and (p + 1) in occupied:
                    d["self_study"] = d.get("self_study", 0) + w["self_study"]
        # workload: demanding-class count for the day
        dem = sum(1 for _, c in seq if c.demanding)
        day_seq[day] = seq
        if dem >= wl["demanding_per_day_threshold"]:
            week = day // tg.DAYS_PER_WEEK
            key = f"_heavy_w{week}"
            d[key] = d.get(key, 0) + 1
        # cognitive energy spent this day (difficulty x minutes)
        day_energy[day] = sum(c.difficulty * tg.PERIOD_MINUTES[p]
                              for p, c in seq)
        # prime-time reward, scaled by difficulty: burning courses belong
        # in the high-weight periods (P1/P5), light ones anywhere else
        for p, c in seq:
            if c.difficulty >= 3.0:
                d["prime_time_reward"] = (d.get("prime_time_reward", 0)
                                          - w["prime_time_reward"]
                                          * (c.difficulty / 5.0)
                                          * tg.PERIOD_WEIGHT[p]
                                          * tg.PERIOD_MINUTES[p] / 60.0)
    for week in range(tg.WEEKS):
        heavy = d.pop(f"_heavy_w{week}", 0)
        thr = wl["heavy_days_per_week_threshold"]
        if heavy > thr:
            d["workload"] = d.get("workload", 0) + (heavy - thr) * w["workload"]
        # brutal-day penalty: a day whose cognitive energy exceeds
        # overload_factor x the week's average drains the student
        es = day_energy[week * tg.DAYS_PER_WEEK:(week + 1) * tg.DAYS_PER_WEEK]
        active = [e for e in es if e > 0]
        if active:
            mean = sum(active) / len(active)
            for e in active:
                if e > mean * overload_factor:
                    d["cognitive_load"] = (d.get("cognitive_load", 0)
                                           + (e - mean * overload_factor)
                                           / 100.0 * w_cog)

    # 3. subject time diversity (per week, per period)
    dv = inst.cfg["diversity"]["max_same_period_per_week"]
    counts: Dict[tuple, int] = {}
    for (week, _day, period), cid in week_period_course.items():
        key = (week, cid, period)
        counts[key] = counts.get(key, 0) + 1
    for v in counts.values():
        if v > dv:
            d["subject_time_diversity"] = (d.get("subject_time_diversity", 0)
                                           + (v - dv) * w["subject_time_diversity"])

    # 6. odd/even stability
    max_changes = inst.cfg["odd_even"]["max_changes_per_week"]
    changes = 0
    for day in range(tg.DAYS_PER_WEEK):
        for p in range(tg.PERIODS_BY_DAY[day]):   # Friday ends at P5
            a = week_period_course.get((0, day, p))
            b = week_period_course.get((1, day, p))
            # CAS happens only once in the two-week cycle.  Its two slots
            # necessarily differ across weeks and must not consume the
            # allowance intended for avoidable timetable changes.
            if ((a is not None and inst.courses[a].subject == "cas")
                    or (b is not None and inst.courses[b].subject == "cas")):
                continue
            if a != b:
                changes += 1
    if changes > max_changes:
        d["odd_even_stability"] = (changes - max_changes) * w["odd_even_stability"]
    return d


# ---------------------------------------------------------------- teacher
def teacher_detail(inst: Instance, tid: int) -> Dict[str, float]:
    t = inst.teachers[tid]
    w = inst.weights
    d: Dict[str, float] = {}
    occ: Dict[int, int] = {}
    conflicts = 0
    unavailable = 0
    for sid in inst.teach_secs.get(tid, []):
        for sl in inst.sections[sid].slots:
            if sl in occ:
                conflicts += 1                  # Hard constraint 3
            else:
                occ[sl] = sid
            if not t.available(sl):
                unavailable += 1                # Hard constraint 5
    if conflicts:
        d["hard_teacher_conflict"] = conflicts * inst.hard_weight
    if unavailable:
        d["hard_teacher_unavailable"] = unavailable * inst.hard_weight

    # 7. mobility: building switches within a day
    max_moves = inst.cfg["teacher_mobility"]["max_building_moves_per_day"]
    day_buildings: Dict[int, list] = {}
    for sl in occ:
        room = inst.rooms[inst.sections[occ[sl]].room_at(sl)]
        day_buildings.setdefault(tg.SLOT_ABS_DAY[sl], []).append(
            (tg.SLOT_PERIOD[sl], room.building))
    for day, items in day_buildings.items():
        items.sort()
        moves = sum(1 for (_, b1), (_, b2) in zip(items, items[1:]) if b1 != b2)
        if moves > max_moves:
            d["teacher_mobility"] = (d.get("teacher_mobility", 0)
                                     + (moves - max_moves) * w["teacher_mobility"])
    return d


# -------------------------------------------------------------------- room
def room_demand_detail(inst: Instance) -> Dict[str, float]:
    """Hard room conflicts in per-session semantics: rooms of the same
    (building, type) are fungible, so the only true infeasibility is
    demand exceeding supply at a slot (the final per-slot room solver
    resolves everything else).  Computed fresh from section slots."""
    demand: Dict[tuple, int] = {}
    for sec in inst.sections:
        room = inst.rooms[sec.room_id]
        bt = (room.building, room.room_type)
        for sl in sec.slots:
            demand[(sl, bt)] = demand.get((sl, bt), 0) + 1
    supply: Dict[tuple, int] = {}
    for r in inst.rooms:
        bt = (r.building, r.room_type)
        supply[bt] = supply.get(bt, 0) + 1
    excess = sum(max(0, d - supply[bt]) for (sl, bt), d in demand.items())
    if excess:                                  # Hard constraint 3
        return {"hard_room_conflict": excess * inst.hard_weight}
    return {}


# ------------------------------------------------------------------ totals
def total_cost(inst: Instance) -> float:
    return sum(inst_detail(inst).values())


def inst_detail(inst: Instance) -> Dict[str, float]:
    out: Dict[str, float] = {}

    def merge(d: Dict[str, float]) -> None:
        for k, v in d.items():
            out[k] = out.get(k, 0.0) + v

    for sec in inst.sections:
        merge(section_detail(inst, sec.id))
    for st in inst.students:
        merge(student_detail(inst, st.id))
    for t in inst.teachers:
        merge(teacher_detail(inst, t.id))
    merge(room_demand_detail(inst))
    return out


def hard_violation_count(inst: Instance) -> int:
    """Number of raw hard violations (unweighted)."""
    n = 0
    for sec in inst.sections:
        course = inst.courses[sec.course_id]
        if sec.minutes() < course.minutes:
            n += 1
        if course.subject == "cas" and not tg.is_adjacent_80_block(sec.slots):
            n += 1
    for st in inst.students:
        seen = set()
        for sid in st.section_ids:
            for sl in inst.sections[sid].slots:
                if sl in seen:
                    n += 1
                seen.add(sl)
    for t in inst.teachers:
        seen = set()
        for sid in inst.teach_secs.get(t.id, []):
            for sl in inst.sections[sid].slots:
                if sl in seen:
                    n += 1
                seen.add(sl)
                if not t.available(sl):
                    n += 1
    # rooms: per-session fungibility -- only demand > supply counts
    d = room_demand_detail(inst)
    n += int(d.get("hard_room_conflict", 0) / inst.hard_weight)
    return n
