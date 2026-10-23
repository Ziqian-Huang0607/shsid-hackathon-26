"""Instance builder: curriculum.json + school.json -> concrete instance.

Pipeline:
  1. generate students per grade with enrolments faithful to the grade
     programmes (fixed components + one option per choice module),
     honouring two school rules:
       * IB-track students in G11/G12 may skip PE (`ib_exempt`),
       * G11/G12 students may take no Optional course (`skippable`);
  2. consolidate undersubscribed signatures (only within the same
     grade/IB/optional track, so exemptions are never lost);
  3. cluster students by signature into cohort blocks of <= capacity;
  4. GRADE-WIDE lessons: PE and Optional are attended by the whole grade
     at the same time (one lesson per grade, sections of <= capacity;
     option variants of one module share the same slot set);
  5. session-shape planning per cluster, with cluster caps reduced by
     whatever the grade-wide blocks occupy;
  6. duration-aware teacher/room assignment (teachers hired on demand --
     no assumption about the total number of teachers).

Scheduling lessons instead of individual sections keeps student
double-booking impossible by construction.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import timegrid as tg
from .model import (Course, Instance, Lesson, Room, Section, Student,
                    Teacher)
from .timegrid import SLOT_MINUTES

# per-duration session-load caps (60-min sessions can only live in P1,
# 55 only in P5, 45 only in P4 -- 10 slots each per cycle).  Teacher caps
# stay well below the 10-slot period capacity: a teacher's sessions must
# additionally fit into the *free* slots of several independent clusters,
# so tight caps buy the slack that keeps placement deadlock-free.
TEACHER_DUR_CAP = {60: 5, 55: 4, 45: 4, 40: 22}
ROOM_DUR_CAP = {60: 9, 55: 9, 45: 9, 40: 36}


def load_config(data_dir: str | Path) -> Tuple[Dict, Dict]:
    data_dir = Path(data_dir)
    curriculum = json.loads((data_dir / "curriculum.json").read_text())
    school = json.loads((data_dir / "school.json").read_text())
    return curriculum, school


def build_instance(students_per_grade: int, seed: int,
                   data_dir: str | Path, min_cluster: int = 8) -> Instance:
    rng = random.Random(seed)
    curriculum, school = load_config(data_dir)
    cap = school["section_capacity"]
    ib_frac = school.get("ib_pe_exempt_fraction", 0.3)
    opt_frac = school.get("optional_skip_fraction", 0.3)

    courses: List[Course] = []
    students: List[Student] = []
    teachers: List[Teacher] = []
    rooms: List[Room] = []
    sections: List[Section] = []
    lessons: List[Lesson] = []
    clusters: Dict[int, List[int]] = {}
    sync_groups: Dict[int, List[int]] = {}

    # ---- facilities: one wing per grade, shared labs/venues/studios ----
    fac = school["facilities"]
    wings = fac["wings"]

    def add_room(name: str, building: str, rtype: str) -> None:
        rooms.append(Room(id=len(rooms), name=name, building=building,
                          room_type=rtype, capacity=cap))

    for wing in wings:
        for i in range(fac["classrooms_per_wing"]):
            add_room(f"{wing}-{i + 1:02d}", wing, "classroom")
    for i in range(fac["labs"]):
        add_room(f"LAB-{i + 1:02d}", "Science", "lab")
    for i in range(fac["pe_venues"]):
        add_room(f"PE-{i + 1:02d}", "Sports", "gym")
    for i in range(fac["studios"]):
        add_room(f"ART-{i + 1:02d}", "Arts", "studio")

    grade_wing = {9: wings[0], 10: wings[1], 11: wings[2], 12: wings[3]}

    # ---- courses + students -------------------------------------------
    course_by_key: Dict[Tuple[int, str], int] = {}

    def get_course(grade: int, spec: Dict, component: str) -> int:
        key = (grade, spec["name"])
        if key not in course_by_key:
            course_by_key[key] = len(courses)
            courses.append(Course(
                id=len(courses), grade=grade, name=spec["name"],
                subject=spec["subject"], minutes=spec["minutes"],
                category=spec["category"], demanding=spec["demanding"],
                difficulty=spec.get("difficulty",
                                    3.0 if spec.get("academic", True) else 1.0),
                room_type=spec["room_type"],
                is_pe=spec.get("is_pe", False),
                academic=spec.get("academic", True),
                component=component))
        return course_by_key[key]

    # course_id -> (component key, grade_wide, skippable/ib_exempt flags)
    course_component: Dict[int, str] = {}
    component_flags: Dict[Tuple[int, str], Dict] = {}

    for grade_str, prog in curriculum["grades"].items():
        grade = int(grade_str)
        for i in range(students_per_grade):
            ib = grade in (11, 12) and rng.random() < ib_frac
            st = Student(id=len(students), grade=grade,
                         name=f"G{grade}-S{i + 1:03d}", ib=ib)
            for comp in prog["components"]:
                if comp["type"] == "fixed":
                    comp_key = comp["name"]
                    component_flags[(grade, comp_key)] = comp
                    if comp.get("ib_exempt") and ib:
                        continue                    # IB students skip PE
                    cid = get_course(grade, comp, comp_key)
                    course_component[cid] = comp_key
                    st.course_ids.append(cid)
                else:                               # choice module
                    comp_key = comp["module"]
                    component_flags[(grade, comp_key)] = comp
                    if comp.get("skippable") and rng.random() < opt_frac:
                        continue                    # takes no Optional
                    opt = rng.choice(comp["options"])
                    cid = get_course(grade, opt, comp_key)
                    course_component[cid] = comp_key
                    st.course_ids.append(cid)
            students.append(st)

    # ---- consolidation of undersubscribed signatures ------------------
    # Only merge within the same track (grade, IB, optional-taker) so an
    # exemption is never consolidated away.
    def signature(st: Student) -> Tuple[int, ...]:
        return tuple(sorted(st.course_ids))

    def track(st: Student) -> Tuple:
        takes_optional = any(
            component_flags[(st.grade, course_component[c])].get("skippable")
            for c in st.course_ids)
        return (st.grade, st.ib, takes_optional)

    by_track: Dict[Tuple, List[int]] = defaultdict(list)
    for st in students:
        by_track[track(st)].append(st.id)

    for tkey, ids in by_track.items():
        groups: Dict[Tuple[int, ...], List[int]] = defaultdict(list)
        for sid in ids:
            groups[signature(students[sid])].append(sid)
        if not groups:
            continue
        big = max(groups.items(), key=lambda kv: len(kv[1]))[0]
        for sig, members in groups.items():
            if sig == big or len(members) >= min_cluster:
                continue
            for sid in members:
                students[sid].course_ids = list(big)

    # ---- cohort clusters (capped at section capacity) ------------------
    for tkey, ids in sorted(by_track.items()):
        groups: Dict[Tuple[int, ...], List[int]] = defaultdict(list)
        for sid in ids:
            groups[signature(students[sid])].append(sid)
        for members in groups.values():
            members = sorted(members)
            for i in range(0, len(members), cap):
                clusters[len(clusters)] = members[i:i + cap]

    cluster_of: Dict[int, int] = {}
    for cid_, members in clusters.items():
        for st_id in members:
            cluster_of[st_id] = cid_

    # ---- lessons -------------------------------------------------------
    # grade-wide components: one lesson per (grade, component, option);
    # option variants of the same component share one slot set (synced).
    gradewide_done: Dict[Tuple[int, str], List[int]] = defaultdict(list)

    def make_sections(lesson: Lesson, members: List[int],
                      course: Course) -> None:
        chunks = [members[i:i + cap] for i in range(0, len(members), cap)]
        for chunk in chunks:
            sec = Section(id=len(sections), course_id=course.id,
                          teacher_id=-1, room_id=-1,
                          students=chunk, slots=lesson.slots)
            sections.append(sec)
            lesson.section_ids.append(sec.id)
            for st_id in chunk:
                students[st_id].section_ids.append(sec.id)

    # grade-wide lessons first (they constrain every cluster)
    grade_students: Dict[int, List[int]] = defaultdict(list)
    for st in students:
        grade_students[st.grade].append(st.id)

    for grade, ids in sorted(grade_students.items()):
        comp_members: Dict[Tuple[str, int], List[int]] = defaultdict(list)
        for sid in ids:
            for cid_ in students[sid].course_ids:
                comp = component_flags[(grade, course_component[cid_])]
                if comp.get("grade_wide"):
                    comp_members[(course_component[cid_], cid_)].append(sid)
        sync_slots: Dict[str, set] = {}
        sync_gid: Dict[str, int] = {}
        for (comp_key, cid_), members in sorted(comp_members.items()):
            lesson = Lesson(id=len(lessons), cluster_id=-1,
                            course_id=cid_, grade_wide=True)
            if comp_key in sync_slots:          # option variant: sync
                lesson.slots = sync_slots[comp_key]
                lesson.sync_group = sync_gid[comp_key]
                sync_groups[lesson.sync_group].append(lesson.id)
            else:
                sync_gid[comp_key] = len(sync_groups)
                lesson.sync_group = sync_gid[comp_key]
                sync_groups[lesson.sync_group] = [lesson.id]
                sync_slots[comp_key] = lesson.slots
            make_sections(lesson, members, courses[cid_])
            lessons.append(lesson)
            gradewide_done[(grade, comp_key)].append(lesson.id)

    # cluster lessons for everything else
    for cid_, members in clusters.items():
        grade = students[members[0]].grade
        program = sorted({c for st in members for c in students[st].course_ids})
        for course_id in program:
            comp = component_flags[(grade, course_component[course_id])]
            if comp.get("grade_wide"):
                continue                          # already scheduled grade-wide
            lesson = Lesson(id=len(lessons), cluster_id=cid_,
                            course_id=course_id)
            make_sections(lesson, members, courses[course_id])
            lessons.append(lesson)

    # ---- session-shape planning ----------------------------------------
    from .planner import CLUSTER_CAPS, decompose_variants, plan_cluster

    # grade-wide lessons: best-shaped exact decomposition (PE avoids
    # P1/P5 shapes unless unavoidable)
    gradewide_usage: Dict[int, Dict[int, int]] = {}
    for (grade, comp_key), lids in gradewide_done.items():
        les = lessons[lids[0]]
        course = courses[les.course_id]
        variants = decompose_variants(course.minutes,
                                      is_pe=course.is_pe)
        les.plan = _variant_to_plan(variants[0])
        for lid in lids[1:]:
            lessons[lid].plan = list(les.plan)
        usage = gradewide_usage.setdefault(grade, {40: 0, 45: 0, 55: 0, 60: 0})
        for d in les.plan:
            usage[d] += 1

    # cluster plans with caps reduced by grade-wide blocks that the
    # cluster's students actually attend, and by whatever empty-time
    # slack the programme leaves (G9/G11/G12 have none: their caps equal
    # the grid's full availability, so planning must saturate every
    # duration class exactly; G10's 160 free minutes are held back as
    # whole slots so placement leaves them empty)
    cluster_caps: Dict[int, Dict[int, int]] = {}
    for cid_, members in clusters.items():
        grade = students[members[0]].grade
        caps = dict(CLUSTER_CAPS)
        st0 = students[members[0]]
        # hold back the student's empty time as whole slots.  The reserve
        # must sum to the slack EXACTLY: residual caps then sum to the
        # cluster-lesson total, so any plan under the caps saturates
        # every duration class and placement is a perfect exact cover —
        # a reserve that misses by 5 minutes makes the caps unsatisfiable
        # and strands a session with no legal slot.  Prefer 40-min frees
        # (end-of-day; free P1 would trigger the self-study penalty).
        slack = tg.CYCLE_MINUTES - sum(courses[c].minutes
                                       for c in st0.course_ids)
        reserve = None
        a_max = min(caps[40], slack // 40)
        for a in range(a_max, -1, -1):
            rem = slack - 40 * a
            found = False
            for b in range(min(caps[45], rem // 45), -1, -1):
                rem2 = rem - 45 * b
                for c in range(min(caps[55], rem2 // 55), -1, -1):
                    rem3 = rem2 - 55 * c
                    if rem3 % 60 == 0 and rem3 // 60 <= caps[60]:
                        reserve = {40: a, 45: b, 55: c, 60: rem3 // 60}
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if reserve:
            for d, n in reserve.items():
                caps[d] -= n
        # a cluster attends a grade-wide block iff its students take it
        for (g, comp_key), lids in gradewide_done.items():
            if g != grade:
                continue
            takes = any(course_component[c] == comp_key
                        for c in st0.course_ids)
            if not takes:
                continue
            for d in lessons[lids[0]].plan:
                caps[d] -= 1
        les_ids = [l.id for l in lessons if l.cluster_id == cid_]
        specs = [{"minutes": courses[l.course_id].minutes,
                  "is_pe": courses[l.course_id].is_pe,
                  # lab time is scarce: prefer 40/45-min lab sessions
                  "long_penalty": 1.5 if courses[l.course_id].room_type == "lab"
                  else None}
                 for l in (lessons[i] for i in les_ids)]
        plans = plan_cluster(specs, rng, caps=caps)
        for lid, plan in zip(les_ids, plans):
            lessons[lid].plan = plan
        cluster_caps[cid_] = caps

    inst_cluster_caps = cluster_caps

    # ---- duration-aware teacher / room assignment ----------------------
    teachers_by_subject: Dict[str, List[int]] = {}
    teach_dur_load: Dict[int, Dict[int, int]] = {}
    teach_buildings: Dict[int, set] = {}
    teach_clusters: Dict[int, set] = {}

    def new_teacher(subject: str, full_availability: bool = False) -> int:
        tid = len(teachers)
        # grade-wide blocks are school-wide commitments: their teachers
        # are hired exactly for those slots, so no random unavailability.
        # Scarce long-period slots (P1/P4/P5) are always staffed: part-time
        # unavailability only ever lands on the plentiful 40-min periods,
        # where the repair loop has room to route around it.
        flexible = [s for s in range(tg.NUM_SLOTS) if SLOT_MINUTES[s] == 40]
        unavailable = (set() if full_availability
                       else {s for s in flexible if rng.random() < 0.10})
        idx = len(teachers_by_subject.get(subject, [])) + 1
        teachers.append(Teacher(
            id=tid, name=f"T-{subject.upper()}-{idx}",
            subjects={subject}, home_building="",
            unavailable=unavailable))
        teachers_by_subject.setdefault(subject, []).append(tid)
        teach_dur_load[tid] = {40: 0, 45: 0, 55: 0, 60: 0}
        teach_buildings[tid] = set()
        teach_clusters[tid] = set()
        return tid

    def fits(load: Dict[int, int], plan: List[int],
             caps: Dict[int, int]) -> bool:
        need = {d: plan.count(d) for d in caps}
        return all(load[d] + need[d] <= caps[d] for d in caps)

    def pick_teacher(subject: str, plan: List[int], building: str,
                     exclude: set = (), full_availability: bool = False,
                     cluster_id: int = -1) -> int:
        """Hire-on-demand, building-loyal: a teacher only ever works in ONE
        building, so cross-building travel (hard constraint 10) is
        impossible by construction and the mobility penalty vanishes.
        `exclude` holds teachers of sibling sections of the same lesson:
        parallel sections share their slot set, so they need DISTINCT
        teachers.  Grade-wide lessons set `full_availability`: with ~13
        parallel sections, random per-teacher unavailability would make
        some slot unavailable to the block almost surely."""
        pool = [t for t in teachers_by_subject.get(subject, [])
                if t not in exclude
                and teach_buildings[t] <= {building}
                and fits(teach_dur_load[t], plan, TEACHER_DUR_CAP)
                and (not full_availability or not teachers[t].unavailable)]
        if full_availability:
            # a grade-wide block's slots are shared by the whole grade;
            # sharing its teachers with any other lesson invites clashes
            # the repair loop can barely move -- use fresh teachers only
            fresh = [t for t in pool
                     if sum(teach_dur_load[t].values()) == 0]
            pool = fresh
        if not pool:
            tid = new_teacher(subject, full_availability=full_availability)
        else:
            # cluster continuity first: a teacher reused WITHIN one cluster
            # keeps cross-cluster slot-alignment (a repair-deadlock source)
            # rare; then prefer fewest clusters, then lightest load
            pool.sort(key=lambda t: (cluster_id not in teach_clusters[t],
                                     len(teach_clusters[t]),
                                     sum(teach_dur_load[t].values())))
            tid = pool[0]
        for d in plan:
            teach_dur_load[tid][d] += 1
        teach_buildings[tid].add(building)
        teach_clusters[tid].add(cluster_id)
        teachers[tid].home_building = teachers[tid].home_building or building
        return tid

    rooms_by_type: Dict[str, List[int]] = defaultdict(list)
    for r in rooms:
        rooms_by_type[r.room_type].append(r.id)
    room_dur_load: Dict[int, Dict[int, int]] = {
        r.id: {40: 0, 45: 0, 55: 0, 60: 0} for r in rooms}

    def pick_room(room_type: str, plan: List[int], wing: str,
                  exclude: set = ()) -> int:
        # parallel sections of one lesson share the slot set, so they
        # need DISTINCT rooms (`exclude`)
        pool = [r for r in rooms_by_type[room_type] if r not in exclude]
        if room_type == "classroom":            # prefer the grade's wing
            own = [r for r in pool if rooms[r].building == wing]
            if own:
                pool = own
        if not pool:
            raise ValueError(
                f"facility limit: a synchronised block needs "
                f"{len(exclude) + 1} '{room_type}' rooms at once but only "
                f"{len(rooms_by_type[room_type])} exist. With 20 students "
                f"per section, grade-synchronised blocks (PE/optional) cap "
                f"grade size at {len(rooms_by_type[room_type]) * 20} "
                f"students - reduce --students or add rooms in "
                f"data/school.json.")
        fitting = [r for r in pool if fits(room_dur_load[r], plan,
                                           ROOM_DUR_CAP)]
        if fitting:
            pool = fitting
        rid = min(pool, key=lambda r: sum(room_dur_load[r].values()))
        for d in plan:
            room_dur_load[rid][d] += 1
        return rid

    # grade-wide lessons first: their rooms/teachers are locked in first.
    # Room before teacher so the teacher is bound to the section's actual
    # room building (building-loyal hiring -> no cross-building travel).
    ordered = sorted(lessons, key=lambda l: (not l.grade_wide,
                                             -courses[l.course_id].minutes))
    for les in ordered:
        course = courses[les.course_id]
        wing = grade_wing[course.grade]
        used_teachers: set = set()
        used_rooms: set = set()
        for sid in les.section_ids:
            rid = pick_room(course.room_type, les.plan, wing,
                            exclude=used_rooms)
            used_rooms.add(rid)
            sections[sid].room_id = rid
            tid = pick_teacher(course.subject, les.plan,
                               rooms[rid].building, exclude=used_teachers,
                               full_availability=les.grade_wide,
                               cluster_id=les.cluster_id)
            used_teachers.add(tid)
            sections[sid].teacher_id = tid

    inst = Instance(
        courses=courses, teachers=teachers, rooms=rooms,
        students=students, sections=sections, lessons=lessons,
        clusters=clusters,
        weights=school["penalty_weights"],
        hard_weight=school["hard_weight"],
        section_capacity=cap, cfg=school)
    inst.sync_groups = sync_groups
    inst.cluster_caps = cluster_caps
    inst.build_indexes()
    return inst


def _variant_to_plan(v) -> List[int]:
    n40, n45, n55, n60 = v
    return [60] * n60 + [55] * n55 + [45] * n45 + [40] * n40
