"""Time grid definition for the two-week (odd/even) timetable cycle.

The school runs a 2-week cycle (Week A / Week B), 5 days each, 7 periods
per day.  Period lengths are uneven (60/40/40/45/55/40/40 minutes), which
is what makes "minutes accounting" the core of this problem: a course that
needs 490 minutes per cycle cannot simply get "one period per day".

Grid capacity: 10 days x 320 min/day = 3200 min per cycle.
Student budget: 3040 min per cycle (the rest is empty / self-study time).
"""

from typing import List

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
PERIODS_PER_DAY = 7
NUM_DAYS = WEEKS * DAYS_PER_WEEK               # 10
NUM_SLOTS = NUM_DAYS * PERIODS_PER_DAY         # 70

CYCLE_MINUTES = sum(PERIOD_MINUTES) * NUM_DAYS                 # 3200
STUDENT_MINUTE_BUDGET = 3040

# Morning segment = P1..P4 (idx 0..3), afternoon = P5..P7 (idx 4..6).
# Consecutiveness for the subject-alternation penalty resets at lunch.
SEGMENTS = [(0, 3), (4, 6)]

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def slot_index(week: int, day: int, period: int) -> int:
    """period is 0-based (0 == P1)."""
    return (week * DAYS_PER_WEEK + day) * PERIODS_PER_DAY + period


SLOT_WEEK = [s // (DAYS_PER_WEEK * PERIODS_PER_DAY) for s in range(NUM_SLOTS)]
SLOT_DAY = [(s // PERIODS_PER_DAY) % DAYS_PER_WEEK for s in range(NUM_SLOTS)]
SLOT_PERIOD = [s % PERIODS_PER_DAY for s in range(NUM_SLOTS)]
SLOT_MINUTES = [PERIOD_MINUTES[SLOT_PERIOD[s]] for s in range(NUM_SLOTS)]


def slot_label(slot: int) -> str:
    return f"W{'AB'[SLOT_WEEK[slot]]}-{DAY_NAMES[SLOT_DAY[slot]]}-P{SLOT_PERIOD[slot] + 1}"


def same_day(slot_a: int, slot_b: int) -> bool:
    return slot_a // PERIODS_PER_DAY == slot_b // PERIODS_PER_DAY


WEEK_STRIDE = DAYS_PER_WEEK * PERIODS_PER_DAY      # 35


def mirror_slot(slot: int) -> int:
    """The same (day, period) in the other week of the cycle."""
    return slot + WEEK_STRIDE if slot < WEEK_STRIDE else slot - WEEK_STRIDE


DURATIONS = sorted(set(PERIOD_MINUTES))            # [40, 45, 55, 60]


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
