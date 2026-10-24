const state = {
  data: null,
  grade: "9",
  track: "module",
  week: "A",
  aliases: {},
  hiddenCourses: new Set(),
  sessionLabels: new Map(),
  categoryOverrides: new Map(),
  template: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const categoryColors = {
  math: 0,
  science: 1,
  language: 2,
  module: 3,
  activity: 4,
  other: 5,
};

function courseCategory(course) {
  const normalized = course.toLowerCase();
  if (normalized.includes("math")) return "math";
  if (normalized.includes("physics") || normalized.includes("chem") || normalized.includes("biology") || normalized.includes("science")) return "science";
  if (normalized.includes("english") || normalized.includes("chinese") || normalized.includes("language")) return "language";
  if (normalized.includes("module") || normalized.includes("optional")) return "module";
  if (normalized === "pe" || normalized === "cas" || normalized.includes("growth")) return "activity";
  return "other";
}

function sessionKey(session) {
  return `${session.week}-${session.day}-${session.period}-${session.course}`;
}

function displayCourse(session) {
  const key = sessionKey(session);
  if (state.sessionLabels.has(key)) return state.sessionLabels.get(key);
  if (state.hiddenCourses.has(session.course)) return null;
  return state.aliases[session.course]?.trim() || session.course;
}

function seniorOptionMarkup(grade) {
  const isGrade11 = grade === "11";
  const options = state.data.courseOptions[grade];
  const module2Course = isGrade11 ? "Module 2: Electives" : "Module 2";
  const module2Minutes = isGrade11 ? 355 : 350;
  const module3Meta = isGrade11 ? "230 min / cycle" : "elective block";
  const module4Meta = isGrade11 ? "360 min / cycle" : "elective block";
  const languageMarkup = isGrade11 ? `
    <label class="option-block select-block" for="senior-language">
      <span class="option-head"><strong>Language</strong><span>235 min / cycle</span></span>
      <select id="senior-language" data-alias="Language" data-category="language" required>${selectOptions(options.language)}</select>
    </label>
  ` : "";
  const optionalMarkup = grade === "12" ? `
    <div class="switch-row">
      <div class="switch-copy"><strong>I take Optional</strong><small>Add or remove the optional block</small></div>
      <label class="switch"><input type="checkbox" id="takes-optional" aria-label="I take Optional" checked /><span></span></label>
    </div>
    <div class="fixed-choice" data-module-block="Optional"><strong>Optional</strong><span>110 min / cycle</span></div>
  ` : "";

  return `
    <div class="switch-row">
      <div class="switch-copy"><strong>I take PE</strong><small>Turn off if you have an IB-track exemption</small></div>
      <label class="switch"><input type="checkbox" id="takes-pe" aria-label="I take PE" checked /><span></span></label>
    </div>

    <div class="programme-rule">
      <div class="option-head"><strong>How many sciences do you take?</strong><span>Grade ${grade}</span></div>
      <div class="choice-segment" role="radiogroup" aria-label="Number of science classes">
        <label><input type="radio" name="science-count" value="1" checked /><span>1 science</span></label>
        <label><input type="radio" name="science-count" value="2" /><span>2 sciences</span></label>
      </div>
      <p id="science-rule-copy">With one science, choose Module 3, Module 4, or both.</p>
    </div>

    <label class="option-block select-block" for="senior-math">
      <span class="option-head"><strong>Mathematics</strong><span>choose your course</span></span>
      <select id="senior-math" data-alias="Mathematics" data-category="math" required>${selectOptions(options.mathematics)}</select>
    </label>

    ${languageMarkup}

    <label class="option-block select-block" for="science-1">
      <span class="option-head"><strong>Science 1</strong><span>your choice</span></span>
      <select id="science-1" required>${selectOptions(options.science1)}</select>
    </label>
    <label class="option-block select-block" id="science-2-block" for="science-2" hidden>
      <span class="option-head"><strong>Science 2</strong><span>your choice</span></span>
      <select id="science-2">${selectOptions(options.science2)}</select>
    </label>

    <label class="option-block select-block" data-module-block="${module2Course}" for="module-2-name">
      <span class="option-head"><strong>Module 2</strong><span>${module2Minutes} min / cycle</span></span>
      <select id="module-2-name" data-alias="${module2Course}" required>${selectOptions(options.module2)}</select>
    </label>

    <div class="elective-group" id="senior-electives">
      <div class="elective-heading">
        <strong>Elective modules</strong>
        <span>Choose M3, M4, or both</span>
      </div>
      <div class="module-choice">
        <div class="switch-row">
          <div class="switch-copy"><strong>I take Module 3</strong><small>${module3Meta}</small></div>
          <label class="switch"><input type="checkbox" id="takes-m3" aria-label="I take Module 3" checked /><span></span></label>
        </div>
        <label class="option-block compact select-block" id="module-3-block" for="module-3-name">
          <select id="module-3-name">${selectOptions(options.module3)}</select>
        </label>
      </div>
      <div class="module-choice">
        <div class="switch-row">
          <div class="switch-copy"><strong>I take Module 4</strong><small>${module4Meta}</small></div>
          <label class="switch"><input type="checkbox" id="takes-m4" aria-label="I take Module 4" checked /><span></span></label>
        </div>
        <label class="option-block compact select-block" id="module-4-block" for="module-4-name">
          <select id="module-4-name">${selectOptions(options.module4)}</select>
        </label>
      </div>
    </div>

    ${optionalMarkup}
    <p class="programme-error" id="programme-error" role="alert" hidden></p>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function selectOptions(options) {
  return '<option value="">Choose a course</option>' + options
    .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
    .join("");
}

function seniorTrackMarkup() {
  return `
    <div class="programme-rule track-rule">
      <div class="option-head"><strong>Which schedule do you follow?</strong><span>Grade ${state.grade}</span></div>
      <div class="choice-segment track-segment" role="radiogroup" aria-label="Schedule track">
        <label><input type="radio" name="schedule-track" value="module" ${state.track === "module" ? "checked" : ""} /><span>AP/ Alevel</span></label>
        <label><input type="radio" name="schedule-track" value="ib" ${state.track === "ib" ? "checked" : ""} /><span>IB Diploma</span></label>
      </div>
    </div>
    <div id="track-options"></div>
  `;
}

function ibOptionMarkup() {
  const groups = state.data.ib.course_groups;
  const blockMinutes = Object.fromEntries(state.data.ib.blocks.map((block) => [block.id, block.minutes]));
  return `
    <div class="ib-intro">
      <span class="ib-badge">Separate IB cycle</span>
      <strong>Choose one subject from Groups 1–5.</strong>
      <p>Your sixth subject can be an Arts course or one additional subject from Group 1, 3, or 4, following the supplied IB table.</p>
    </div>
    <div class="ib-groups">
      ${["group1", "group2", "group3", "group4", "group5"].map((id) => `
        <label class="option-block select-block" for="ib-${id}">
          <span class="option-head"><strong>${groups[id].label}</strong><span>${blockMinutes[id]} min / cycle</span></span>
          <select id="ib-${id}" data-ib-group="${id}" required>${selectOptions(groups[id].options)}</select>
        </label>
      `).join("")}
    </div>
    <div class="programme-rule">
      <div class="option-head"><strong>Your sixth subject</strong><span>430 min / cycle</span></div>
      <div class="choice-segment track-segment ib-sixth-segment" role="radiogroup" aria-label="IB sixth subject source">
        <label><input type="radio" name="ib-sixth-mode" value="arts" checked /><span>Group 6 Arts</span></label>
        <label><input type="radio" name="ib-sixth-mode" value="group1" /><span>Extra Group 1</span></label>
        <label><input type="radio" name="ib-sixth-mode" value="group3" /><span>Extra Group 3</span></label>
        <label><input type="radio" name="ib-sixth-mode" value="group4" /><span>Extra Group 4</span></label>
      </div>
    </div>
    <label class="option-block select-block" id="ib-group6-wrap" for="ib-group6">
      <span class="option-head"><strong>${groups.group6.label}</strong><span>choose one</span></span>
      <select id="ib-group6">${selectOptions(groups.group6.options)}</select>
    </label>
    <label class="option-block select-block" id="ib-extra-group1-wrap" for="ib-extra-group1" hidden>
      <span class="option-head"><strong>Additional Group 1 subject</strong><span>choose one</span></span>
      <select id="ib-extra-group1">${selectOptions(groups.group1.options)}</select>
    </label>
    <label class="option-block select-block" id="ib-extra-group3-wrap" for="ib-extra-group3" hidden>
      <span class="option-head"><strong>Additional Group 3 subject</strong><span>choose one</span></span>
      <select id="ib-extra-group3">${selectOptions(groups.group3.options)}</select>
    </label>
    <label class="option-block select-block" id="ib-extra-group4-wrap" for="ib-extra-group4" hidden>
      <span class="option-head"><strong>Additional Group 4 subject</strong><span>choose one</span></span>
      <select id="ib-extra-group4">${selectOptions(groups.group4.options)}</select>
    </label>
    <div class="fixed-blocks" aria-label="Fixed IB programme blocks">
      <span><strong>TOK</strong> 320 min</span><span><strong>PE</strong> 110 min</span><span><strong>CAS</strong> 80 min</span>
    </div>
    <p class="programme-error" id="programme-error" role="alert" hidden></p>
  `;
}

function updateSeniorRules() {
  const scienceCount = Number($('input[name="science-count"]:checked')?.value || 1);
  const twoSciences = scienceCount === 2;
  $("#science-2-block").hidden = !twoSciences;
  $("#senior-electives").hidden = twoSciences;
  $("#science-rule-copy").textContent = twoSciences
    ? "Two sciences use the M3 and M4 blocks, so elective choices are unavailable."
    : "With one science, choose Module 3, Module 4, or both.";

  if (!twoSciences) {
    $("#module-3-block").hidden = !$("#takes-m3").checked;
    $("#module-4-block").hidden = !$("#takes-m4").checked;
  }
  $("#programme-error").hidden = true;
}

function updateIbSixthChoice() {
  const mode = $('input[name="ib-sixth-mode"]:checked')?.value || "arts";
  $("#ib-group6-wrap").hidden = mode !== "arts";
  for (const group of ["group1", "group3", "group4"]) {
    $(`#ib-extra-${group}-wrap`).hidden = mode !== group;
  }
  $("#programme-error").hidden = true;
}

function attachModuleTrackListeners(container) {
  $$('input[name="science-count"]', container).forEach((input) => input.addEventListener("change", updateSeniorRules));
  [$("#takes-m3"), $("#takes-m4")].forEach((input) => input.addEventListener("change", updateSeniorRules));
  const optionalToggle = $("#takes-optional");
  if (optionalToggle) {
    optionalToggle.addEventListener("change", () => {
      container.querySelector('[data-module-block="Optional"]').hidden = !optionalToggle.checked;
    });
  }
  updateSeniorRules();
}

function renderSeniorTrackOptions() {
  const trackOptions = $("#track-options");
  if (state.track === "ib") {
    trackOptions.innerHTML = ibOptionMarkup();
    $$('input[name="ib-sixth-mode"]', trackOptions).forEach((input) => input.addEventListener("change", updateIbSixthChoice));
    updateIbSixthChoice();
    $("#form-note").textContent = "IB block times follow the supplied programme; rooms and teachers are not yet assigned.";
    return;
  }
  trackOptions.innerHTML = seniorOptionMarkup(state.grade);
  attachModuleTrackListeners(trackOptions);
  $("#form-note").textContent = "Uses representative room and teacher assignments from the timetable model.";
}

function renderProgrammeOptions() {
  const container = $("#programme-options");
  const config = state.data.grades[state.grade];
  const blocks = [];

  if (["11", "12"].includes(state.grade)) {
    container.innerHTML = seniorTrackMarkup();
    $$('input[name="schedule-track"]', container).forEach((input) => input.addEventListener("change", (event) => {
      state.track = event.target.value;
      renderSeniorTrackOptions();
    }));
    renderSeniorTrackOptions();
    return;
  }

  state.track = "module";
  $("#form-note").textContent = "Uses representative room and teacher assignments from the timetable model.";

  const gradeOptions = state.data.courseOptions[state.grade];
  blocks.push(`
    <label class="option-block select-block" for="world-language">
      <span class="option-head"><strong>World Language</strong><span>choose one</span></span>
      <select id="world-language" data-alias="World Language" data-category="language" required>${selectOptions(gradeOptions.world_language)}</select>
    </label>
  `);

  for (const module of config.modules) {
    if (module.skippable) {
      blocks.push(`
        <div class="switch-row">
          <div class="switch-copy"><strong>I take Optional</strong><small>Add or remove the optional block</small></div>
          <label class="switch"><input type="checkbox" id="takes-optional" aria-label="I take Optional" checked /><span></span></label>
        </div>
      `);
    }
    const optionKey = module.module === "Module 2" ? "module2" : "module3";
    blocks.push(`
      <label class="option-block select-block" data-module-block="${module.scheduleCourse}">
        <span class="option-head"><strong>${module.module}</strong><span>${module.minutes} min / cycle</span></span>
        <select data-alias="${module.scheduleCourse}" required>${selectOptions(gradeOptions[optionKey])}</select>
      </label>
    `);
  }

  container.innerHTML = blocks.length
    ? blocks.join("")
    : '<p class="programme-empty">This grade has a fixed programme in the current model—no module choices are required.</p>';

}

function buildIbTemplate() {
  const sessions = [];
  for (const week of ["A", "B"]) {
    state.data.days.forEach((day, dayIndex) => {
      state.data.ib.schedule_blocks[week][day].forEach((course, periodIndex) => {
        sessions.push({
          week,
          day,
          period: state.data.periods[periodIndex].id,
          course,
          room: "To be assigned",
          building: "IB programme",
          teacher: "To be assigned",
        });
      });
    });
  }
  return {
    id: `ib-g${state.grade}`,
    grade: Number(state.grade),
    representative: "IB block plan",
    takesPE: true,
    takesOptional: false,
    sessions,
  };
}

function chooseTemplate() {
  if (state.track === "ib") return buildIbTemplate();
  const takesPE = $("#takes-pe")?.checked ?? true;
  const takesOptional = $("#takes-optional")?.checked ?? true;
  const candidates = state.data.templates.filter((template) => String(template.grade) === state.grade);
  return candidates.find((template) => {
    const peMatches = !["11", "12"].includes(state.grade) || template.takesPE === takesPE;
    const optionalMatches = state.grade !== "12" || template.takesOptional === takesOptional;
    return peMatches && optionalMatches;
  }) || candidates[0];
}

function showProgrammeError(message, focusTarget) {
  const error = $("#programme-error");
  if (!error) return false;
  error.textContent = message;
  error.hidden = false;
  focusTarget?.focus();
  return false;
}

function validateSeniorProgramme() {
  if (!["11", "12"].includes(state.grade)) return true;
  const scienceCount = Number($('input[name="science-count"]:checked').value);
  const mathematics = $("#senior-math");
  const language = $("#senior-language");
  const science1 = $("#science-1");
  const science2 = $("#science-2");
  const module2 = $("#module-2-name");
  if (!mathematics.value) return showProgrammeError("Choose your mathematics course.", mathematics);
  if (language && !language.value) return showProgrammeError("Choose your Grade 11 language course.", language);
  if (!science1.value.trim()) return showProgrammeError("Choose your first science class.", science1);
  if (scienceCount === 2 && !science2.value.trim()) {
    return showProgrammeError("Choose your second science class.", science2);
  }
  if (scienceCount === 2 && science1.value.trim().toLowerCase() === science2.value.trim().toLowerCase()) {
    return showProgrammeError("Choose two different science classes.", science2);
  }
  if (scienceCount === 1 && !$("#takes-m3").checked && !$("#takes-m4").checked) {
    return showProgrammeError("With one science, choose Module 3, Module 4, or both.", $("#takes-m3"));
  }
  if (scienceCount === 1 && $("#takes-m3").checked && !$("#module-3-name").value.trim()) {
    return showProgrammeError("Choose your Module 3 class.", $("#module-3-name"));
  }
  if (scienceCount === 1 && $("#takes-m4").checked && !$("#module-4-name").value.trim()) {
    return showProgrammeError("Choose your Module 4 class.", $("#module-4-name"));
  }
  if (!module2.value) return showProgrammeError("Choose your Module 2 course.", module2);
  if (scienceCount === 1) {
    const module3 = $("#takes-m3").checked ? $("#module-3-name").value : "";
    const module4 = $("#takes-m4").checked ? $("#module-4-name").value : "";
    if (module3 && module3 === module2.value && module3 !== "Philosophy") {
      return showProgrammeError("Module 3 must be different from Module 2.", $("#module-3-name"));
    }
    if (state.grade === "11" && module2.value === "AP Psychology" && module3 === "Psychology") {
      return showProgrammeError("Psychology is unavailable in Module 3 when AP Psychology is selected in Module 2.", $("#module-3-name"));
    }
    if (module4 && [module2.value, module3].includes(module4)) {
      return showProgrammeError("Module 4 must be different from your other module choices.", $("#module-4-name"));
    }
  }
  return true;
}

function validateIbProgramme() {
  for (const group of ["group1", "group2", "group3", "group4", "group5"]) {
    const select = $(`#ib-${group}`);
    if (!select.value) return showProgrammeError(`Choose a course for ${state.data.ib.course_groups[group].label}.`, select);
  }
  const mode = $('input[name="ib-sixth-mode"]:checked').value;
  const sixth = mode === "arts" ? $("#ib-group6") : $(`#ib-extra-${mode}`);
  if (!sixth.value) return showProgrammeError("Choose your sixth IB subject.", sixth);
  if (mode !== "arts" && $(`#ib-${mode}`).value === sixth.value) {
    return showProgrammeError(`Your additional ${state.data.ib.course_groups[mode].label} subject must be different from your first choice.`, sixth);
  }
  return true;
}

function validateProgramme() {
  if (state.track === "ib") return validateIbProgramme();
  return validateSeniorProgramme();
}

function collectProgrammeChoices() {
  state.aliases = {};
  state.hiddenCourses = new Set();
  state.sessionLabels = new Map();
  state.categoryOverrides = new Map();

  if (state.track === "ib") {
    for (const group of ["group1", "group2", "group3", "group4", "group5"]) {
      state.aliases[group] = $(`#ib-${group}`).value;
    }
    const mode = $('input[name="ib-sixth-mode"]:checked').value;
    const sixth = mode === "arts" ? $("#ib-group6").value : $(`#ib-extra-${mode}`).value;
    state.aliases.group6 = sixth;
    state.aliases.tok = "Theory of Knowledge (TOK)";
    state.aliases.pe = "PE";
    state.aliases.cas = "CAS";
    state.categoryOverrides.set("group1", "language");
    state.categoryOverrides.set("group2", "language");
    state.categoryOverrides.set("group3", "other");
    state.categoryOverrides.set("group4", "science");
    state.categoryOverrides.set("group5", "math");
    const sixthCategory = mode === "group1" ? "language" : mode === "group4" ? "science" : "other";
    state.categoryOverrides.set("group6", sixthCategory);
    state.categoryOverrides.set("tok", "other");
    state.categoryOverrides.set("pe", "activity");
    state.categoryOverrides.set("cas", "activity");
    return;
  }

  $$('[data-alias]').forEach((input) => {
    const block = input.closest("[data-module-block]");
    if (!block || !block.hidden) {
      state.aliases[input.dataset.alias] = input.value;
      if (input.dataset.category) state.categoryOverrides.set(input.dataset.alias, input.dataset.category);
    }
  });

  if (!["11", "12"].includes(state.grade)) return;
  const scienceCount = Number($('input[name="science-count"]:checked').value);
  const science1 = $("#science-1").value.trim();
  const science2 = $("#science-2").value.trim();
  const takesM3 = $("#takes-m3").checked;
  const takesM4 = $("#takes-m4").checked;
  const module3 = $("#module-3-name").value.trim();
  const module4 = $("#module-4-name").value.trim();

  if (state.grade === "11") {
    state.aliases["Module 1: Science"] = science1;
    state.categoryOverrides.set("Module 1: Science", "science");
    if (scienceCount === 2) {
      state.aliases["Module 3: Electives"] = science2;
      state.aliases["Module 4: Electives"] = science2;
      state.categoryOverrides.set("Module 3: Electives", "science");
      state.categoryOverrides.set("Module 4: Electives", "science");
    } else {
      if (takesM3) state.aliases["Module 3: Electives"] = module3;
      else state.hiddenCourses.add("Module 3: Electives");
      if (takesM4) state.aliases["Module 4: Electives"] = module4;
      else state.hiddenCourses.add("Module 4: Electives");
    }
    return;
  }

  state.aliases["Module 1: AP Physics 2"] = science1;
  state.categoryOverrides.set("Module 1: AP Physics 2", "science");
  if (scienceCount === 2) {
    state.aliases["Module 1: Honors Organic Chemistry"] = science2;
    state.categoryOverrides.set("Module 1: Honors Organic Chemistry", "science");
    return;
  }

  state.categoryOverrides.set("Module 1: Honors Organic Chemistry", "module");
  const electiveNames = [takesM3 ? module3 : null, takesM4 ? module4 : null].filter(Boolean);
  const electiveSessions = state.template.sessions.filter(
    (session) => session.course === "Module 1: Honors Organic Chemistry",
  );
  electiveSessions.forEach((session, index) => {
    state.sessionLabels.set(sessionKey(session), electiveNames[index % electiveNames.length]);
  });
}

function resultSubtitle() {
  if (state.track === "ib") {
    return `Separate IB Diploma block plan · Week ${state.week} · rooms and teachers to be assigned`;
  }
  return `Built from the Grade ${state.grade} module plan · Week ${state.week} shown below`;
}

function effectiveCategory(session) {
  return state.categoryOverrides.get(session.course) || courseCategory(displayCourse(session) || session.course);
}

function colorFor(session) {
  return categoryColors[effectiveCategory(session)];
}

function renderStats() {
  const visibleSessions = state.template.sessions.filter((session) => displayCourse(session));
  const uniqueCourses = new Set(visibleSessions.map((session) => displayCourse(session))).size;
  const usedSlots = visibleSessions.length;
  const minutes = visibleSessions.reduce((total, session) => {
    const period = state.data.periods.find((item) => item.id === session.period);
    return total + (period?.minutes || 0);
  }, 0);
  const stats = [
    [uniqueCourses, "Courses in your cycle"],
    [usedSlots, "Scheduled periods"],
    [`${minutes.toLocaleString()} min`, "Learning time"],
  ];
  $("#result-stats").innerHTML = stats
    .map(([value, label]) => `<div class="stat-card"><strong>${value}</strong><span>${label}</span></div>`)
    .join("");
}

function renderLegend() {
  const categories = [...new Set(
    state.template.sessions.filter((session) => displayCourse(session)).map((session) => effectiveCategory(session)),
  )];
  $("#legend").innerHTML = categories
    .map((category) => `<span><i class="class-cell" data-color="${categoryColors[category]}"></i>${category}</span>`)
    .join("");
}

function openClassDialog(session) {
  const period = state.data.periods.find((item) => item.id === session.period);
  $("#dialog-slot").textContent = `Week ${session.week} · ${session.day} · ${session.period} · ${period.start}`;
  $("#dialog-course").textContent = displayCourse(session);
  $("#dialog-teacher").textContent = session.teacher;
  $("#dialog-room").textContent = session.room;
  $("#dialog-building").textContent = session.building;
  $("#dialog-duration").textContent = `${period.minutes} minutes`;
  $("#class-dialog").showModal();
}

function renderSchedule() {
  const grid = $("#schedule-grid");
  const sessions = state.template.sessions.filter(
    (session) => session.week === state.week && displayCourse(session),
  );
  const lookup = new Map(sessions.map((session) => [`${session.day}-${session.period}`, session]));
  const fragment = document.createDocumentFragment();

  const corner = document.createElement("div");
  corner.className = "grid-corner";
  fragment.append(corner);
  for (const day of state.data.days) {
    const heading = document.createElement("div");
    heading.className = "day-heading";
    heading.textContent = day;
    fragment.append(heading);
  }

  for (const period of state.data.periods) {
    const label = document.createElement("div");
    label.className = "period-label";
    label.innerHTML = `<strong>${period.id}</strong><span>${period.start} · ${period.minutes}m</span>`;
    fragment.append(label);

    for (const day of state.data.days) {
      const session = lookup.get(`${day}-${period.id}`);
      if (session) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "class-cell";
        button.dataset.color = colorFor(session);
        button.setAttribute("role", "gridcell");
        button.setAttribute("aria-label", `${day} ${period.id}, ${displayCourse(session)}, ${session.room}`);
        const course = document.createElement("span");
        course.className = "course";
        course.textContent = displayCourse(session);
        const room = document.createElement("span");
        room.className = "room";
        room.textContent = session.room;
        const teacher = document.createElement("span");
        teacher.className = "cell-meta";
        teacher.textContent = session.teacher;
        button.append(course, room, teacher);
        button.addEventListener("click", () => openClassDialog(session));
        fragment.append(button);
      } else {
        const empty = document.createElement("div");
        empty.className = "empty-cell";
        empty.setAttribute("role", "gridcell");
        empty.textContent = day === "Fri" && ["P6", "P7"].includes(period.id) ? "No period" : "Free";
        fragment.append(empty);
      }
    }
  }

  grid.replaceChildren(fragment);
}

function showResult() {
  const name = $("#student-name").value.trim();
  const prefix = name ? `${name}’s` : "Your";
  $("#result-title").textContent = `${prefix} Grade ${state.grade}${state.track === "ib" ? " IB" : ""} timetable`;
  $("#result-subtitle").textContent = resultSubtitle();
  renderStats();
  renderLegend();
  renderSchedule();
  const result = $("#result");
  result.hidden = false;
  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function init() {
  try {
    const response = await fetch("./data/schedule-data.json");
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    state.data = await response.json();
    renderProgrammeOptions();
  } catch (error) {
    $("#programme-options").innerHTML = `<p class="programme-empty">The timetable data could not be loaded. Please refresh and try again.</p>`;
    $(".generate-button").disabled = true;
    console.error(error);
  }
}

$("#grade-picker").addEventListener("change", (event) => {
  if (event.target.name !== "grade") return;
  state.grade = event.target.value;
  renderProgrammeOptions();
});

$("#schedule-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateProgramme()) return;
  state.template = chooseTemplate();
  collectProgrammeChoices();
  state.week = "A";
  $$(".week-tab").forEach((tab) => {
    const active = tab.dataset.week === "A";
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  showResult();
});

$$(".week-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.week = tab.dataset.week;
    $$(".week-tab").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    $("#result-subtitle").textContent = resultSubtitle();
    renderSchedule();
  });
});

$("#edit-button").addEventListener("click", () => $("#builder").scrollIntoView({ behavior: "smooth" }));
$("#print-button").addEventListener("click", () => window.print());
$(".dialog-close").addEventListener("click", () => $("#class-dialog").close());
$("#class-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

init();
