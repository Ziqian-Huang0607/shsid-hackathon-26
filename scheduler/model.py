"""Core domain model: courses, teachers, rooms, students, sections.

A *Course* is an enrolment unit for one grade (e.g. "G11 Module 1: Science").
A *Section* is a taught instance of a course: one teacher, one room,
<= 25 students, and a set of time slots whose minutes must sum to at
least the course's required two-week minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from . import timegrid as tg


@dataclass
class Course:
    id: int
    grade: int
    name: str
    subject: str          # eligibility key for teachers
    minutes: int          # required minutes per 2-week cycle
    category: str         # STEM | HUM | OTHER  (drives alternation penalty)
    demanding: bool
    difficulty: float = 3.0   # cognitive-load score 1..5 (5 = most intense)
    room_type: str = "classroom"        # classroom | lab | gym | studio
    is_pe: bool = False
    academic: bool = True
    component: str = ""     # which programme component this course fills

    @property
    def label(self) -> str:
        return f"G{self.grade} {self.name}"


@dataclass
class Teacher:
    id: int
    name: str
    subjects: Set[str]
    home_building: str
    unavailable: Set[int] = field(default_factory=set)   # slot ids

    def available(self, slot: int) -> bool:
        return slot not in self.unavailable


@dataclass
class Room:
    id: int
    name: str
    building: str
    room_type: str
    capacity: int = 25


@dataclass
class Student:
    id: int
    grade: int
    name: str
    ib: bool = False        # IB-track students may skip PE in G11/G12
    course_ids: List[int] = field(default_factory=list)
    section_ids: List[int] = field(default_factory=list)


@dataclass
class Section:
    id: int
    course_id: int
    teacher_id: int
    room_id: int                          # home room (default for all slots)
    students: List[int] = field(default_factory=list)
    slots: Set[int] = field(default_factory=set)
    # sparse per-slot room overrides: the hard constraints only require a
    # suitable classroom PER SESSION ("a classroom cannot accommodate two
    # classes simultaneously"), so the final room solver may place
    # individual sessions of a section in different same-type rooms of the
    # same building.  Rooms of a section always stay in one building
    # (teacher building loyalty, H10).
    slot_rooms: Dict[int, int] = field(default_factory=dict)

    def minutes(self) -> int:
        return sum(tg.SLOT_MINUTES[s] for s in self.slots)

    def room_at(self, slot: int) -> int:
        return self.slot_rooms.get(slot, self.room_id)


@dataclass
class Lesson:
    """A schedulable block: one cohort-cluster taking one course.

    Parallel sections (when a cluster exceeds room capacity) share the
    same slot set but use distinct teachers and rooms.  Because every
    student of the cluster sits in the lesson, scheduling lessons
    disjointly per cluster makes student double-booking impossible *by
    construction*.
    """
    id: int
    cluster_id: int
    course_id: int
    section_ids: List[int] = field(default_factory=list)
    slots: Set[int] = field(default_factory=set)
    plan: List[int] = field(default_factory=list)   # planned durations
    grade_wide: bool = False    # whole grade attends at the same time
    sync_group: int = -1        # lessons in one group share the slot set

    def minutes(self) -> int:
        return sum(tg.SLOT_MINUTES[s] for s in self.slots)


@dataclass
class Instance:
    courses: List[Course]
    teachers: List[Teacher]
    rooms: List[Room]
    students: List[Student]
    sections: List[Section]
    lessons: List[Lesson]
    clusters: Dict[int, List[int]]     # cluster_id -> student ids
    weights: Dict[str, float]          # soft penalty weights
    hard_weight: float
    section_capacity: int
    cfg: Dict                          # raw school.json (thresholds etc.)

    # indexes (teach_secs/room_secs map a section to its teacher/room;
    # rebuilt whenever the optimiser re-binds a teacher or home room)
    teach_secs: Dict[int, List[int]] = field(default_factory=dict)
    room_secs: Dict[int, List[int]] = field(default_factory=dict)
    sync_groups: Dict[int, List[int]] = field(default_factory=dict)
    # per-cluster duration caps used by the planner (grade-wide usage
    # already deducted) -- stored so the optimiser can replan a cluster
    cluster_caps: Dict[int, Dict[int, int]] = field(default_factory=dict)
    # per-slot room demand model: rooms are fungible within
    # (building, room_type) and assigned per session, so the feasibility
    # invariant during search is demand <= supply per (slot, building,
    # type).  sec_bt caches a section's (building, room_type), which is
    # invariant (re-binds never cross buildings or types).
    room_supply: Dict[tuple, int] = field(default_factory=dict)
    room_demand: Dict[int, Dict[tuple, int]] = field(default_factory=dict)
    sec_bt: Dict[int, tuple] = field(default_factory=dict)

    def build_indexes(self) -> None:
        self.teach_secs = {t.id: [] for t in self.teachers}
        self.room_secs = {r.id: [] for r in self.rooms}
        for s in self.sections:
            self.teach_secs[s.teacher_id].append(s.id)
            self.room_secs[s.room_id].append(s.id)
        self.room_supply = {}
        for r in self.rooms:
            bt = (r.building, r.room_type)
            self.room_supply[bt] = self.room_supply.get(bt, 0) + 1
        self.sec_bt = {s.id: (self.rooms[s.room_id].building,
                              self.rooms[s.room_id].room_type)
                       for s in self.sections}
        self.room_demand = {s: {} for s in range(tg.NUM_SLOTS)}
        for sec in self.sections:
            bt = self.sec_bt[sec.id]
            for sl in sec.slots:
                d = self.room_demand[sl]
                d[bt] = d.get(bt, 0) + 1

    def room_excess(self, slot: int) -> int:
        """Sessions at `slot` that cannot be housed (demand > supply)."""
        return sum(max(0, d - self.room_supply.get(bt, 0))
                   for bt, d in self.room_demand.get(slot, {}).items())

    def course_of_section(self, sid: int) -> Course:
        return self.courses[self.sections[sid].course_id]
