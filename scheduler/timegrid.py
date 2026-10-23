"""Time grid definition for the two-week (odd/even) timetable cycle.

The school runs a 2-week cycle (Week A / Week B), 5 days each.  Monday to
Thursday have 7 periods; **Friday has 5** (no P6/P7 — school rule).
Period lengths are uneven (60/40/40/45/55/40/40 minutes), which is what
makes "minutes accounting" the core of this problem: a course that needs
490 minutes per cycle cannot simply get "one period per day".

Grid capacity: 8 days x 320 min + 2 Fridays x 240 min = 3040 min/cycle.
That is EXACTLY the student minute budget (3040), so full programmes
(G9/G11/G12) fill every teaching slot of the cycle — there is no slack
of any duration class, which the planner and repair ladder are built for.
"""

from typing import Iterable, List

PERIOD_MINUTES = [60, 40, 40, 45, 55, 40, 40]          # P1..P7
PERIOD_STARTS = ["08:30", "09:40", "10:30", "11:20",
                 "13:15", "14:20", "15:10"]

# "Prime learning time" weights shipped with the problem data (modules.xlsx).
# P1 and P5 are the highest-value cognitive slots; we reward putting
# academically demanding courses there (and the PE-timing penalty agrees:
# PE should not occupy P1/P5).
PERIOD_WEIGHT = [0.2469, 0.1396, 0.1396, 0.0757, 0.2469, 0.0757, 0.0757]

# Minimum gap between consecutive periods (minutes).  From the published
# period table every adjacent gap is exactly 10 min (and lunch is 70 min),
# so inter-building travel is always feasible -> Hard constraint 10 is
# structurally satisfied; the validator still checks it explicitly.
MIN_GAP_MINUTES = 10

WEEKS = 2
DAYS_PER_WEEK = 5
PERIODS_PER_DAY = 7                      # maximum (Mon-Thu)
PERIODS_BY_DAY = [7, 7, 7, 7, 5]         # Friday ends after P5
NUM_DAYS = WEEKS * DAYS_PER_WEEK         # 10

WEEK_STRIDE = sum(PERIODS_BY_DAY)        # 33 slots per week
NUM_SLOTS = WEEKS * WEEK_STRIDE          # 66

# absolute day (0..9) -> index of its first slot
_DAY_START = []
_o = 0
for _w in range(WEEKS):
    for _d in range(DAYS_PER_WEEK):
        _DAY_START.append(_o)
        _o += PERIODS_BY_DAY[_d]

CYCLE_MINUTES = (sum(PERIOD_MINUTES[:5]) * 2
                 + sum(PERIOD_MINUTES) * 8)          # 3040
STUDENT_MINUTE_BUDGET = 3040

# Morning segment = P1..P4 (idx 0..3), afternoon = P5..P7 (idx 4..6).
# Consecutiveness for the subject-alternation penalty resets at lunch.
# (On Friday only period idx 4 of the afternoon segment exists.)
SEGMENTS = [(0, 3), (4, 6)]

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def slot_index(week: int, day: int, period: int) -> int:
    """period is 0-based (0 == P1).  Raises for a period the day lacks."""
    if not (0 <= period < PERIODS_BY_DAY[day]):
        raise ValueError(
            f"{DAY_NAMES[day]} has {PERIODS_BY_DAY[day]} periods; "
            f"P{period + 1} does not exist")
    return _DAY_START[week * DAYS_PER_WEEK + day] + period


# slot -> attribute lookup tables (the ONLY way downstream code should
# reason about slot positions; never arithmetic on raw slot ids)
SLOT_WEEK: List[int] = []
SLOT_DAY: List[int] = []
SLOT_PERIOD: List[int] = []
SLOT_ABS_DAY: List[int] = []             # 0..9, week-aware day index
for _w in range(WEEKS):
    for _d in range(DAYS_PER_WEEK):
        for _p in range(PERIODS_BY_DAY[_d]):
            SLOT_WEEK.append(_w)
            SLOT_DAY.append(_d)
            SLOT_PERIOD.append(_p)
            SLOT_ABS_DAY.append(_w * DAYS_PER_WEEK + _d)
SLOT_MINUTES = [PERIOD_MINUTES[p] for p in SLOT_PERIOD]


def slot_label(slot: int) -> str:
    return f"W{'AB'[SLOT_WEEK[slot]]}-{DAY_NAMES[SLOT_DAY[slot]]}-P{SLOT_PERIOD[slot] + 1}"


def same_day(slot_a: int, slot_b: int) -> bool:
    return SLOT_ABS_DAY[slot_a] == SLOT_ABS_DAY[slot_b]


def mirror_slot(slot: int) -> int:
    """The same (day, period) in the other week of the cycle."""
    return slot + WEEK_STRIDE if slot < WEEK_STRIDE else slot - WEEK_STRIDE


def is_adjacent_80_block(slots: Iterable[int]) -> bool:
    """Exactly two consecutive 40-minute periods on one day of the cycle."""
    pair = sorted(slots)
    return (len(pair) == 2
            and all(0 <= s < NUM_SLOTS for s in pair)
            and SLOT_ABS_DAY[pair[0]] == SLOT_ABS_DAY[pair[1]]
            and SLOT_PERIOD[pair[1]] == SLOT_PERIOD[pair[0]] + 1
            and SLOT_MINUTES[pair[0]] == SLOT_MINUTES[pair[1]] == 40)


DURATIONS = sorted(set(PERIOD_MINUTES))            # [40, 45, 55, 60]

# how many slots of each duration exist per week (10/10/10/36 on this grid)
SLOTS_PER_DURATION = {d: sum(1 for m in SLOT_MINUTES if m == d)
                      for d in DURATIONS}


def decompose_minutes(minutes: int) -> List[int]:
    """Exact decomposition of a minute requirement into period durations.

    Coin-change DP over {40,45,55,60}: fewest sessions first, longer
    sessions preferred on ties (less fragmentation).  Every requirement
    in the data sheets is a multiple of 5 and decomposes exactly.
    Raises ValueError if no exact decomposition exists.
    """
    INF = float("inf")
    # dp[m] = (count, prev_minutes, duration_used)
    dp = [(INF, -1, 0)] * (minutes + 1)
    dp[0] = (0, -1, 0)
    for m in range(1, minutes + 1):
        best = (INF, -1, 0)
        for dur in DURATIONS:
            if m - dur >= 0 and dp[m - dur][0] + 1 < best[0]:
                best = (dp[m - dur][0] + 1, m - dur, dur)
            elif (m - dur >= 0 and dp[m - dur][0] + 1 == best[0]
                  and dur > best[2]):
                best = (dp[m - dur][0] + 1, m - dur, dur)
        dp[m] = best
    if dp[minutes][0] == INF:
        raise ValueError(f"{minutes} cannot be decomposed into {DURATIONS}")
    out, m = [], minutes
    while m > 0:
        _, prev, dur = dp[m]
        out.append(dur)
        m = prev
    return sorted(out, reverse=True)
