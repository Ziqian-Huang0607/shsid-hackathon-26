"""Session-shape planner: decides, per lesson, how its required minutes
split into period durations {60, 55, 45, 40} BEFORE any slot assignment.

Why this matters (the subtle core of the problem):

  * Duration is bound to period: 60-min sessions can only live in P1,
    55-min in P5, 45-min in P4, and 40-min sessions have P2/P3/P6/P7.
  * A cohort cluster's programme totals ~3040 of the 3200 grid minutes,
    i.e. the cluster must use nearly EVERY P1/P4/P5 slot of the cycle:
        10x60 + 10x55 + 10x45 + 36x40 = 3040   (G9 / G12 programmes)
  * But the diversity penalty forbids one course sitting at the same
    period more than twice a week, so the scarce long durations must be
    *shared* across the cluster's lessons (soft cap ~4 per lesson).
  * PE must avoid P1/P5 entirely, so PE lessons get 40/45-min shapes only.

The planner enumerates per-lesson duration-multiset variants, then
hill-climbs the cluster-level combination so per-cluster caps
{60:10, 55:10, 45:10, 40:40} are respected.
"""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple

# per-cluster slot caps (a cluster can occupy each period at most
# once/day).  The 60/45/40 caps deliberately leave ONE slot of slack in
# every duration class the cluster uses: without a free slot of the
# right duration, the repair loop has nothing to move a trapped session
# into (G9/G12 run at 95% grid occupancy, so slack is precious).
CLUSTER_CAPS = {60: 9, 55: 10, 45: 9, 40: 39}
# soft per-lesson caps: >2 sessions/week at the same period triggers the
# diversity penalty, so >4 per cycle is discouraged
SOFT_CAP = {60: 4, 55: 4, 45: 4, 40: 12}
# preference: 40-min sessions are plentiful and flexible; long sessions
# are scarce and period-bound
SHAPE_PREF = {40: 1.0, 45: 1.1, 55: 1.3, 60: 1.3}

Variant = Tuple[int, int, int, int]   # (n40, n45, n55, n60)


def decompose_variants(minutes: int, is_pe: bool = False,
                       long_penalty: float = None,
                       max_count: int = 26,
                       max_variants: int = 300) -> List[Variant]:
    """All exact duration multisets summing to `minutes` (trimmed).

    PE lessons may still use 60/55-min (P1/P5) shapes -- the pe_timing
    penalty is *soft*, and gym capacity at P4 is finite -- but those
    variants are priced high so they are only chosen when necessary.
    Lab courses get a moderate `long_penalty`: lab supply is finite and
    every 60-min session can only live in P1, so lab plans should prefer
    40/45-min shapes and use long sessions sparingly.
    """
    out: List[Variant] = []
    for n60 in range(minutes // 60 + 1):
        for n55 in range((minutes - 60 * n60) // 55 + 1):
            rem45 = minutes - 60 * n60 - 55 * n55
            for n45 in range(rem45 // 45 + 1):
                rem = rem45 - 45 * n45
                if rem % 40 == 0:
                    n40 = rem // 40
                    if 0 < n40 + n45 + n55 + n60 <= max_count:
                        out.append((n40, n45, n55, n60))
    if not out:
        raise ValueError(f"no exact decomposition for {minutes} min")
    if long_penalty is None:
        long_penalty = 3.0 if is_pe else 0.0
    out.sort(key=lambda v: _pref(v) + long_penalty * (v[2] + v[3]))
    if len(out) > max_variants:          # keep the best-shaped ones
        out = out[:max_variants]
    return out


def _pref(v: Variant) -> float:
    n40, n45, n55, n60 = v
    score = (n40 * SHAPE_PREF[40] + n45 * SHAPE_PREF[45]
             + n55 * SHAPE_PREF[55] + n60 * SHAPE_PREF[60])
    for n, d in ((n40, 40), (n45, 45), (n55, 55), (n60, 60)):
        if n > SOFT_CAP[d]:
            score += (n - SOFT_CAP[d]) * 3.0
    # odd counts cannot be mirrored across Week A/B (odd/even penalty),
    # so even session counts are preferred
    score += sum(0.8 for n in v if n % 2 == 1)
    return score


def plan_cluster(lesson_specs: Sequence[Dict], rng: random.Random,
                 rounds: int = 4000,
                 caps: Dict[int, int] = None) -> List[List[int]]:
    """Hill-climb a duration plan for every lesson of one cluster.

    lesson_specs: [{"minutes": int, "is_pe": bool,
                    "long_penalty": float (optional)}, ...]
    caps: per-cluster per-duration slot caps (defaults to CLUSTER_CAPS;
          callers reduce them by whatever grade-wide blocks occupy).
    returns: list of duration lists, one per lesson.
    """
    caps = caps or CLUSTER_CAPS
    long_pen = [s["long_penalty"] if s.get("long_penalty") is not None
                else (3.0 if s.get("is_pe") else 0.0)
                for s in lesson_specs]
    variants = [decompose_variants(s["minutes"], s.get("is_pe", False),
                                   long_penalty=lp)
                for s, lp in zip(lesson_specs, long_pen)]

    def vpref(li: int, vi: int) -> float:
        v = variants[li][vi]
        return _pref(v) + long_pen[li] * (v[2] + v[3])

    choice = [0] * len(lesson_specs)     # start at best-shaped variant

    def used_counts(sel: List[int]) -> Dict[int, int]:
        used = {40: 0, 45: 0, 55: 0, 60: 0}
        for li, vi in enumerate(sel):
            v = variants[li][vi]
            used[40] += v[0]; used[45] += v[1]
            used[55] += v[2]; used[60] += v[3]
        return used

    def cost(sel: List[int]) -> float:
        c = sum(vpref(li, vi) for li, vi in enumerate(sel))
        for d, cap in caps.items():
            over = used_counts(sel)[d] - cap
            if over > 0:
                c += over * 1000.0
        return c

    best_sel, best_cost = list(choice), cost(choice)
    cur_sel, cur_cost = list(choice), best_cost
    for _ in range(rounds):
        li = rng.randrange(len(lesson_specs))
        vi = rng.randrange(len(variants[li]))
        if vi == cur_sel[li]:
            continue
        old = cur_sel[li]
        cur_sel[li] = vi
        c = cost(cur_sel)
        if c <= cur_cost:
            cur_cost = c
            if c < best_cost:
                best_sel, best_cost = list(cur_sel), c
        else:
            cur_sel[li] = old
        if best_cost < 1000:             # all cluster caps satisfied
            break
    plans = []
    for li, vi in enumerate(best_sel):
        n40, n45, n55, n60 = variants[li][vi]
        plans.append([60] * n60 + [55] * n55 + [45] * n45 + [40] * n40)
    return plans
