#!/usr/bin/env python3
"""Render README figures from the exported, optimized timetable CSVs.

SVG keeps the figures reproducible with the Python standard library alone.
Run after ``python3 main.py run`` so images match the latest exported schedule.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import textwrap
from collections import defaultdict
from pathlib import Path


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
PERIOD_MINUTES = (60, 40, 40, 45, 55, 40, 40)
PERIODS_BY_DAY = (7, 7, 7, 7, 5)


def label(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: int, y: int, value: str, size: int = 16,
         color: str = "#19344a", weight: int = 400,
         anchor: str = "start") -> str:
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{label(value)}</text>')


def rect(x: int, y: int, width: int, height: int, fill: str,
         stroke: str = "#d7e0e7", radius: int = 0) -> str:
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}" stroke="{stroke}"/>')


def svg(width: int, height: int, parts: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
            '<style>text{font-family:Arial,Helvetica,sans-serif}</style>\n'
            + "\n".join(parts) + "\n</svg>\n")


def course_color(course: str) -> str:
    name = course.lower()
    if any(word in name for word in ("physics", "chemistry", "science", "biology")):
        return "#dcecf7"
    if "math" in name:
        return "#e6e3f7"
    if any(word in name for word in ("english", "chinese", "language")):
        return "#e1f1e8"
    if any(word in name for word in ("history", "humanities")):
        return "#fcebdc"
    if course == "PE":
        return "#f7e1e5"
    if course == "Self-study":
        return "#f1f4f6"
    return "#edf0dc"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def render_sample(rows: list[dict[str, str]], grade: int) -> str:
    students = sorted({r["student"] for r in rows if int(r["grade"]) == grade})
    if not students:
        raise ValueError(f"No Grade {grade} student in timetable_students.csv")
    student = students[0]
    courses = {(r["week"], r["day"], r["period"]): r["course"]
               for r in rows if r["student"] == student}

    width, height = 1450, 1010
    x0, y0, pw, ph = 153, 172, 251, 110
    parts = [rect(0, 0, width, height, "#ffffff", "#ffffff"),
             text(54, 61, f"Grade {grade} · representative student timetable", 30,
                  weight=700),
             text(54, 93, f"{student}  |  optimized two-week cycle shown in one weekly grid", 17,
                  color="#526879"),
             rect(55, 112, 21, 21, "#e9edf0", "#e9edf0", 3),
             text(85, 128, "Gray cells show different Week A / Week B classes", 15,
                  color="#526879")]
    for day_idx, day in enumerate(DAYS):
        x = x0 + day_idx * pw
        parts.append(rect(x, y0 - 37, pw - 5, 33, "#173b54", "#173b54", 4))
        parts.append(text(x + (pw - 5) // 2, y0 - 15, day, 17,
                          color="#ffffff", weight=700, anchor="middle"))

    for period_idx, minutes in enumerate(PERIOD_MINUTES):
        y = y0 + period_idx * ph
        parts.append(text(55, y + 43, f"P{period_idx + 1}", 19, weight=700))
        parts.append(text(55, y + 67, f"{minutes} min", 14, color="#63788a"))
        for day_idx, day in enumerate(DAYS):
            x = x0 + day_idx * pw
            if period_idx >= PERIODS_BY_DAY[day_idx]:
                parts.append(rect(x, y, pw - 5, ph - 5, "#f5f7f8", radius=5))
                parts.append(text(x + (pw - 5) // 2, y + 58, "—", 20,
                                  color="#8fa0ad", anchor="middle"))
                continue
            key = (day, f"P{period_idx + 1}")
            a = courses.get(("A", *key), "Self-study")
            b = courses.get(("B", *key), "Self-study")
            changed = a != b
            parts.append(rect(x, y, pw - 5, ph - 5,
                              "#e9edf0" if changed else course_color(a), radius=5))
            if changed:
                for line_idx, course in enumerate((a, b)):
                    prefix = "A · " if line_idx == 0 else "B · "
                    for wrap_idx, segment in enumerate(textwrap.wrap(course, width=30) or [course]):
                        shown = prefix + segment if wrap_idx == 0 else "    " + segment
                        parts.append(text(x + 13, y + 29 + line_idx * 49 + wrap_idx * 17,
                                          shown, 14, weight=600 if wrap_idx == 0 else 400))
            else:
                segments = textwrap.wrap(a, width=30) or [a]
                for line_idx, segment in enumerate(segments[:3]):
                    parts.append(text(x + 13, y + 43 + line_idx * 18, segment,
                                      15, weight=600 if line_idx == 0 else 400))
    parts.append(text(54, 978, "Source: out/timetable_students.csv · CAS uses the same styling as other courses.",
                      15, color="#63788a"))
    return svg(width, height, parts)


def room_pool(row: dict[str, str]) -> str:
    room, building = row["room"], row["building"]
    if building.startswith("Wing-") and room.startswith("Wing-"):
        return building
    if building == "Science" and room.startswith("LAB-"):
        return "Laboratories"
    if building == "Sports" and room.startswith("PE-"):
        return "PE venues"
    raise ValueError(f"Unrecognized room/building: {room} / {building}")


def shade(fraction: float) -> str:
    low, high = (239, 246, 249), (28, 109, 151)
    fraction = max(0.0, min(1.0, fraction))
    channels = [round(a + (b - a) * fraction) for a, b in zip(low, high)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def render_rooms(rows: list[dict[str, str]], facilities: dict) -> str:
    pools = [(wing, facilities["classrooms_per_wing"])
             for wing in facilities["wings"]]
    pools += [("Laboratories", facilities["labs"]),
              ("PE venues", facilities["pe_venues"])]
    pools = [(name, cap) for name, cap in pools if cap > 0]
    occupancy: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        pool = room_pool(row)
        key = (pool, row["week"], row["day"], row["period"])
        if row["room"] in occupancy[key]:
            raise ValueError(f"Room booked twice in the same slot: {row['room']} {key}")
        occupancy[key].add(row["room"])

    width, height = 1560, 1240
    parts = [rect(0, 0, width, height, "#ffffff", "#ffffff"),
             text(48, 61, "Room-pool utilization · optimized timetable", 31, weight=700),
             text(48, 94, "Occupied rooms / configured rooms in each teaching period; Week A and Week B shown separately.",
                  17, color="#526879")]
    panel_w, panel_h = 744, 351
    cell_w, cell_h = 59, 34
    for index, (pool, capacity) in enumerate(pools):
        px = 48 + (index % 2) * 768
        py = 145 + (index // 2) * 356
        gx, gy = px + 105, py + 73
        parts.append(rect(px, py, panel_w, panel_h, "#fbfcfd", "#d7e0e7", 8))
        peak = max((len(occupancy.get((pool, week, day, f"P{p + 1}"), set()))
                    for week in "AB" for day in DAYS
                    for p in range(PERIODS_BY_DAY[DAYS.index(day)])), default=0)
        parts.append(text(px + 18, py + 30, f"{pool}  ·  {capacity} rooms", 20,
                          weight=700))
        parts.append(text(px + panel_w - 20, py + 30, f"Peak {peak}/{capacity}",
                          15, color="#526879", anchor="end"))
        for col in range(10):
            week, day = "AB"[col // 5], DAYS[col % 5]
            parts.append(text(gx + col * cell_w + 25, gy - 13,
                              f"{week} {day}", 13, color="#526879", anchor="middle"))
            for p in range(7):
                cx, cy = gx + col * cell_w, gy + p * cell_h
                if p >= PERIODS_BY_DAY[col % 5]:
                    parts.append(rect(cx, cy, cell_w - 3, cell_h - 3, "#eff2f4", radius=2))
                    continue
                used = len(occupancy.get((pool, week, day, f"P{p + 1}"), set()))
                if used > capacity:
                    raise ValueError(f"{pool} has {used} occupied rooms, capacity {capacity}")
                fraction = used / capacity
                parts.append(rect(cx, cy, cell_w - 3, cell_h - 3, shade(fraction), radius=2))
                parts.append(text(cx + 27, cy + 21, f"{used}/{capacity}", 11,
                                  color="#ffffff" if fraction >= .62 else "#19344a",
                                  weight=600, anchor="middle"))
        for p in range(7):
            parts.append(text(gx - 17, gy + p * cell_h + 22, f"P{p + 1}", 13,
                              color="#526879", anchor="end"))
    parts.append(text(48, 1230, "Source: out/timetable_teachers.csv and data/school.json · Friday P6–P7 do not exist.",
                      15, color="#63788a"))
    return svg(width, height, parts)


def render(outdir: Path, data_dir: Path, grade: int = 9) -> None:
    metrics = json.loads((outdir / "metrics.json").read_text(encoding="utf-8"))
    violations = metrics.get("hard_constraint_check", {})
    if not violations or any(value != 0 for value in violations.values()):
        raise ValueError("Refusing to illustrate a run without a zero-violation hard-constraint audit")
    facilities = json.loads((data_dir / "school.json").read_text(encoding="utf-8"))["facilities"]
    students = read_csv(outdir / "timetable_students.csv")
    teachers = read_csv(outdir / "timetable_teachers.csv")
    (outdir / "sample_student.svg").write_text(render_sample(students, grade), encoding="utf-8")
    (outdir / "room_utilisation.svg").write_text(render_rooms(teachers, facilities),
                                                  encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--grade", type=int, default=9)
    args = parser.parse_args()
    render(args.out, args.data, args.grade)
    print(f"Wrote {args.out / 'sample_student.svg'} and {args.out / 'room_utilisation.svg'}")


if __name__ == "__main__":
    main()
