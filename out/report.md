# Timetable Optimisation Report

- Students: 1000 | Sections: 563 | Teachers: 386 | Rooms: 104
- Hard-constraint violations after optimisation: **0**
- Total soft penalty: 384741.3 -> **190851.2** (-50.4%)

## Baseline vs Optimised

| Metric | Baseline (current-style) | Optimised | Change |
|---|---|---|---|
| Hard violations | 6 | 0 | -6 |
| subject_alternation | 98250.0 | 78690.0 | -19560.0 (-20%) |
| pe_timing | 0.0 | 0.0 | +0.0  |
| subject_time_diversity | 38752.0 | 15099.0 | -23653.0 (-61%) |
| self_study | 6160.0 | 5140.0 | -1020.0 (-17%) |
| workload | 47300.0 | 48400.0 | +1100.0 (+2%) |
| odd_even_stability | 166344.0 | 14576.0 | -151768.0 (-91%) |
| teacher_mobility | 0.0 | 0.0 | +0.0  |
| cognitive_load | 26311.3 | 27106.2 | +794.9 (+3%) |
| same_day_repeat | 1624.0 | 1840.0 | +216.0 (+13%) |
| overshoot | 0.0 | 0.0 | +0.0  |
| student_overload | 0.0 | 0.0 | +0.0  |
| **Total soft penalty** | **384741.3** | **190851.2** | **-193890.1 (-50.4%)** |
| Soft penalty per student | 384.74 | 190.85 | -193.89 |
