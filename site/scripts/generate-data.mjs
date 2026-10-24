import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const siteRoot = path.resolve(here, "..");
const projectRoot = path.resolve(siteRoot, "..");
const curriculum = JSON.parse(
  fs.readFileSync(path.join(projectRoot, "data/curriculum.json"), "utf8"),
);
const ibCurriculum = JSON.parse(
  fs.readFileSync(path.join(projectRoot, "data/ib-curriculum.json"), "utf8"),
);
const courseOptions = JSON.parse(
  fs.readFileSync(path.join(projectRoot, "data/course-options.json"), "utf8"),
);
const csv = fs
  .readFileSync(path.join(projectRoot, "out/timetable_students.csv"), "utf8")
  .trim()
  .split(/\r?\n/);

const headers = csv[0].split(",");
const rows = csv.slice(1).map((line) => {
  const values = line.split(",");
  return Object.fromEntries(headers.map((header, index) => [header, values[index]]));
});

const byStudent = new Map();
for (const row of rows) {
  if (!byStudent.has(row.student)) byStudent.set(row.student, []);
  byStudent.get(row.student).push(row);
}

const seen = new Set();
const templates = [];
for (const [student, sessions] of byStudent) {
  const grade = Number(sessions[0].grade);
  const courses = [...new Set(sessions.map((session) => session.course))].sort();
  const signature = `${grade}|${courses.join("|")}`;
  if (seen.has(signature)) continue;
  seen.add(signature);
  templates.push({
    id: `g${grade}-${templates.filter((template) => template.grade === grade).length + 1}`,
    grade,
    representative: student,
    takesPE: courses.includes("PE"),
    takesOptional: courses.includes("Optional"),
    courses,
    sessions: sessions.map(({ week, day, period, course, room, building, teacher }) => ({
      week,
      day,
      period,
      course,
      room,
      building,
      teacher,
    })),
  });
}

const grades = Object.fromEntries(
  Object.entries(curriculum.grades).map(([grade, config]) => [
    grade,
    {
      academicTarget: config.academic_target,
      fixed: config.components
        .filter((component) => component.type === "fixed")
        .map(({ name, minutes, category, demanding, room_type, ib_exempt }) => ({
          name,
          minutes,
          category,
          demanding: Boolean(demanding),
          roomType: room_type,
          ibExempt: Boolean(ib_exempt),
        })),
      modules: config.components
        .filter((component) => component.type === "choice")
        .map((component) => ({
          module: component.module,
          skippable: Boolean(component.skippable),
          scheduleCourse: component.options[0].name,
          minutes: component.options[0].minutes,
        })),
    },
  ]),
);

const payload = {
  generatedAt: new Date().toISOString(),
  source: "HackSHSID 2026 optimized seed-7 timetable",
  days: ["Mon", "Tue", "Wed", "Thu", "Fri"],
  periods: [
    { id: "P1", start: "08:30", minutes: 60 },
    { id: "P2", start: "09:40", minutes: 40 },
    { id: "P3", start: "10:30", minutes: 40 },
    { id: "P4", start: "11:20", minutes: 45 },
    { id: "P5", start: "13:15", minutes: 55 },
    { id: "P6", start: "14:20", minutes: 40 },
    { id: "P7", start: "15:10", minutes: 40 },
  ],
  grades,
  ib: ibCurriculum,
  courseOptions,
  templates,
  metrics: {
    students: 1000,
    sections: 563,
    teachingPeriods: 66,
    hardViolations: 0,
    cycleMinutes: 3040,
  },
};

const outputDir = path.join(siteRoot, "dist", "data");
fs.mkdirSync(outputDir, { recursive: true });
fs.writeFileSync(
  path.join(outputDir, "schedule-data.json"),
  `${JSON.stringify(payload)}\n`,
  "utf8",
);
console.log(`Wrote ${templates.length} timetable templates to dist/data/schedule-data.json`);
