/** Decide Well - SPA controller. */

import { api, ApiError, getToken, setToken } from "./api.js?v=28";
import { startParticles, stopParticles } from "./particles.js?v=15";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  user: null,
  authMode: "login", // login | register
  view: "tasks",
  taskFilter: "pending",
  taskLayout: "list", // list | matrix (Eisenhower)
  quadrantTitles: {}, // key -> display name, cached from the matrix payload
  surveySpec: null,
  surveyAnswers: null, // saved answers (null until submitted)
  wizard: { step: 0, draft: {} },
  taskIndex: {}, // id -> task, for opening the decision workspace
  // Data-protection notice, fetched once and rendered into the sign-up form.
  notice: null,
  // What the sign-up checkboxes currently say. Every box starts false - a
  // pre-ticked consent box is not consent, so the default has to be "no".
  consent: {
    privacy: false, personalization: false, ai_processing: false,
    age_confirmed: false,
  },
  learning: null, // cached /api/feedback/profile for the Insights tab
};

/* ================= helpers ================= */

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function fmtDate(iso, dateOnly = false) {
  if (!iso) return "-";
  const opts = dateOnly
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };
  return new Date(iso).toLocaleString(undefined, opts);
}

function dueLabel(iso) {
  const ms = new Date(iso) - Date.now();
  if (ms < 0) return `<span class="overdue-tag">overdue</span>`;
  const hours = ms / 36e5;
  if (hours < 24) return `due in ${Math.max(1, Math.round(hours))}h`;
  return `due in ${Math.round(hours / 24)}d`;
}

function minutesLabel(min) {
  if (min == null) return "";
  return min >= 60 ? `${(min / 60).toFixed(min % 60 ? 1 : 0)}h` : `${min}m`;
}

/* The assistant's product name. The wire/DB value stays "gemini" so old
   history rows keep resolving; only the label the user reads is AVEX. */
const AI_NAME = "AVEX";
const sourceLabel = (src) => (src === "gemini" ? AI_NAME : "Engine");

function scoreClass(score) {
  if (score >= 70) return "score-hot";
  if (score >= 45) return "score-warm";
  return "score-cool";
}

const ICONS = {
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  undo: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 00-15-6.7L3 13"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>`,
};

/** Safe renderer for insight text (bullets + bold only). */
function renderInsight(text) {
  const lines = escapeHtml(text).split("\n").map((l) => l.trim()).filter(Boolean);
  const bullets = lines.filter((l) => /^[-*•]/.test(l));
  const bold = (s) => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  if (bullets.length) {
    const rest = lines.filter((l) => !/^[-*•]/.test(l)).map(bold).join("<br>");
    return (rest ? `<p>${rest}</p>` : "") +
      `<ul>${bullets.map((b) => `<li>${bold(b.replace(/^[-*•]\s*/, ""))}</li>`).join("")}</ul>`;
  }
  return `<p>${lines.map(bold).join("<br>")}</p>`;
}

/* ================= theme ================= */
/* Three choices, not two: light, dark, or follow the device. The inline
   script in index.html has already stamped data-theme before first paint;
   this keeps it in sync when the user picks, and when the OS flips while
   they are on "system". */

const THEME_KEY = "dw_theme";
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function themeChoice() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

function applyTheme() {
  const choice = themeChoice();
  const dark = choice === "dark" || (choice === "system" && darkQuery.matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  $$("#theme-switch button").forEach((b) =>
    b.classList.toggle("active", b.dataset.themeChoice === choice));
}

function setTheme(choice) {
  try {
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch { /* private mode: the choice just won't outlive the tab */ }
  applyTheme();
}

/* ================= behavioural science ================= */
/* One correlation = a published finding + the evidence for it in the user's
   own data + the engine's response. Rendered identically wherever it appears
   (Tasks matrix, decision audit, Insights), so the vocabulary stays stable. */

const BS_KIND = {
  driver: { label: "Applied", cls: "bs-driver" },
  risk: { label: "Watch for", cls: "bs-risk" },
};

function bsCard(link) {
  const kind = BS_KIND[link.kind] || BS_KIND.driver;
  return `
    <article class="bs-item ${kind.cls}${link.strength === "strong" ? " strong" : ""}">
      <div class="bs-item-head">
        <span class="bs-kind">${kind.label}</span>
        <h4 class="bs-name">${escapeHtml(link.effect)}</h4>
        ${link.metric ? `<span class="bs-metric">${escapeHtml(link.metric)}</span>` : ""}
      </div>
      <p class="bs-finding">${escapeHtml(link.finding)}</p>
      <dl class="bs-rows">
        <div><dt>In your data</dt><dd>${escapeHtml(link.signal)}</dd></div>
        <div><dt>So the app</dt><dd>${escapeHtml(link.response)}</dd></div>
      </dl>
      <cite class="bs-source">${escapeHtml(link.source)}</cite>
    </article>`;
}

function bsList(links, emptyHint) {
  if (!links || !links.length) {
    return emptyHint ? `<p class="muted">${escapeHtml(emptyHint)}</p>` : "";
  }
  return `<div class="bs-grid">${links.map(bsCard).join("")}</div>`;
}

/* ================= data-protection consent =================
   The sign-up screen has to be *informed* consent, which means the user can
   read what each purpose actually does before ticking it - not a link to a
   policy nobody opens. Every word below comes from GET /api/privacy/notice,
   the same module the server validates against, so the agreement on screen
   and the agreement on record cannot drift apart. */

/* The notice names the mandatory scope "essential"; the register request
   calls the same thing `privacy`. Map here rather than renaming either -
   the scope key is what the consent ledger records, and the field name is
   what the schema validates. */
const CONSENT_FIELD = {
  essential: "privacy",
  personalization: "personalization",
  ai_processing: "ai_processing",
};

const CONSENT_ICONS = {
  essential: "M12 3 5 6v6c0 4.4 3 8 7 9 4-1 7-4.6 7-9V6Z",
  personalization: "M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5",
  ai_processing: "M12 3.2c4.7 0 8.5 3.2 8.5 7.2s-3.8 7.2-8.5 7.2c-1 0-2-.1-2.9-.4L5 19l1-3.2C4.4 14.5 3.5 12.9 3.5 10.4c0-4 3.8-7.2 8.5-7.2Z",
};

function consentScopeDetails(scope) {
  const rows = [
    ["What it covers", (scope.collects || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("")],
  ];
  return `
    <div class="consent-detail">
      <p class="consent-purpose">${escapeHtml(scope.purpose || "")}</p>
      <ul class="consent-collects">${rows[0][1]}</ul>
      <dl class="consent-facts">
        <dt>Legal basis</dt><dd>${escapeHtml(scope.legal_basis || "-")}</dd>
        ${scope.recipients ? `<dt>Who else sees it</dt><dd>${escapeHtml(scope.recipients)}</dd>` : ""}
        <dt>How long we keep it</dt><dd>${escapeHtml(scope.retention || "-")}</dd>
        <dt>${scope.required ? "Why it is required" : "If you say no"}</dt>
        <dd>${escapeHtml(scope.if_declined || "-")}</dd>
        ${scope.withdrawal ? `<dt>Changing your mind</dt><dd>${escapeHtml(scope.withdrawal)}</dd>` : ""}
      </dl>
    </div>`;
}

function consentScopeRow(scope) {
  const required = !!scope.required;
  return `
    <div class="consent-scope${required ? " is-required" : ""}">
      <label class="consent-row">
        <input type="checkbox"
               data-consent="${escapeHtml(CONSENT_FIELD[scope.key] || scope.key)}"
               ${required ? 'data-required="1"' : ""} />
        <span class="consent-box" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M5 12.5 10 17.5 19 7"
            fill="none" stroke="currentColor" stroke-width="2.6"
            stroke-linecap="round" stroke-linejoin="round"/></svg>
        </span>
        <span class="consent-text">
          <span class="consent-title">
            <svg class="consent-ico" viewBox="0 0 24 24" aria-hidden="true">
              <path d="${CONSENT_ICONS[scope.key] || CONSENT_ICONS.essential}"
                    fill="none" stroke="currentColor" stroke-width="1.7"
                    stroke-linejoin="round" stroke-linecap="round"/>
            </svg>
            <span class="consent-title-text">${escapeHtml(scope.title)}</span>
            <em class="consent-flag">${required ? "Required" : "Optional"}</em>
          </span>
          <span class="consent-summary">${escapeHtml(scope.summary)}</span>
        </span>
      </label>
      <button type="button" class="consent-more" data-more="${escapeHtml(scope.key)}"
              aria-expanded="false">What this means</button>
      <div class="consent-detail-wrap hidden" data-detail="${escapeHtml(scope.key)}">
        ${consentScopeDetails(scope)}
      </div>
    </div>`;
}

function renderConsent() {
  const root = $("#auth-consent");
  const notice = state.notice;
  if (!root) return;
  if (!notice) {
    root.innerHTML = `<p class="consent-lead">Loading the privacy notice...</p>`;
    return;
  }

  root.innerHTML = `
    <div class="consent-head">
      <span class="consent-kicker">Before you start</span>
      <span class="consent-version">Notice v${escapeHtml(notice.version)}</span>
    </div>
    <p class="consent-lead">${escapeHtml(notice.summary)}</p>

    ${notice.scopes.map(consentScopeRow).join("")}

    <label class="consent-row consent-age">
      <input type="checkbox" data-consent="age_confirmed" data-required="1" />
      <span class="consent-box" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M5 12.5 10 17.5 19 7"
          fill="none" stroke="currentColor" stroke-width="2.6"
          stroke-linecap="round" stroke-linejoin="round"/></svg>
      </span>
      <span class="consent-text">
        <span class="consent-title">
          <span class="consent-title-text">I am 14 or over</span>
          <em class="consent-flag">Required</em></span>
        <span class="consent-summary">${escapeHtml(notice.age_notice)}</span>
      </span>
    </label>

    <button type="button" class="consent-more consent-more--all" id="consent-full"
            aria-expanded="false">Read how we handle your data</button>
    <div class="consent-detail-wrap hidden" id="consent-full-body">
      <div class="consent-detail">
        ${notice.practices.map((practice) => `
          <p class="consent-practice">
            <strong>${escapeHtml(practice.title)}.</strong>
            ${escapeHtml(practice.body)}
          </p>`).join("")}
        <p class="consent-practice"><strong>Your rights.</strong> All of these
          work today, from inside the app:</p>
        <ul class="consent-collects">
          ${notice.rights.map((right) => `
            <li><strong>${escapeHtml(right.right)}</strong> - ${escapeHtml(right.how)}</li>`).join("")}
        </ul>
      </div>
    </div>`;

  // Re-apply whatever was already ticked, so switching between login and
  // sign-up does not silently drop the user's answers.
  $$("#auth-consent input[type=checkbox]").forEach((box) => {
    box.checked = !!state.consent[box.dataset.consent];
  });
}

function consentComplete() {
  return state.consent.privacy && state.consent.age_confirmed;
}

function bindConsent() {
  const root = $("#auth-consent");
  root.addEventListener("change", (event) => {
    const box = event.target.closest("input[type=checkbox]");
    if (!box) return;
    state.consent[box.dataset.consent] = box.checked;
    box.closest(".consent-scope, .consent-age")
      ?.classList.toggle("is-missing", !!box.dataset.required && !box.checked);
    $("#auth-error").classList.add("hidden");
  });

  root.addEventListener("click", (event) => {
    const trigger = event.target.closest(".consent-more");
    if (!trigger) return;
    const key = trigger.dataset.more;
    const panel = key
      ? root.querySelector(`[data-detail="${key}"]`)
      : $("#consent-full-body");
    if (!panel) return;
    const open = panel.classList.toggle("hidden");
    trigger.setAttribute("aria-expanded", String(!open));
    trigger.textContent = open
      ? (key ? "What this means" : "Read how we handle your data")
      : "Hide";
  });
}

async function loadNotice() {
  if (state.notice) return;
  try {
    state.notice = await api.privacyNotice();
  } catch {
    state.notice = null;
  }
  renderConsent();
}

/* ================= auth ================= */

function showAuth() {
  $("#auth-screen").classList.remove("hidden");
  $("#app-shell").classList.add("hidden");
  startParticles();
}

function applyAuthMode() {
  const registering = state.authMode === "register";
  $("#auth-name-row").classList.toggle("hidden", !registering);
  // The consent block belongs to sign-up only. Showing it at log-in would
  // ask people to re-consent to something they already agreed to.
  $("#auth-consent").classList.toggle("hidden", !registering);
  if (registering) loadNotice();
  $("#auth-submit-label").textContent = registering ? "Create Account" : "Log In";
  $("#auth-switch-label").textContent = registering ? "Already have an account?" : "Don't have an account?";
  $("#auth-switch-link").textContent = registering ? "Log in" : "Sign up";
  // Login mode shows the "New User" CTA; register mode shows the way back.
  $("#new-user-btn").classList.toggle("hidden", registering);
  $("#auth-or").classList.toggle("hidden", registering);
  $("#auth-switch-row").classList.toggle("hidden", !registering);
  $("#auth-error").classList.add("hidden");
}

// The CTA starts as a single button; the first click reveals the credential fields.
function revealAuthFields() {
  const form = $("#auth-form");
  if (!form.classList.contains("is-collapsed")) return false;
  form.classList.remove("is-collapsed");
  const first = state.authMode === "register" ? $("#auth-name") : $("#auth-email");
  first.focus();
  return true;
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  if (revealAuthFields()) return;
  const errorEl = $("#auth-error");
  errorEl.classList.add("hidden");
  const email = $("#auth-email").value.trim();
  const password = $("#auth-password").value;
  const submit = $("#auth-submit");

  if (state.authMode === "register" && !consentComplete()) {
    // Fail here rather than at the server, so the user sees which box is
    // outstanding instead of a generic 422.
    $$("#auth-consent input[data-required]").forEach((box) => {
      box.closest(".consent-scope, .consent-age")
        ?.classList.toggle("is-missing", !box.checked);
    });
    errorEl.textContent = state.consent.privacy
      ? "Please confirm your age to continue."
      : "Please accept the privacy notice to create an account.";
    errorEl.classList.remove("hidden");
    $("#auth-consent").scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }

  submit.disabled = true;
  try {
    const result = state.authMode === "register"
      ? await api.register({
          name: $("#auth-name").value.trim() || email.split("@")[0],
          email,
          password,
          consent: { ...state.consent, policy_version: state.notice?.version || "" },
        })
      : await api.login({ email, password });
    setToken(result.access_token);
    state.user = result.user;
    await enterApp();
  } catch (err) {
    errorEl.textContent = err instanceof ApiError ? err.message : "Could not reach the server.";
    errorEl.classList.remove("hidden");
  } finally {
    submit.disabled = false;
  }
}

/* ================= shell ================= */

async function enterApp() {
  stopParticles();
  $("#auth-screen").classList.add("hidden");
  $("#app-shell").classList.remove("hidden");
  $("#user-avatar").textContent = (state.user.name || "?")
    .split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  if (!state.surveySpec) {
    try { state.surveySpec = await api.getSurveySpec(); } catch { state.surveySpec = []; }
  }
  if (state.user.has_survey && !state.surveyAnswers) {
    try { state.surveyAnswers = (await api.getSurvey()).answers; } catch { /* fine */ }
  }
  // Restore this user's assistant thread, if the tab still has one.
  chatLoad();

  // New users take the survey first; returning users land on the dashboard.
  setView(state.user.has_survey ? "tasks" : "survey");
}

function setView(view) {
  state.view = view;
  $$("#app-nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  ({ tasks: renderTasks, decide: renderDecide, insights: renderInsights,
     history: renderHistory, survey: renderSurvey })[view]();
}

/* ================= tasks ================= */

function syncCategoryFields() {
  const category = $("#task-category").value;
  $("#study-fields").classList.toggle("hidden", category !== "Study");
  // Travel is planned by the day - no timestamp needed on its due date.
  duePickerSetMode(category === "Travel" ? "date" : "datetime");
}

/* ============== due-date picker ==============
   A hand-rolled calendar. The native datetime-local popup is a different
   widget in every browser - cramped, unthemeable, and it pairs the month grid
   with a scrolling column of numbers that reads as noise. This renders a
   normal month: weekday header, six fixed rows so the panel never jumps, a
   month/year jump screen, and a separate time row. The chosen value is
   written back into the hidden #task-due input as "YYYY-MM-DDTHH:mm"
   (or "YYYY-MM-DD" in date-only mode), exactly what the form read before. */

const DP_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DP_MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
const DP_PRESETS = [
  { label: "9 AM", h: 9, m: 0 },
  { label: "Noon", h: 12, m: 0 },
  { label: "6 PM", h: 18, m: 0 },
  { label: "11:59 PM", h: 23, m: 59 },
];

const dp = {
  mode: "datetime",  // datetime | date
  date: null,        // selected day, midnight-local, or null
  hour: 18,
  minute: 0,
  cursor: startOfMonth(new Date()),
  screen: "days",    // days | months
  open: false,
};

function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
function sameDay(a, b) {
  return !!a && !!b && a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function pad2(n) { return String(n).padStart(2, "0"); }

/* 12-hour clock parts for the time row. */
function dpClock() {
  const h24 = dp.hour;
  return { h12: h24 % 12 === 0 ? 12 : h24 % 12, ampm: h24 < 12 ? "AM" : "PM" };
}

function duePickerSetMode(mode) {
  if (dp.mode === mode) return;
  dp.mode = mode;
  duePickerClear();
}

function duePickerClear() {
  dp.date = null;
  dp.hour = 18;
  dp.minute = 0;
  dp.cursor = startOfMonth(new Date());
  dpCommit();
  if (dp.open) dpRender(".dp-day.dp-today, .dp-title");
}

/* Push the current selection into the hidden input + the trigger label. */
function dpCommit() {
  const input = $("#task-due");
  const display = $("#task-due-display");
  if (!dp.date) {
    input.value = "";
    display.textContent = dp.mode === "date" ? "Pick a day" : "Pick a date and time";
    display.classList.add("dp-empty");
    return;
  }
  const d = dp.date;
  const ymd = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  input.value = dp.mode === "date" ? ymd : `${ymd}T${pad2(dp.hour)}:${pad2(dp.minute)}`;
  display.classList.remove("dp-empty");
  // The year is only worth the width when it isn't the current one - without
  // this the label outgrows the sidebar field and ellipsises mid-time.
  const opts = { weekday: "short", day: "numeric", month: "short" };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  let text = d.toLocaleDateString(undefined, opts);
  if (dp.mode !== "date") {
    const { h12, ampm } = dpClock();
    text += ` \u00b7 ${h12}:${pad2(dp.minute)} ${ampm}`;
  }
  display.textContent = text;
  $("#task-due-trigger").classList.remove("invalid");
}

function dpDayCells() {
  const first = dp.cursor;
  const start = new Date(first);
  start.setDate(1 - first.getDay());          // back up to the Sunday
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

function dpRenderDays() {
  const today = new Date();
  const month = dp.cursor.getMonth();
  const head = DP_WEEKDAYS
    .map((w) => `<span class="dp-wd">${w.slice(0, 2)}</span>`)
    .join("");
  const cells = dpDayCells().map((d) => {
    const cls = ["dp-day"];
    if (d.getMonth() !== month) cls.push("dp-out");
    if (sameDay(d, today)) cls.push("dp-today");
    if (sameDay(d, dp.date)) cls.push("dp-sel");
    const iso = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    const label = d.toLocaleDateString(undefined,
      { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    return `<button type="button" class="${cls.join(" ")}" data-day="${iso}"
      aria-label="${label}"${sameDay(d, dp.date) ? ' aria-current="date"' : ""}
      tabindex="-1">${d.getDate()}</button>`;
  }).join("");
  return `<div class="dp-cal">
      <div class="dp-weekdays" aria-hidden="true">${head}</div>
      <div class="dp-grid">${cells}</div>
    </div>`;
}

function dpRenderMonths() {
  const year = dp.cursor.getFullYear();
  const now = new Date();
  const cells = DP_MONTHS.map((name, i) => {
    const cls = ["dp-month"];
    if (i === dp.cursor.getMonth()) cls.push("dp-sel");
    if (i === now.getMonth() && year === now.getFullYear()) cls.push("dp-today");
    return `<button type="button" class="${cls.join(" ")}" data-month="${i}"
      aria-label="${name} ${year}">${name.slice(0, 3)}</button>`;
  }).join("");
  return `<div class="dp-months">${cells}</div>`;
}

function dpRenderTime() {
  if (dp.mode === "date") return "";
  const { h12, ampm } = dpClock();
  const hours = Array.from({ length: 12 }, (_, i) => i + 1)
    .map((h) => `<option value="${h}"${h === h12 ? " selected" : ""}>${h}</option>`).join("");
  const mins = Array.from({ length: 12 }, (_, i) => i * 5)
    .map((m) => `<option value="${m}"${m === dp.minute ? " selected" : ""}>${pad2(m)}</option>`)
    .join("");
  // A minute set by a preset (11:59) is not on the 5-minute grid.
  const odd = dp.minute % 5
    ? `<option value="${dp.minute}" selected>${pad2(dp.minute)}</option>` : "";
  const presets = DP_PRESETS.map((preset) => {
    const on = preset.h === dp.hour && preset.m === dp.minute;
    return `<button type="button" class="dp-preset${on ? " on" : ""}"
      data-h="${preset.h}" data-m="${preset.m}">${preset.label}</button>`;
  }).join("");
  return `<div class="dp-time">
      <span class="dp-time-label">Time</span>
      <div class="dp-time-controls">
        <select class="dp-h" aria-label="Hour">${hours}</select>
        <span class="dp-colon">:</span>
        <select class="dp-m" aria-label="Minute">${mins}${odd}</select>
        <div class="dp-ampm" role="group" aria-label="AM or PM">
          <button type="button" data-ampm="AM" class="${ampm === "AM" ? "on" : ""}">AM</button>
          <button type="button" data-ampm="PM" class="${ampm === "PM" ? "on" : ""}">PM</button>
        </div>
      </div>
      <div class="dp-presets">${presets}</div>
    </div>`;
}

function dpRender(focusSel) {
  const pop = $("#task-due-pop");
  const months = dp.screen === "months";
  const title = months
    ? dp.cursor.getFullYear()
    : `${DP_MONTHS[dp.cursor.getMonth()]} ${dp.cursor.getFullYear()}`;
  pop.innerHTML = `
    <div class="dp-head">
      <button type="button" class="dp-nav" data-step="-1"
        aria-label="${months ? "Previous year" : "Previous month"}">&#8249;</button>
      <button type="button" class="dp-title" data-screen aria-live="polite">
        ${title}<span class="dp-caret" aria-hidden="true">${months ? "&#9650;" : "&#9660;"}</span>
      </button>
      <button type="button" class="dp-nav" data-step="1"
        aria-label="${months ? "Next year" : "Next month"}">&#8250;</button>
    </div>
    <div class="dp-body">
      ${months ? dpRenderMonths() : dpRenderDays()}
      ${months ? "" : dpRenderTime()}
    </div>
    <div class="dp-foot">
      <button type="button" class="dp-link" data-act="clear">Clear</button>
      <button type="button" class="dp-link" data-act="today">Today</button>
      <button type="button" class="dp-done" data-act="done">Done</button>
    </div>`;
  // Re-rendering drops whatever had focus; hand it back to the equivalent
  // control so the keyboard flow survives.
  if (focusSel) pop.querySelector(focusSel)?.focus();
  if (dp.open) dpPlace();
}

function dpOpen() {
  if (dp.open) return;
  dp.open = true;
  dp.screen = "days";
  dp.cursor = startOfMonth(dp.date || new Date());
  $("#task-due-pop").classList.remove("hidden");
  $("#task-due-trigger").setAttribute("aria-expanded", "true");
  dpRender(".dp-day.dp-sel, .dp-day.dp-today");
  dpPlace();
}

/* Pin the panel to the viewport next to the field: below it by default, above
   it when there is more room there, and never taller than the space it has.
   Anything that doesn't fit scrolls inside .dp-body, so the month header and
   the footer buttons stay on screen even on a short window. */
const DP_GAP = 8;    // breathing room between the field and the panel
const DP_EDGE = 10;  // keep this far clear of the window edges
// Below this there isn't room for the header, four week rows and the footer,
// and squeezing the panel in beside the field starts to cover it.
const DP_COMFORT = 300;
// Narrower than this there is no room to stand the time controls beside the
// calendar, so the panel stays stacked and scrolls instead.
const DP_WIDE_MIN = 720;

function dpPlace() {
  const pop = $("#task-due-pop");
  // Under 560px the panel is a bottom sheet positioned entirely by CSS.
  if (window.matchMedia("(max-width: 560px)").matches) {
    pop.style.cssText = "";
    pop.classList.remove("dp-center");
    return;
  }
  const r = $("#task-due-trigger").getBoundingClientRect();
  const below = window.innerHeight - r.bottom - DP_GAP - DP_EDGE;
  const above = r.top - DP_GAP - DP_EDGE;
  const centred = window.innerHeight - DP_EDGE * 2;
  // Round up: the panel's real height isn't a whole number, and measuring low
  // here clips the last week row by a pixel or two.
  const measure = () => Math.ceil(pop.getBoundingClientRect().height);

  pop.style.maxHeight = "none";
  pop.classList.remove("dp-wide");
  let wanted = measure();
  // Stacked, the calendar and the time controls make a panel too tall for a
  // half-height window. Standing them side by side is ~130px shorter, which is
  // usually the difference between sitting beside the field and covering it.
  if (wanted > Math.max(above, below) && window.innerWidth >= DP_WIDE_MIN) {
    pop.classList.add("dp-wide");
    wanted = measure();
  }
  const fitsBelow = wanted <= below;
  const fitsAbove = wanted <= above;

  // Beside the field when the whole panel fits there. Otherwise centre it like
  // a dialog if that shows the full month, or if both sides are so short that
  // anchoring would leave a two-row sliver - covering the field beats that.
  if (!fitsBelow && !fitsAbove &&
      (wanted <= centred || Math.max(above, below) < DP_COMFORT)) {
    pop.classList.add("dp-center");
    pop.style.top = "";
    pop.style.left = "";
    pop.style.maxHeight = `${centred}px`;
    return;
  }
  pop.classList.remove("dp-center");
  const up = !fitsBelow && (fitsAbove || above > below);
  const height = Math.min(wanted, up ? above : below);
  pop.style.maxHeight = `${height}px`;
  pop.style.top = up
    ? `${Math.max(DP_EDGE, r.top - DP_GAP - height)}px`
    : `${Math.min(r.bottom + DP_GAP, Math.max(DP_EDGE, window.innerHeight - height - DP_EDGE))}px`;
  pop.style.left =
    `${Math.max(DP_EDGE, Math.min(r.left, window.innerWidth - pop.offsetWidth - DP_EDGE))}px`;
}

function dpClose({ focus = false } = {}) {
  if (!dp.open) return;
  dp.open = false;
  $("#task-due-pop").classList.add("hidden");
  $("#task-due-trigger").setAttribute("aria-expanded", "false");
  if (focus) $("#task-due-trigger").focus();
}

/* Arrow keys walk the grid; the month follows the selection across edges. */
function dpMoveSelection(days) {
  const from = dp.date || new Date();
  const next = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  dp.date = next;
  dp.cursor = startOfMonth(next);
  dpCommit();
  dpRender(".dp-day.dp-sel");
}

function dpHandleClick(e) {
  // The re-renders below detach e.target, which would make the document-level
  // "clicked outside?" test read the click as outside and close the panel.
  e.dpInside = true;
  const nav = e.target.closest(".dp-nav");
  if (nav) {
    const step = Number(nav.dataset.step);
    dp.cursor = dp.screen === "months"
      ? new Date(dp.cursor.getFullYear() + step, dp.cursor.getMonth(), 1)
      : new Date(dp.cursor.getFullYear(), dp.cursor.getMonth() + step, 1);
    dpRender(`.dp-nav[data-step="${step}"]`);
    return;
  }
  if (e.target.closest(".dp-title")) {
    dp.screen = dp.screen === "months" ? "days" : "months";
    dpRender(".dp-title");
    return;
  }
  const month = e.target.closest(".dp-month");
  if (month) {
    dp.cursor = new Date(dp.cursor.getFullYear(), Number(month.dataset.month), 1);
    dp.screen = "days";
    dpRender(".dp-day.dp-sel, .dp-day.dp-today, .dp-title");
    return;
  }
  const day = e.target.closest(".dp-day");
  if (day) {
    const [y, m, d] = day.dataset.day.split("-").map(Number);
    dp.date = new Date(y, m - 1, d);
    dp.cursor = startOfMonth(dp.date);
    dpCommit();
    // Date-only mode has nothing left to choose, so get out of the way.
    if (dp.mode === "date") { dpClose({ focus: true }); return; }
    dpRender(".dp-day.dp-sel");
    return;
  }
  const preset = e.target.closest(".dp-preset");
  if (preset) {
    dp.hour = Number(preset.dataset.h);
    dp.minute = Number(preset.dataset.m);
    if (!dp.date) dp.date = dpToday();
    dpCommit();
    dpRender(`.dp-preset[data-h="${dp.hour}"][data-m="${dp.minute}"]`);
    return;
  }
  const ampm = e.target.closest("[data-ampm]");
  if (ampm) {
    const want = ampm.dataset.ampm;
    if (want !== dpClock().ampm) dp.hour = (dp.hour + 12) % 24;
    if (dp.date) dpCommit();
    dpRender(`[data-ampm="${want}"]`);
    return;
  }
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "clear") { duePickerClear(); return; }
  if (act === "today") {
    dp.date = dpToday();
    dp.cursor = startOfMonth(dp.date);
    dp.screen = "days";
    dpCommit();
    if (dp.mode === "date") { dpClose({ focus: true }); return; }
    dpRender(".dp-day.dp-sel");
    return;
  }
  if (act === "done") dpClose({ focus: true });
}

function dpToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function dpHandleChange(e) {
  if (e.target.matches(".dp-h")) {
    const h12 = Number(e.target.value) % 12;
    dp.hour = dpClock().ampm === "PM" ? h12 + 12 : h12;
  } else if (e.target.matches(".dp-m")) {
    dp.minute = Number(e.target.value);
  } else return;
  if (!dp.date) dp.date = dpToday();
  dpCommit();
}

function bindDuePicker() {
  const pop = $("#task-due-pop");
  // The form sits in .task-form-col, which is position:sticky - and a sticky
  // element creates a stacking context, trapping the panel underneath the
  // app header no matter how high its z-index goes. It is position:fixed and
  // placed by script anyway, so it lives on <body> instead.
  document.body.appendChild(pop);
  $("#task-due-trigger").addEventListener("click", () => (dp.open ? dpClose() : dpOpen()));
  pop.addEventListener("click", dpHandleClick);
  pop.addEventListener("change", dpHandleChange);
  document.addEventListener("click", (e) => {
    if (dp.open && !e.dpInside &&
        !e.target.closest("#task-due-picker, #task-due-pop")) dpClose();
  });
  // The form sits in a sticky column, so the field moves as the page scrolls.
  // Capture phase, so scrolling inside .dp-body is caught too and ignored.
  window.addEventListener("scroll", (e) => {
    if (!dp.open) return;
    if (e.target instanceof Element && e.target.closest("#task-due-pop")) return;
    dpPlace();
  }, true);
  window.addEventListener("resize", () => { if (dp.open) dpPlace(); });
  // On the document, not the field: the panel is no longer a descendant of it.
  document.addEventListener("keydown", (e) => {
    if (!dp.open || !e.target.closest?.("#task-due-picker, #task-due-pop")) return;
    if (e.key === "Escape") {
      e.stopPropagation();          // don't also close the decision modal
      dpClose({ focus: true });
      return;
    }
    if (dp.screen !== "days" || e.target.closest("select")) return;
    const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    if (e.key in steps) { e.preventDefault(); dpMoveSelection(steps[e.key]); }
  });
  dpCommit();
}

async function handleTaskSubmit(event) {
  event.preventDefault();
  const errorEl = $("#task-error");
  errorEl.classList.add("hidden");
  const category = $("#task-category").value;
  if (!$("#task-due").value) {
    errorEl.textContent = "Please pick a due date.";
    errorEl.classList.remove("hidden");
    $("#task-due-trigger").classList.add("invalid");
    $("#task-due-trigger").focus();
    return;
  }
  const dueRaw = $("#task-due").value;
  // Date-only (Travel): treat as end of that day so urgency isn't inflated.
  const due = dueRaw.includes("T") ? new Date(dueRaw) : new Date(`${dueRaw}T23:59`);
  const payload = {
    title: $("#task-title").value,
    category,
    due_date: due.toISOString(),
  };
  if (category === "Study") {
    payload.estimated_minutes = parseInt($("#task-estimate").value, 10) || null;
  }
  try {
    await api.createTask(payload);
    $("#task-form").reset();
    $("#task-estimate").value = 60;
    duePickerClear();
    syncCategoryFields();
    toast("Task added - ranking updated");
    renderTasks();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  }
}

function taskMeta(task) {
  const bits = [
    `<span class="cat-tag cat-${task.category}">${task.category}</span>`,
    fmtDate(task.due_date, task.category === "Travel"),
  ];
  if (task.status === "pending") bits.push(dueLabel(task.due_date));
  if (task.estimated_minutes != null) bits.push(`${minutesLabel(task.estimated_minutes)} est.`);
  if (task.decided_option) {
    bits.push(`<span class="cat-tag decided-tag">Decided: ${escapeHtml(task.decided_option)}</span>`);
  }
  return bits.join(" <span aria-hidden='true'>·</span> ");
}

function taskActions(task) {
  const primary = task.status === "pending"
    ? `<button class="icon-btn" data-act="complete" data-id="${task.id}" title="Mark done" aria-label="Mark done">${ICONS.check}</button>`
    : `<button class="icon-btn" data-act="reopen" data-id="${task.id}" title="Reopen" aria-label="Reopen">${ICONS.undo}</button>`;
  return `<div class="task-actions">${primary}
    <button class="icon-btn danger" data-act="delete" data-id="${task.id}" title="Delete" aria-label="Delete">${ICONS.trash}</button></div>`;
}

function emptyState(title, hint) {
  return `<div class="empty"><strong>${title}</strong>${hint}</div>`;
}

/* ---- Eisenhower matrix ----
   The backend places every task from the same urgency (U) and importance (C)
   factors that drive the ranking, so the grid and the list can never tell two
   different stories. See scoring.classify. */

const QUADRANT_ORDER = ["do", "schedule", "delegate", "eliminate"];

function matrixTask(task) {
  const moved = task.quadrant_overridden;
  const home = state.quadrantTitles[task.computed_quadrant] || "where it was";
  return `
    <div class="eis-task${moved ? " moved" : ""}" data-open="${task.id}" data-task="${task.id}"
         title="Drag to another quadrant · click to open the decision workspace">
      <span class="eis-rank">#${task.rank}</span>
      <span class="eis-task-title">${escapeHtml(task.title)}</span>
      <span class="eis-task-due">${task.status === "pending" ? dueLabel(task.due_date) : ""}</span>
      <span class="score-chip ${scoreClass(task.score)}">${task.score.toFixed(0)}%</span>
      ${moved ? `<button class="eis-reset" data-act="reset-quadrant" data-id="${task.id}"
          aria-label="Move back to ${escapeHtml(home)}"
          title="You moved this. The engine put it in ${escapeHtml(home)} - click to hand it back.">↺</button>` : ""}
    </div>`;
}

/* ---- drag a task between quadrants ----
   Pointer events rather than HTML5 drag-and-drop, which does not fire on
   touch at all. A mouse starts dragging after 6px of travel; a finger has to
   hold still for a moment first, so the page can still be scrolled by
   swiping across a task. Either way a plain click still opens the task. */

const DRAG_MOVE_THRESHOLD = 6;   // px before a mouse press becomes a drag
const DRAG_TOUCH_HOLD_MS = 320;  // press-and-hold before a finger drags

let drag = null;
let dragEndedAt = 0;  // lets the click handler ignore the click after a drop

function blockTouchScroll(event) {
  if (drag && drag.active) event.preventDefault();
}

/* Edge auto-scroll. On a phone the quadrants stack, so the one you are aiming
   for is usually below the fold - without this you simply cannot reach it.
   Driven by rAF rather than by pointermove, so holding still at the edge keeps
   scrolling instead of stalling. */
const DRAG_EDGE_ZONE = 90;   // px from the viewport edge where scrolling starts
const DRAG_EDGE_SPEED = 18;  // px per frame at the very edge

function autoScrollTick() {
  if (!drag || !drag.active) return;
  const y = drag.lastY;
  const fromTop = y - DRAG_EDGE_ZONE;
  const fromBottom = window.innerHeight - DRAG_EDGE_ZONE - y;
  let delta = 0;
  if (fromTop < 0) delta = -DRAG_EDGE_SPEED * Math.min(1, -fromTop / DRAG_EDGE_ZONE);
  else if (fromBottom < 0) delta = DRAG_EDGE_SPEED * Math.min(1, -fromBottom / DRAG_EDGE_ZONE);
  if (delta) {
    window.scrollBy(0, delta);
    refreshDropTarget();  // the cell under the finger changed without it moving
  }
  drag.raf = requestAnimationFrame(autoScrollTick);
}

function refreshDropTarget() {
  const cell = cellUnder(drag.lastX, drag.lastY);
  $$(".eis-cell.drop-target").forEach((c) => {
    if (c !== cell) c.classList.remove("drop-target");
  });
  if (cell && cell !== drag.fromCell) cell.classList.add("drop-target");
  drag.target = cell;
}

function endDrag() {
  if (!drag) return;
  clearTimeout(drag.holdTimer);
  cancelAnimationFrame(drag.raf);
  drag.ghost?.remove();
  drag.chip.classList.remove("is-dragging");
  document.body.classList.remove("eis-dragging");
  $$(".eis-cell.drop-target").forEach((c) => c.classList.remove("drop-target"));
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", onDragEnd);
  window.removeEventListener("pointercancel", onDragEnd);
  window.removeEventListener("touchmove", blockTouchScroll);
  const finished = drag;
  drag = null;
  return finished;
}

function beginDrag(x, y) {
  const box = drag.chip.getBoundingClientRect();
  drag.active = true;
  drag.offsetX = x - box.left;
  drag.offsetY = y - box.top;

  const ghost = drag.chip.cloneNode(true);
  ghost.classList.add("eis-ghost");
  ghost.style.width = `${box.width}px`;
  document.body.appendChild(ghost);
  drag.ghost = ghost;

  drag.chip.classList.add("is-dragging");
  document.body.classList.add("eis-dragging");
  drag.lastX = x;
  drag.lastY = y;
  positionGhost(x, y);
  drag.raf = requestAnimationFrame(autoScrollTick);
}

function positionGhost(x, y) {
  drag.ghost.style.transform =
    `translate(${x - drag.offsetX}px, ${y - drag.offsetY}px)`;
}

function cellUnder(x, y) {
  // The ghost is pointer-events:none, so this hit-tests through it.
  return document.elementFromPoint(x, y)?.closest(".eis-cell") || null;
}

function onDragMove(event) {
  if (!drag) return;
  const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
  if (!drag.active) {
    // A finger that moves before the hold timer fires is scrolling, not dragging.
    if (drag.touch) {
      if (distance > DRAG_MOVE_THRESHOLD) endDrag();
      return;
    }
    if (distance <= DRAG_MOVE_THRESHOLD) return;
    beginDrag(event.clientX, event.clientY);
  }
  event.preventDefault();
  drag.lastX = event.clientX;
  drag.lastY = event.clientY;
  positionGhost(event.clientX, event.clientY);
  refreshDropTarget();
}

function onDragEnd(event) {
  if (!drag) return;
  const wasActive = drag.active;
  const target = drag.target;
  const taskId = drag.taskId;
  const from = drag.fromCell;
  endDrag();
  if (!wasActive) return;

  dragEndedAt = Date.now();  // swallow the click this pointerup is about to fire
  if (target && target !== from) {
    moveTaskToQuadrant(taskId, target.dataset.quadrant);
  }
}

function onDragStart(event) {
  if (event.button > 0) return;                  // left button / primary touch only
  if (event.target.closest("button")) return;    // the reset button is not a handle
  const chip = event.target.closest(".eis-task");
  if (!chip) return;

  drag = {
    chip,
    taskId: chip.dataset.task,
    fromCell: chip.closest(".eis-cell"),
    startX: event.clientX,
    startY: event.clientY,
    active: false,
    target: null,
    lastX: event.clientX,
    lastY: event.clientY,
    raf: 0,
    touch: event.pointerType !== "mouse",
    holdTimer: null,
  };
  if (drag.touch) {
    drag.holdTimer = setTimeout(
      () => drag && beginDrag(event.clientX, event.clientY), DRAG_TOUCH_HOLD_MS);
  }
  window.addEventListener("pointermove", onDragMove, { passive: false });
  window.addEventListener("pointerup", onDragEnd);
  window.addEventListener("pointercancel", onDragEnd);
  // preventDefault on touchmove is what actually stops the page scrolling;
  // touch-action alone cannot be changed reliably mid-gesture.
  window.addEventListener("touchmove", blockTouchScroll, { passive: false });
}

async function moveTaskToQuadrant(taskId, quadrant) {
  const task = state.taskIndex[taskId];
  if (task && task.quadrant === quadrant) return;
  try {
    await api.setQuadrant(taskId, quadrant);
    toast(quadrant === task?.computed_quadrant
      ? "Back where the engine had it"
      : `Moved to ${state.quadrantTitles[quadrant] || "that quadrant"} - score unchanged`);
  } catch (err) {
    toast(err.message);
  }
  renderTasks();
}

function matrixCell(quadrant, byId) {
  const tasks = quadrant.task_ids.map((id) => byId[id]).filter(Boolean);
  return `
    <section class="eis-cell q-${quadrant.key}" data-quadrant="${quadrant.key}">
      <span class="eis-count" title="${tasks.length} task(s)">${tasks.length}</span>
      <h4 class="eis-cell-title">${escapeHtml(quadrant.title)}</h4>
      <p class="eis-rule">${escapeHtml(quadrant.rule)}</p>
      <div class="eis-tasks">
        ${tasks.map(matrixTask).join("") ||
          `<p class="eis-empty">Nothing here. Drop a task in.</p>`}
      </div>
      <details class="eis-why">
        <summary>Why this quadrant</summary>
        <p>${escapeHtml(quadrant.science)}</p>
        <cite>${escapeHtml(quadrant.source)}</cite>
      </details>
    </section>`;
}

function renderMatrix(data) {
  const byId = Object.fromEntries(data.tasks.map((t) => [t.id, t]));
  // Titles come from the backend; cache them so a moved task can name the
  // quadrant the engine had picked for it.
  state.quadrantTitles = Object.fromEntries(
    (data.matrix || []).map((q) => [q.key, q.title]));
  const cells = Object.fromEntries(
    (data.matrix || []).map((q) => [q.key, matrixCell(q, byId)]));
  if (!data.tasks.length) {
    return emptyState("Nothing to place",
      "Add a task on the left and it lands in a quadrant instantly.");
  }
  const moved = data.tasks.filter((t) => t.quadrant_overridden).length;
  return `
    <div class="eis">
      <h3 class="eis-title">The Eisenhower Decision Matrix</h3>
      <p class="eis-legend">
        Urgent means the deadline is inside 48 hours (or the time left barely
        covers the estimate). Important is the category's weight in your profile.
        <strong>Drag any task into another quadrant</strong> when you know
        something the engine doesn't - that moves where it sits, never what it
        scores.${moved ? ` You have moved ${moved} task${moved > 1 ? "s" : ""};
        each one carries a ↺ to hand it back.` : ""}
      </p>
      <div class="eis-grid">
        <span class="eis-corner"></span>
        <span class="eis-col-label">Urgent</span>
        <span class="eis-col-label">Not Urgent</span>

        <span class="eis-row-label">Important</span>
        ${cells.do || ""}
        ${cells.schedule || ""}

        <span class="eis-row-label">Not Important</span>
        ${cells.delegate || ""}
        ${cells.eliminate || ""}
      </div>
      ${data.matrix_notes && data.matrix_notes.length ? `
        <div class="eis-notes">
          <h3 class="card-title">What the shape of this grid says</h3>
          ${bsList(data.matrix_notes)}
        </div>` : ""}
    </div>`;
}

async function renderTasks() {
  const list = $("#task-list");
  const matrix = $("#task-matrix");
  const isMatrix = state.taskLayout === "matrix";

  // The matrix is a view of the *ranking*, which only covers pending tasks -
  // so the status filter has nothing to filter and is hidden while it is open.
  $("#task-filter").classList.toggle("hidden", isMatrix);
  list.classList.toggle("hidden", isMatrix);
  matrix.classList.toggle("hidden", !isMatrix);

  if (isMatrix) {
    matrix.innerHTML = `<div class="empty">Loading...</div>`;
    try {
      const data = await api.prioritized();
      data.tasks.forEach((t) => { state.taskIndex[t.id] = t; });
      matrix.innerHTML = renderMatrix(data);
    } catch (err) {
      matrix.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
    }
    return;
  }

  list.innerHTML = `<div class="empty">Loading...</div>`;
  try {
    let html = "";
    if (state.taskFilter === "pending") {
      const data = await api.prioritized();
      data.tasks.forEach((t) => { state.taskIndex[t.id] = t; });
      html = data.tasks.map((t) => `
        <div class="task-item clickable ${t.rank === 1 ? "top" : ""}" data-open="${t.id}" title="Open decision workspace">
          <div class="rank-badge">#${t.rank}</div>
          <div class="task-main">
            <div class="task-title">${escapeHtml(t.title)}</div>
            <div class="task-meta">${taskMeta(t)}</div>
          </div>
          <div class="score-chip ${scoreClass(t.score)}">${t.score.toFixed(0)}%</div>
          ${taskActions(t)}
        </div>`).join("");
      if (!html) html = emptyState("Nothing on your plate", "Add a task on the left - the engine ranks it instantly.");
    } else {
      const tasks = await api.listTasks(state.taskFilter);
      tasks.forEach((t) => { state.taskIndex[t.id] = t; });
      html = tasks.map((t) => `
        <div class="task-item ${t.status === "done" ? "done" : "clickable"}" ${t.status === "done" ? "" : `data-open="${t.id}"`}>
          <div class="rank-badge">${t.status === "done" ? "✓" : "•"}</div>
          <div class="task-main">
            <div class="task-title">${escapeHtml(t.title)}</div>
            <div class="task-meta">${taskMeta(t)}</div>
          </div>
          ${taskActions(t)}
        </div>`).join("");
      if (!html) html = emptyState("Nothing here yet", "Completed tasks will show up in this list.");
    }
    list.innerHTML = html;
  } catch (err) {
    list.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

async function handleTaskAction(event) {
  // A drop fires a click on pointerup; that click is not a real one.
  if (Date.now() - dragEndedAt < 350) return;
  const btn = event.target.closest("button[data-act]");
  if (!btn) {
    // Clicking the card itself opens the per-task decision workspace.
    const card = event.target.closest("[data-open]");
    if (card) {
      const task = state.taskIndex[card.dataset.open];
      if (task) openDecision(task);
    }
    return;
  }
  event.stopPropagation();
  const { act, id } = btn.dataset;
  try {
    if (act === "reset-quadrant") {
      await api.setQuadrant(id, null);
      toast("Handed back to the engine");
    }
    if (act === "complete") { await api.completeTask(id); toast("Done. Nice work."); }
    if (act === "reopen") { await api.reopenTask(id); toast("Task reopened"); }
    if (act === "delete") {
      if (!confirm("Delete this task permanently?")) return;
      await api.deleteTask(id);
      toast("Task deleted");
    }
    if (state.view === "tasks") renderTasks();
    if (state.view === "decide") renderDecide();
  } catch (err) {
    toast(err.message);
  }
}

/* ================= decide ================= */

const FACTOR_LABELS = { urgency: "Urgency", importance: "Importance", effort: "Effort", aging: "Aging" };

function factorBars(factors) {
  return `<div class="factor-bars">` + Object.entries(FACTOR_LABELS).map(([key, label]) => `
    <div class="fbar">${label} ${(factors[key] * 100).toFixed(0)}%
      <div class="track"><div class="fill" style="width:${Math.min(factors[key] * 100, 100)}%"></div></div>
    </div>`).join("") + `</div>`;
}

function scoreRing(score) {
  const R = 52, C = 2 * Math.PI * R;
  const offset = C * (1 - Math.min(score, 100) / 100);
  return `
  <div class="ring" role="img" aria-label="Priority score ${score.toFixed(0)} percent">
    <svg width="120" height="120" viewBox="0 0 120 120">
      <circle class="bg" cx="60" cy="60" r="${R}" stroke-width="9" fill="none"/>
      <circle class="fg" cx="60" cy="60" r="${R}" stroke-width="9" fill="none"
        stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>
    </svg>
    <span class="val">${score.toFixed(0)}%</span>
  </div>`;
}

async function renderDecide() {
  const hero = $("#up-next");
  const list = $("#decide-list");
  hero.innerHTML = "";
  list.innerHTML = `<div class="empty">Loading...</div>`;
  try {
    const data = await api.prioritized();
    if (!data.tasks.length) {
      list.innerHTML = emptyState("Nothing to decide", "Add tasks first - then Decide Well tells you what to start.");
      return;
    }
    const [top, ...rest] = data.tasks;
    data.tasks.forEach((t) => { state.taskIndex[t.id] = t; });
    const pct = Math.round(data.clarity * 100);
    hero.innerHTML = `
      <div class="up-next clickable" data-open="${top.id}" title="Open decision workspace">
        ${scoreRing(top.score)}
        <div>
          <div class="up-next-kicker">UP NEXT ${top.decided_option ? `· DECIDED: ${escapeHtml(top.decided_option.toUpperCase())}` : ""}</div>
          <div class="up-next-title">${escapeHtml(top.title)}</div>
          <div class="up-next-meta">${taskMeta(top)}</div>
          <div class="up-next-reason">${escapeHtml(top.reason)}</div>
        </div>
        <button class="btn-done" data-act="complete" data-id="${top.id}">Mark done</button>
      </div>
      <p class="clarity-note">${
        pct >= 25
          ? `Clear call - the top task leads the runner-up by ${pct}%.`
          : `Close call - the top two are within ${pct}% of each other. Momentum breaks the tie.`
      }</p>`;

    list.innerHTML = rest.map((t) => `
      <div class="task-item clickable" data-open="${t.id}" style="align-items:flex-start" title="Open decision workspace">
        <div class="rank-badge">#${t.rank}</div>
        <div class="task-main">
          <div class="task-title">${escapeHtml(t.title)}</div>
          <div class="task-meta">${taskMeta(t)}</div>
          ${factorBars(t.factors)}
          <div class="reason">${escapeHtml(t.reason)}</div>
        </div>
        <div class="score-chip ${scoreClass(t.score)}">${t.score.toFixed(0)}%</div>
        ${taskActions(t)}
      </div>`).join("") ||
      `<p class="clarity-note">That's your only pending task - no competition today.</p>`;
  } catch (err) {
    list.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

async function handleExplain() {
  const btn = $("#explain-btn");
  const panel = $("#insight-panel");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> Thinking...`;
  try {
    const result = await api.explain();
    panel.classList.remove("hidden");
    $("#insight-source").textContent = sourceLabel(result.source);
    $("#insight-body").innerHTML = renderInsight(result.insight);
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = `Explain with ${AI_NAME}`;
  }
}

/* ================= insights ================= */

function surveyTips(answers) {
  const tips = [];
  if (!answers) {
    return ["Take the survey (Profile tab) to personalize how urgency, importance and effort are weighted."];
  }
  if (answers.motivator === "A deadline" || answers.motivator === "Fear of falling behind" ||
      answers.many_tasks === "Do the most urgent") {
    tips.push("You said deadlines drive you - urgency carries extra weight in your ranking.");
  }
  if (answers.motivator === "A personal goal") {
    tips.push("You're goal-driven, so long-term importance counts for more in your scores.");
  }
  if (answers.many_tasks === "Do the easiest first" || answers.motivator === "A reward") {
    tips.push("You build momentum with small wins - short tasks get a gentle boost so you can get rolling.");
  }
  if (answers.many_tasks === "Freeze and do nothing for a while") {
    tips.push("When you freeze, skip the debate: the Up Next card is pre-decided for you. Just start it.");
  }
  const OFTEN = ["Often", "Very Often"];
  if (OFTEN.includes(answers.delay_start) || OFTEN.includes(answers.stuck_first)) {
    tips.push("You often delay starts - older tasks resurface faster so nothing rots in your backlog.");
  }
  (answers.support_areas || []).slice(0, 3).forEach((area) => {
    tips.push(`You asked for help with ${area.toLowerCase()} - matching tasks rank higher for you.`);
  });
  if (["Less than 4", "4-6"].includes(answers.sleep_hours) ||
      ["Never", "Rarely"].includes(answers.exercise_freq)) {
    tips.push("Your health answers nudge Health tasks upward - small consistent ones count double for you.");
  }
  if (OFTEN.includes(answers.regret)) {
    tips.push("You often wish you'd picked differently - check the factor bars to see why each task is placed where it is.");
  }
  return tips.slice(0, 6);
}

async function renderInsights() {
  const grid = $("#stat-grid");
  grid.innerHTML = "";
  try {
    const s = await api.stats();
    const tiles = [
      { val: s.pending, lbl: "Pending", pct: s.total_tasks ? s.pending / s.total_tasks : 0 },
      { val: `${Math.round(s.completion_rate * 100)}%`, lbl: "Completed", pct: s.completion_rate },
      { val: `${s.avg_score.toFixed(0)}%`, lbl: "Avg priority", pct: s.avg_score / 100 },
      { val: s.overdue, lbl: "Overdue", pct: s.pending ? s.overdue / s.pending : 0 },
      { val: s.due_this_week, lbl: "Due this week", pct: s.pending ? s.due_this_week / s.pending : 0 },
    ];
    grid.innerHTML = tiles.map((t) => `
      <div class="stat-tile"><div class="val">${t.val}</div><div class="lbl">${t.lbl}</div>
        <div class="bar"><i style="width:${Math.min(t.pct * 100, 100)}%"></i></div>
      </div>`).join("");

    const max = Math.max(1, ...Object.values(s.by_category));
    $("#category-bars").innerHTML = Object.entries(s.by_category)
      .sort((a, b) => b[1] - a[1])
      .map(([cat, count]) => `
        <div class="cat-row"><span class="name">${cat}</span>
          <div class="track"><div class="fill" style="width:${(count / max) * 100}%"></div></div>
          <strong>${count}</strong>
        </div>`).join("") || `<p class="muted">No tasks yet.</p>`;

    $("#tips-list").innerHTML = surveyTips(state.surveyAnswers)
      .map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  } catch (err) {
    grid.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
  renderBehavioral();
  renderLearning();
  renderTempo();
}

/* ---- what the loop has learned (Insights tab) ----
   The learned policy is shown in full rather than summarised away. Two
   numbers per parameter: what the engine now believes, and how much of that
   belief is actually switched on. They differ because a belief built on four
   data points is shrunk toward neutral on purpose - and hiding the gap would
   make the level look like a game mechanic instead of the confidence
   interval it really is. */

const EVENT_LABELS = {
  decision_made: "Decision made",
  satisfaction_rated: "You rated a decision",
  ratings_corrected: "You corrected the AI ratings",
  decision_redone: "Decision re-run",
  task_completed: "Task completed",
  quadrant_override: "Task moved in the matrix",
  chat_question: "You asked AVEX",
};

function learnStat(value, label, hint) {
  return `
    <div class="learn-stat" title="${escapeHtml(hint || "")}">
      <strong>${value}</strong>
      <span>${escapeHtml(label)}</span>
    </div>`;
}

function learnRow(row) {
  const applied = row.applied_pct;
  const believed = row.raw_pct;
  const width = Math.min(50, Math.abs(applied) * 1.6);
  return `
    <div class="learn-row ${row.direction}">
      <span class="learn-row-key">${escapeHtml(row.label)}</span>
      <div class="learn-row-track">
        <i class="learn-row-mid"></i>
        <div class="learn-row-fill" style="width:${width.toFixed(1)}%;
          ${applied >= 0 ? "left:50%" : `left:${(50 - width).toFixed(1)}%`}"></div>
      </div>
      <span class="learn-row-val">
        ${applied >= 0 ? "+" : ""}${applied.toFixed(0)}%
        <em>believes ${believed >= 0 ? "+" : ""}${believed.toFixed(0)}%</em>
      </span>
    </div>`;
}

async function renderLearning() {
  const card = $("#learn-card");
  if (!card) return;
  if (!state.learning) {
    try {
      state.learning = await api.learningProfile();
    } catch {
      card.classList.add("hidden");
      return;
    }
  }
  const p = state.learning;
  card.classList.remove("hidden");

  if (!p.enabled) {
    card.innerHTML = `
      <div class="bs-card-head">
        <div>
          <h3 class="card-title" style="margin-bottom:4px">Learning from your decisions</h3>
          <p class="muted">Switched off for this account.</p>
        </div>
        <span class="tag">Off</span>
      </div>
      <p class="muted">Nothing about how your decisions turn out is being
        recorded, and the engine runs on your survey profile alone. You can
        turn this on under Profile, in Data &amp; privacy - and turn it back
        off, with everything erased, at any time.</p>`;
    return;
  }

  const moved = [...p.tags, ...p.factors].filter((row) => row.moved);

  card.innerHTML = `
    <div class="bs-card-head">
      <div>
        <h3 class="card-title" style="margin-bottom:4px">What the engine has learned about you</h3>
        <p class="muted">${escapeHtml(p.blurb)}</p>
      </div>
      <span class="level-chip">
        <span class="level-dots">${levelDots(p.level, p.max_level)}</span>
        Level ${p.level} · ${escapeHtml(p.label)}
      </span>
    </div>

    <div class="learn-progress">
      <div class="learn-progress-track">
        <div class="learn-progress-fill" style="width:${Math.min(100, p.progress_pct)}%"></div>
      </div>
      <p class="muted">
        ${p.to_next
          ? `${p.to_next} more learning event${p.to_next === 1 ? "" : "s"} to level ${p.level + 1}.`
          : "Top level reached."}
        Right now ${p.trust_pct.toFixed(0)}% of what has been learned is applied
        to your scores - the rest is held back until there is enough evidence
        behind it.
      </p>
    </div>

    <div class="learn-stats">
      ${learnStat(`${p.events}`, "learning events", "Every action the loop scored.")}
      ${learnStat(`${p.rated_decisions}`, "decisions rated", "Decisions you gave an explicit verdict on.")}
      ${learnStat(
        p.avg_satisfaction_pct == null ? "-" : `${p.avg_satisfaction_pct.toFixed(0)}%`,
        "average satisfaction", "Your own average across every rating you have given.")}
      ${learnStat(
        p.follow_rate_pct == null ? "-" : `${p.follow_rate_pct.toFixed(0)}%`,
        "you follow the call", "How often you go with the recommendation.")}
      ${learnStat(
        `${p.calibration_bias_pts >= 0 ? "+" : ""}${p.calibration_bias_pts.toFixed(0)}`,
        "calibration (pts)",
        "How far the satisfaction prediction has run low (+) or high (-). Corrected for automatically.")}
    </div>

    ${moved.length ? `
      <div class="learn-rows">
        <div class="learn-rows-head">
          <span>How your weighting has shifted</span>
          <em>applied now · what it believes</em>
        </div>
        ${moved.map(learnRow).join("")}
      </div>` : `
      <p class="muted">No weight has moved yet. Rate a few decisions and the
        criteria that keep working for you will start to count for more.</p>`}

    <details class="learn-method">
      <summary>How the learning works</summary>
      <p>${escapeHtml(p.method)}</p>
      ${p.recent.length ? `
        <div class="learn-events">
          ${p.recent.slice(0, 8).map((event) => `
            <div class="learn-event">
              <span>${escapeHtml(EVENT_LABELS[event.kind] || event.kind)}</span>
              <em>${fmtDate(event.created_at)}</em>
              <strong class="${(event.reward || 0) >= 0 ? "up" : "down"}">
                ${event.reward == null ? "no reward"
                  : `${event.reward > 0 ? "+" : ""}${event.reward.toFixed(2)}`}
              </strong>
            </div>`).join("")}
        </div>` : ""}
    </details>`;
}

/* ---- data & privacy (Profile tab) ----
   The consent notice promises withdrawal, export, restriction and erasure.
   These are those four promises, wired to the endpoints that keep them. */

const PRIVACY_TOGGLES = [
  ["personalization", "Learn from my decisions",
   "Records how your decisions turn out and tunes the weighting to you. Off means nothing is recorded and nothing is learned."],
  ["ai_processing", "Let AVEX read my task context",
   "Sends task and option wording to Google Gemini for written insights and automatic ratings. Off keeps everything on this server."],
];

async function renderPrivacy() {
  const card = $("#privacy-card");
  if (!card) return;
  let consent;
  try {
    consent = await api.getConsent();
  } catch {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");

  const recent = (consent.history || []).slice(0, 6);
  card.innerHTML = `
    <div class="bs-card-head">
      <div>
        <h3 class="card-title" style="margin-bottom:4px">Data &amp; privacy</h3>
        <p class="muted">Accepted notice v${escapeHtml(consent.policy_version || "-")}
          on ${consent.accepted_at ? fmtDate(consent.accepted_at, true) : "-"}.</p>
      </div>
      ${consent.stale ? `<span class="tag tag-warn">Notice updated</span>`
        : `<span class="tag">Up to date</span>`}
    </div>

    ${consent.stale ? `
      <p class="privacy-stale">The privacy notice has been revised since you
        agreed to it (now v${escapeHtml(consent.current_version)}). Nothing has
        changed in how your data is handled until you accept it.
        <button type="button" class="ghost-btn" data-privacy="accept">Review &amp; accept</button>
      </p>` : ""}

    <div class="privacy-toggles">
      ${PRIVACY_TOGGLES.map(([key, title, blurb]) => `
        <label class="privacy-toggle">
          <input type="checkbox" data-consent-toggle="${key}" ${consent[key] ? "checked" : ""} />
          <span class="privacy-switch" aria-hidden="true"><i></i></span>
          <span class="privacy-toggle-text">
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(blurb)}</span>
          </span>
        </label>`).join("")}
    </div>

    <div class="privacy-actions">
      <button type="button" class="ghost-btn" data-privacy="export">Download my data</button>
      <button type="button" class="ghost-btn" data-privacy="erase">Erase what was learned</button>
      <button type="button" class="ghost-btn danger" data-privacy="delete">Delete my account</button>
    </div>

    ${recent.length ? `
      <details class="privacy-log">
        <summary>Consent history</summary>
        ${recent.map((row) => `
          <div class="privacy-log-row">
            <span>${escapeHtml(row.scope)}</span>
            <strong class="${row.granted ? "up" : "down"}">${row.granted ? "granted" : "withdrawn"}</strong>
            <em>${fmtDate(row.at, true)} · v${escapeHtml(row.policy_version)}</em>
          </div>`).join("")}
      </details>` : ""}`;
}

/* Split by event type on purpose: clicking a toggle fires *both* click and
   change, so sharing one handler would send every consent update twice. */
async function handlePrivacyToggle(event) {
  const toggle = event.target.closest("[data-consent-toggle]");
  if (toggle) {
    const key = toggle.dataset.consentToggle;
    try {
      await api.updateConsent({ [key]: toggle.checked });
      if (state.user?.consent) state.user.consent[key] = toggle.checked;
      state.learning = null;
      toast(toggle.checked ? "Turned on. It applies from your next action."
                           : "Turned off. It stops immediately.");
      if (key === "personalization") renderPrivacy();
    } catch (err) {
      toggle.checked = !toggle.checked;
      toast(err instanceof ApiError ? err.message : "Could not save that.");
    }
  }
}

async function handlePrivacyAction(event) {
  const action = event.target.closest("[data-privacy]")?.dataset.privacy;
  if (!action) return;

  if (action === "accept") {
    await api.updateConsent({ policy_version: state.notice?.version
      || (await api.privacyNotice()).version });
    toast("Thanks - the current notice is on record.");
    return renderPrivacy();
  }

  if (action === "export") {
    // Portability means a file the user actually holds, not a screenful of
    // JSON they would have to copy by hand.
    const data = await api.exportData();
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `decidewell-export-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    return toast("Your data is downloading.");
  }

  if (action === "erase") {
    if (!confirm("Erase everything the engine has learned about you? Your "
      + "account, tasks and decisions stay; only the learning history goes. "
      + "This cannot be undone.")) return;
    const result = await api.eraseLearning();
    state.learning = null;
    toast(`Erased ${result.erased_events} events and ${result.erased_feedback} ratings.`);
    return renderPrivacy();
  }

  if (action === "delete") {
    if (!confirm("Delete your account and everything in it? Tasks, decisions, "
      + "survey and learning history are all erased immediately. This cannot "
      + "be undone.")) return;
    if (!confirm("Last check - this is permanent. Delete the account?")) return;
    await api.deleteAccount();
    setToken(null);
    state.user = null;
    chatClear();
    showAuth();
    toast("Your account and all of its data have been deleted.");
  }
}

/* ---- decision tempo (Insights tab) ----
   Two questions, two charts, deliberately never combined into one plot with
   two y-axes: seconds and counts share no scale, and overlaying them would
   invent a correlation the data does not contain. Small multiples instead.

   Each chart carries a single series, so colour never has to tell two things
   apart; the verdict badges pair their colour with the word, so nothing here
   is encoded by hue alone. */

const TEMPO_MAX_BUCKETS = 14;   // keep the x-axis readable on a phone

/** Seconds as something a person reads without converting in their head. */
function fmtDuration(seconds) {
  if (seconds == null) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (mins < 60) return rest ? `${mins}m ${rest}s` : `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/** Short axis label: the bucket key without the year, which never varies. */
function fmtBucketLabel(label) {
  const [, month, day] = label.replace("w/c ", "").split("-");
  if (!month || !day) return label;
  return `${day}/${month}`;
}

/** Nice round ceiling for an axis, so ticks land on readable numbers. */
function niceCeil(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * magnitude;
}

/** Least-squares line through (i, value) purely for drawing the trend rule.
 *  The *reported* slope is Theil-Sen from the server; this is only the
 *  visual guide, fitted on the same points the reader can see. */
function tempoTrendPoints(values) {
  const n = values.length;
  if (n < 2) return null;
  const meanX = (n - 1) / 2;
  const meanY = values.reduce((a, b) => a + b, 0) / n;
  let sxx = 0, sxy = 0;
  values.forEach((y, i) => { sxx += (i - meanX) ** 2; sxy += (i - meanX) * (y - meanY); });
  if (!sxx) return null;
  const slope = sxy / sxx;
  return [meanY - slope * meanX, meanY + slope * (n - 1 - meanX)];
}

/**
 * One small-multiple panel. `kind` is "line" (time per decision) or "bar"
 * (decisions per bucket) - different marks so the two panels never read as
 * the same measurement twice.
 */
function tempoPanel(rows, { kind, values, width, title, subtitle, unit, granularity }) {
  const H = width < 420 ? 128 : 152;
  const top = niceCeil(Math.max(...values) * 1.15);
  const ticks = [0, top / 2, top];
  const tickText = ticks.map((t) => (unit === "s" ? fmtDuration(t) : String(Math.round(t))));

  // Size the left gutter to the widest tick actually drawn. A fixed gutter
  // clips "3m 20s" on a phone, and the clipped label reads as a bug.
  const longest = Math.max(...tickText.map((t) => t.length));
  const PAD = { t: 14, r: 12, b: 24, l: Math.round(longest * 5.6) + 12 };
  const plotW = Math.max(width - PAD.l - PAD.r, 40);
  const plotH = H - PAD.t - PAD.b;

  const slot = plotW / Math.max(rows.length, 1);
  // Lines sit *on* the edges of the plot (a point scale); bars sit in the
  // middle of their slot (a band scale), or the first and last would be
  // sliced in half by the plot edge.
  const x = (i) => (kind === "bar"
    ? PAD.l + (i + 0.5) * slot
    : PAD.l + (rows.length === 1 ? plotW / 2 : (i / (rows.length - 1)) * plotW));
  const y = (v) => PAD.t + plotH - (v / top) * plotH;

  // Hairline, solid, recessive - three ticks is enough to read a level off.
  const grid = ticks.map((t, ti) => `
    <line class="tp-grid" x1="${PAD.l}" x2="${PAD.l + plotW}" y1="${y(t)}" y2="${y(t)}" />
    <text class="tp-tick" x="${PAD.l - 6}" y="${y(t) + 3.5}" text-anchor="end">${
      escapeHtml(tickText[ti])
    }</text>`).join("");

  // Only the first, middle and last bucket are labelled: on a phone every
  // label would collide, and the tooltip carries the rest.
  const labelAt = new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1]);
  const xLabels = rows.map((r, i) => (labelAt.has(i) ? `
    <text class="tp-tick" x="${x(i)}" y="${H - 7}" text-anchor="${
      i === 0 ? "start" : i === rows.length - 1 ? "end" : "middle"
    }">${escapeHtml(fmtBucketLabel(r.label))}</text>` : "")).join("");

  let marks = "";
  if (kind === "line") {
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    // Markers carry a 2px surface ring so they stay legible where the line
    // crosses them or where two points sit close together.
    const dots = values.map((v, i) =>
      `<circle class="tp-dot" cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" />`).join("");
    marks = `<path class="tp-line" d="${d}" />${dots}`;
  } else {
    const bw = Math.max(Math.min(slot - 2, 24), 3);   // <=24px, 2px of air
    marks = values.map((v, i) => {
      const top_ = y(v);
      const height = Math.max(PAD.t + plotH - top_, 1);
      const r = Math.min(4, bw / 2, height);
      // Rounded at the data end, square at the baseline.
      return `<path class="tp-bar" d="M${(x(i) - bw / 2).toFixed(1)} ${(PAD.t + plotH).toFixed(1)}
        V${(top_ + r).toFixed(1)} a${r} ${r} 0 0 1 ${r} ${-r}
        h${(bw - 2 * r).toFixed(1)} a${r} ${r} 0 0 1 ${r} ${r}
        V${(PAD.t + plotH).toFixed(1)} Z" />`;
    }).join("");
  }

  // The trend rule: recessive, solid, clearly not a second series.
  const trend = tempoTrendPoints(values);
  const trendLine = trend ? `<line class="tp-trend" x1="${x(0)}" y1="${
    y(Math.max(Math.min(trend[0], top), 0))}" x2="${x(rows.length - 1)}" y2="${
    y(Math.max(Math.min(trend[1], top), 0))}" />` : "";

  // Hit strips: full-height targets, far bigger than the marks themselves.
  const hits = rows.map((r, i) => {
    // Clamped so the end strips stay inside the plot instead of hanging off
    // it, which would put part of the hit target under the y-axis labels.
    const left = Math.max(x(i) - slot / 2, PAD.l);
    const right = Math.min(x(i) + slot / 2, PAD.l + plotW);
    return `<rect class="tp-hit" data-i="${i}" x="${left.toFixed(1)}" y="${PAD.t}"
      width="${Math.max(right - left, 1).toFixed(1)}" height="${plotH}" />`;
  }).join("");

  return `
    <div class="tp-panel" data-kind="${kind}">
      <div class="tp-panel-head">
        <h4>${escapeHtml(title)}</h4>
        <span class="muted">${escapeHtml(subtitle)}</span>
      </div>
      <div class="tp-plot">
        <svg viewBox="0 0 ${width} ${H}" width="${width}" height="${H}" role="img"
             aria-label="${escapeHtml(title)}">
          ${grid}${trendLine}${marks}${xLabels}
          <line class="tp-cross hidden-cross" x1="0" y1="${PAD.t}" x2="0" y2="${PAD.t + plotH}" />
          ${hits}
        </svg>
        <div class="tp-tip" hidden></div>
      </div>
    </div>`;
}

/** Wire the crosshair + tooltip for one rendered panel. */
function bindTempoPanel(panel, rows, granularity) {
  const svg = panel.querySelector("svg");
  const tip = panel.querySelector(".tp-tip");
  const cross = panel.querySelector(".tp-cross");
  const plot = panel.querySelector(".tp-plot");

  const show = (i, hit) => {
    const r = rows[i];
    const box = hit.getBoundingClientRect();
    const host = plot.getBoundingClientRect();
    cross.classList.remove("hidden-cross");
    const cx = parseFloat(hit.getAttribute("x")) + parseFloat(hit.getAttribute("width")) / 2;
    cross.setAttribute("x1", cx);
    cross.setAttribute("x2", cx);
    tip.hidden = false;
    tip.innerHTML = `
      <strong>${escapeHtml(r.label)}</strong>
      <span>${escapeHtml(fmtDuration(r.median_seconds))} median${
        r.decisions > 1 ? " per decision" : ""}</span>
      <span>${r.decisions} decision${r.decisions === 1 ? "" : "s"}</span>
      <span class="tp-tip-adj">${escapeHtml(fmtDuration(r.adjusted_seconds))} adjusted for choice count</span>`;
    // Keep the bubble inside the card on a narrow screen.
    const left = box.left - host.left + box.width / 2;
    tip.style.left = `${Math.min(Math.max(left, 58), host.width - 58)}px`;
  };
  const hide = () => {
    tip.hidden = true;
    cross.classList.add("hidden-cross");
  };

  svg.querySelectorAll(".tp-hit").forEach((hit) => {
    const i = parseInt(hit.dataset.i, 10);
    hit.addEventListener("pointerenter", () => show(i, hit));
    hit.addEventListener("pointerdown", () => show(i, hit));
  });
  plot.addEventListener("pointerleave", hide);
}

const TEMPO_VERDICT = {
  faster:  { cls: "good", icon: "&#9660;", word: "getting faster" },
  slower:  { cls: "warn", icon: "&#9650;", word: "getting slower" },
  rising:  { cls: "good", icon: "&#9650;", word: "rising" },
  falling: { cls: "warn", icon: "&#9660;", word: "falling" },
  steady:  { cls: "flat", icon: "&#9679;", word: "holding steady" },
};

/** A score tile: the number, what it means, and how much to trust it. */
function tempoTile(title, score, verdict, evidence, detail) {
  const v = TEMPO_VERDICT[verdict] || TEMPO_VERDICT.steady;
  return `
    <div class="tp-tile">
      <div class="tp-tile-head">${escapeHtml(title)}</div>
      <div class="tp-score">${score.toFixed(0)}<small>/100</small></div>
      <div class="tp-badge ${v.cls}"><span aria-hidden="true">${v.icon}</span> ${v.word}</div>
      <div class="tp-meter"><i style="width:${Math.min(Math.max(score, 0), 100)}%"></i>
        <b style="left:50%"></b></div>
      <div class="tp-detail">${detail}</div>
      <div class="tp-evidence ${evidence}">${
        evidence === "significant" ? "Statistically significant"
        : evidence === "suggestive" ? "Suggestive, not yet conclusive"
        : "Not yet distinguishable from chance"}</div>
    </div>`;
}

let tempoGrain = "";      // "" = let the server pick from the span
let tempoData = null;     // last payload, so a resize re-draws without refetch

async function renderTempo() {
  const body = $("#tempo-body");
  if (!body) return;
  body.innerHTML = `<p class="muted">Loading...</p>`;
  try {
    tempoData = await api.tempo(tempoGrain || null);
  } catch (err) {
    body.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
    return;
  }
  drawTempo();
}

function drawTempo() {
  const body = $("#tempo-body");
  const data = tempoData;
  if (!body || !data) return;

  if (data.status !== "ready") {
    body.innerHTML = `
      <div class="tp-empty">
        <p>${escapeHtml(data.message)}</p>
        <p class="muted">So far: ${data.totals.decisions} timed decision${
          data.totals.decisions === 1 ? "" : "s"} across ${data.totals.buckets} ${
          data.granularity}${data.totals.buckets === 1 ? "" : "s"}. Decisions made
          before the timer existed, and any made with personalization switched
          off, are not counted.</p>
      </div>`;
    return;
  }

  const rows = data.series.slice(-TEMPO_MAX_BUCKETS);
  // Measure the real width so one SVG unit is one CSS pixel: scaling a fixed
  // viewBox down to phone width would shrink the axis text with it.
  const width = Math.max(Math.floor(body.clientWidth || 520), 260);
  const half = width > 720 ? Math.floor((width - 18) / 2) : width;
  const unit = data.granularity === "day" ? "day" : "week";

  body.innerHTML = `
    <p class="tp-lede">${escapeHtml(data.message)}</p>

    <div class="tp-tiles">
      ${tempoTile("Speed", data.speed.score, data.speed.verdict, data.speed.evidence,
        `${data.speed.pct_change_per_bucket > 0 ? "+" : ""}${
          data.speed.pct_change_per_bucket}% time per decision, each ${unit}`)}
      ${tempoTile("Confidence", data.confidence.score, data.confidence.verdict,
        data.confidence.evidence,
        `${data.confidence.slope > 0 ? "+" : ""}${data.confidence.slope} decisions per ${unit}`)}
    </div>

    <div class="tp-panels">
      ${tempoPanel(rows, {
        kind: "line", values: rows.map((r) => r.adjusted_seconds), width: half,
        title: "Time spent per decision",
        subtitle: `Median, adjusted for how many options were on the table. Down is faster.`,
        unit: "s", granularity: data.granularity,
      })}
      ${tempoPanel(rows, {
        kind: "bar", values: rows.map((r) => r.decisions), width: half,
        title: "Decisions made",
        subtitle: `How many you settled each ${unit}. Up reads as confidence.`,
        unit: "n", granularity: data.granularity,
      })}
    </div>

    <details class="tp-method">
      <summary>How this is calculated</summary>
      <div class="tp-method-body">
        <p>Each decision is timed in the browser from opening the decision panel
          to submitting it, then divided by <strong>log&#8322;(options + 1)</strong> - the
          <strong>Hick-Hyman law</strong>, which says choice time grows with the logarithm
          of the number of alternatives. Without that, picking between six
          options would always look like "slowing down".</p>
        <p>The trend in each series is a <strong>Theil-Sen slope</strong> (the median of
          every pairwise slope, so one abandoned tab cannot create a trend) and
          its significance is a <strong>Mann-Kendall test</strong>, which assumes nothing
          about the shape of the data. Times are logged first, because reaction
          times are skewed, which is why the speed figure reads as a percentage
          change rather than a number of seconds.</p>
        ${data.power_law ? `
        <p>Across all ${data.power_law.n} timed decisions, your practice curve fits
          the <strong>power law of practice</strong>, T&nbsp;=&nbsp;a&nbsp;&times;&nbsp;N<sup>&minus;b</sup>,
          with <strong>b&nbsp;=&nbsp;${data.power_law.exponent.toFixed(2)}</strong>
          (R&sup2;&nbsp;${data.power_law.r2.toFixed(2)}). Published learning
          exponents usually sit between 0.2 and 0.6;
          ${data.power_law.exponent > 0.05
            ? "yours is positive, so repetition is genuinely making this cheaper."
            : "yours is not positive yet, so repetition has not started paying off."}</p>` : ""}
        <p class="muted">Scores are pulled toward 50 while the evidence is thin
          (${Math.round(data.speed.reliability * 100)}% of the way to full weight
          at ${data.totals.buckets} ${unit}s), so an early run of good luck cannot
          read as a breakthrough. Mann-Kendall p:
          speed ${data.speed.p_value}, volume ${data.confidence.p_value}.</p>
      </div>
    </details>`;

  body.querySelectorAll(".tp-panel").forEach((panel) =>
    bindTempoPanel(panel, rows, data.granularity));
}

/* ---- behavioural-science correlation (Insights tab) ---- */

async function renderBehavioral() {
  const params = $("#bs-params");
  const links = $("#bs-links");
  params.innerHTML = "";
  links.innerHTML = `<p class="muted">Loading...</p>`;
  try {
    const data = await api.behavioral();
    const moved = data.parameters.filter((p) => p.moved);

    params.innerHTML = `
      <div class="bs-params-head">
        <h4>What your answers actually changed</h4>
        <span class="muted">${moved.length
          ? `${moved.length} of ${data.parameters.length} engine parameters moved`
          : "Every parameter is at its default"}</span>
      </div>
      <div class="bs-param-rows">
        ${data.parameters.map((p) => `
          <div class="bs-param${p.moved ? " moved" : ""}">
            <div class="bs-param-name">${escapeHtml(p.label)}
              <span>${escapeHtml(p.meaning)}</span>
            </div>
            <div class="bs-param-vals">
              <span class="from">${p.base.toFixed(2)}</span>
              <span class="arrow">to</span>
              <span class="to">${p.tuned.toFixed(2)}</span>
              ${p.moved ? `<span class="delta">${p.change_pct > 0 ? "+" : ""}${p.change_pct}%</span>`
                        : `<span class="delta flat">unchanged</span>`}
            </div>
          </div>`).join("")}
      </div>
      <p class="bs-param-note">
        The three core weights are rescaled to sum to 1 after they are set, so a
        raised weight can still end up carrying a smaller slice of the score when
        another rose further. Shares after rescaling:
        ${data.parameters.filter((p) => p.share !== p.tuned)
          .map((p) => `${escapeHtml(p.label.replace(" weight", ""))} ${(p.share * 100).toFixed(0)}%`)
          .join(" · ") || "unchanged"}.
      </p>`;

    links.innerHTML = bsList(
      data.correlations,
      "Take the survey on the Profile tab and this fills in - every finding below is matched to something you actually told us.",
    );
  } catch (err) {
    links.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

/* ================= history ================= */

async function renderHistory() {
  const list = $("#history-list");
  list.innerHTML = `<div class="empty">Loading...</div>`;
  try {
    const entries = await api.history();
    if (!entries.length) {
      list.innerHTML = emptyState("No decisions yet", `Use "Explain with ${AI_NAME}" on the Decide tab - every run is saved here.`);
      return;
    }
    list.innerHTML = entries.map((e) => {
      const top = e.ranking[0];
      return `
      <div class="history-item">
        <div class="history-head" data-toggle>
          <span class="history-date">${fmtDate(e.created_at)}</span>
          <span class="history-title">${escapeHtml(top?.title ?? "-")}
            <span class="cat-tag cat-${top?.category}">${top?.category ?? ""}</span></span>
          <span class="score-chip ${scoreClass(top?.score ?? 0)}">${top ? `${top.score.toFixed(0)}%` : "-"}</span>
          <span class="tag">${sourceLabel(e.insight_source)}</span>
          <span class="chev">▾</span>
        </div>
        <div class="history-body hidden">
          <ol>${e.ranking.map((r) =>
            `<li><strong>${escapeHtml(r.title)}</strong> - ${r.category}, score ${Math.round(r.score)}%</li>`).join("")}
          </ol>
          <div style="margin-top:8px">Clarity of winner: <strong>${Math.round(e.clarity * 100)}%</strong></div>
          ${e.insight ? `<div class="history-insight">${renderInsight(e.insight)}</div>` : ""}
        </div>
      </div>`;
    }).join("");
    $$("[data-toggle]").forEach((head) =>
      head.addEventListener("click", () => {
        head.nextElementSibling.classList.toggle("hidden");
        head.parentElement.classList.toggle("open");
      }));
  } catch (err) {
    list.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

/* ================= survey wizard ================= */

/* Branching. Steps 1-4 are asked of everybody; from step 4 ("Where you need
   help") onwards each section declares the support areas that unlock it, so
   a student only answers the deep-dives for the areas they asked for. Mirrors
   survey_spec.condition_met on the backend, which is what actually validates. */

function conditionMet(condition, draft) {
  if (!condition) return true;
  let value = draft[condition.key] || [];
  if (!Array.isArray(value)) value = [value];
  if (condition.contains !== undefined) return value.includes(condition.contains);
  return (condition.contains_any || []).some((option) => value.includes(option));
}

function sectionVisible(section, draft) {
  return conditionMet(section.depends_on, draft);
}

function visibleSections(spec, draft) {
  return spec.filter((section) => sectionVisible(section, draft));
}

function questionVisible(question, draft) {
  return conditionMet(question.depends_on, draft);
}

function questionAnswered(question, draft) {
  const value = draft[question.key];
  if (question.type === "multi") return Array.isArray(value) && value.length > 0;
  if (question.type === "text") return true; // optional
  return value !== undefined && value !== null && value !== "";
}

function renderQuestion(question, draft) {
  const value = draft[question.key];
  const optional = question.required === false
    ? ` <span class="optional">(optional)</span>` : "";
  let control = "";

  if (question.type === "single" || question.type === "multi") {
    const long = question.options.some((o) => o.length > 24);
    control = `<div class="opt-grid ${long ? "cols" : ""}" data-key="${question.key}" data-type="${question.type}">` +
      question.options.map((opt) => {
        const selected = question.type === "multi"
          ? (value || []).includes(opt) : value === opt;
        return `<button type="button" class="opt ${selected ? "sel" : ""}" data-opt="${escapeHtml(opt)}">${escapeHtml(opt)}</button>`;
      }).join("") + `</div>`;
  } else if (question.type === "scale") {
    control = `
      <div class="scale-row" data-key="${question.key}" data-type="scale">
        ${[1, 2, 3, 4, 5].map((n) =>
          `<button type="button" class="opt ${value === n ? "sel" : ""}" data-opt="${n}">${n}</button>`).join("")}
      </div>
      <div class="scale-caps"><span>${escapeHtml(question.low || "1")}</span><span>${escapeHtml(question.high || "5")}</span></div>`;
  } else if (question.type === "number") {
    control = `
      <div class="num-input" data-key="${question.key}" data-type="number">
        <button type="button" class="num-btn" data-step="-1" aria-label="decrease">-</button>
        <input type="number" min="${question.min}" max="${question.max}" value="${value ?? question.min}" inputmode="numeric" />
        <button type="button" class="num-btn" data-step="1" aria-label="increase">+</button>
      </div>`;
  } else if (question.type === "text") {
    control = `<input type="text" data-key="${question.key}" data-type="text"
      maxlength="200" placeholder="Type here..." value="${escapeHtml(value || "")}" />`;
  }

  return `<div class="q-block" data-q="${question.key}">
    <label class="q-label">${escapeHtml(question.label)}${optional}</label>${control}
  </div>`;
}

function renderSurvey() {
  // The consent controls live beside the survey, not behind it: the Profile
  // tab is where a user goes to change what the app knows about them.
  renderPrivacy();
  const root = $("#survey-root");
  const spec = state.surveySpec || [];
  if (!spec.length) {
    root.innerHTML = `<div class="empty">Survey unavailable - try refreshing.</div>`;
    return;
  }
  const wiz = state.wizard;

  // Prefill from saved answers when editing an existing profile.
  if (state.surveyAnswers && !wiz.started) {
    wiz.draft = JSON.parse(JSON.stringify(state.surveyAnswers));
  }

  if (!wiz.started) {
    const retaking = Boolean(state.surveyAnswers);
    root.innerHTML = `
      <div class="survey-welcome">
        <div class="eyebrow">DECISION-MAKING SURVEY</div>
        <h2>${retaking ? "Update your decision profile" : `Hey ${escapeHtml(state.user.name.split(" ")[0])}, let's tune the engine to you`}</h2>
        <p>Four short sections about how you decide - then you pick the areas you
           want help with, and you only answer the deep-dives for those.
           Your answers directly change how your tasks get ranked.</p>
        <button class="btn-primary" id="wiz-start">${retaking ? "Review my answers" : "Start - takes ~3 minutes"}</button>
      </div>`;
    $("#wiz-start").addEventListener("click", () => {
      wiz.started = true;
      wiz.step = 0;
      renderSurvey();
    });
    return;
  }

  // Only the unlocked sections exist as far as the wizard is concerned, so
  // the step count and the progress bar always describe the path this
  // student is actually walking.
  const sections = visibleSections(spec, wiz.draft);
  const step = Math.min(wiz.step, sections.length - 1);
  const section = sections[step];
  const isLast = step === sections.length - 1;
  const visible = section.questions.filter((q) => questionVisible(q, wiz.draft));
  const totalQ = sections
    .flatMap((s) => s.questions)
    .filter((q) => questionVisible(q, wiz.draft));
  const answered = totalQ.filter((q) => questionAnswered(q, wiz.draft)).length;
  const pct = Math.round((answered / totalQ.length) * 100);
  const accent = section.accent || "sage";

  root.innerHTML = `
    <div class="wiz-top">
      <span class="wiz-step-label accent-${accent}">Step ${step + 1} of ${sections.length}</span>
      <span class="wiz-count">${pct}% complete</span>
    </div>
    <div class="progress-track"><div class="progress-fill accent-${accent}" style="width:${pct}%"></div></div>
    <div class="card wiz-card accent-${accent}">
      ${section.because ? `
        <span class="wiz-branch">Because you picked ${escapeHtml(section.because)}</span>` : ""}
      <h3 class="wiz-title">${escapeHtml(section.title)}</h3>
      ${section.blurb ? `<p class="wiz-blurb">${escapeHtml(section.blurb)}</p>` : `<div style="height:10px"></div>`}
      ${visible.map((q) => renderQuestion(q, wiz.draft)).join("")}
      <div class="wiz-nav">
        ${step > 0 ? `<button type="button" class="ghost-btn" id="wiz-back">Back</button>` : "<span></span>"}
        <span class="spacer"></span>
        <span class="wiz-err hidden" id="wiz-err"></span>
        <button type="button" class="btn-primary" id="wiz-next">
          ${isLast ? "Finish & save" : "Next"}
        </button>
      </div>
    </div>
    <div class="stepper" aria-hidden="true">
      ${sections.map((s, i) => `<i class="accent-${s.accent || "sage"} ${
        i < step ? "done" : i === step ? "now" : ""}"></i>`).join("")}
    </div>`;

  bindWizardEvents(section, visible, sections);
}

function bindWizardEvents(section, visibleQuestions, sections) {
  const wiz = state.wizard;
  const root = $("#survey-root");

  root.querySelectorAll(".opt-grid, .scale-row").forEach((group) => {
    group.addEventListener("click", (event) => {
      const btn = event.target.closest(".opt");
      if (!btn) return;
      const key = group.dataset.key;
      const opt = btn.dataset.opt;
      if (group.dataset.type === "multi") {
        const current = new Set(wiz.draft[key] || []);
        current.has(opt) ? current.delete(opt) : current.add(opt);
        wiz.draft[key] = [...current];
      } else if (group.dataset.type === "scale") {
        wiz.draft[key] = parseInt(opt, 10);
      } else {
        wiz.draft[key] = opt;
      }
      renderSurvey(); // re-render keeps conditional questions + progress in sync
    });
  });

  root.querySelectorAll(".num-input").forEach((wrap) => {
    const input = wrap.querySelector("input");
    const key = wrap.dataset.key;
    const clamp = () => {
      const min = parseInt(input.min, 10), max = parseInt(input.max, 10);
      let v = parseInt(input.value, 10);
      if (Number.isNaN(v)) return;
      v = Math.max(min, Math.min(max, v));
      input.value = v;
      wiz.draft[key] = v;
    };
    input.addEventListener("change", clamp);
    wrap.querySelectorAll(".num-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        input.value = (parseInt(input.value, 10) || parseInt(input.min, 10)) + parseInt(btn.dataset.step, 10);
        clamp();
      }));
    // register default immediately so the question counts as answered once seen
    if (wiz.draft[key] === undefined) wiz.draft[key] = parseInt(input.value, 10);
  });

  root.querySelectorAll('input[data-type="text"]').forEach((input) => {
    input.addEventListener("input", () => { wiz.draft[input.dataset.key] = input.value; });
  });

  const back = $("#wiz-back");
  if (back) back.addEventListener("click", () => { wiz.step -= 1; renderSurvey(); });

  $("#wiz-next").addEventListener("click", async () => {
    const err = $("#wiz-err");
    const missing = visibleQuestions.filter(
      (q) => q.type !== "text" && !questionAnswered(q, wiz.draft));
    if (missing.length) {
      err.textContent = "Please answer everything on this step.";
      err.classList.remove("hidden");
      root.querySelector(`[data-q="${missing[0].key}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    err.classList.add("hidden");
    // Recomputed here, not captured at render time: choosing support areas on
    // this very step can add or remove the steps that follow it.
    const path = visibleSections(state.surveySpec, wiz.draft);
    if (wiz.step < path.length - 1) {
      wiz.step += 1;
      renderSurvey();
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    // final submit
    const btn = $("#wiz-next");
    btn.disabled = true;
    btn.textContent = "Saving...";
    try {
      const saved = await api.saveSurvey(wiz.draft);
      state.surveyAnswers = saved.answers;
      state.user.has_survey = true;
      wiz.started = false;
      toast("Profile saved - your ranking is now personalized");
      setView("tasks");
    } catch (error) {
      err.textContent = error.message;
      err.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Finish & save";
    }
  });
}

/* ================= per-task decision workspace ================= */

const dec = {
  task: null, template: null,
  options: [], criteria: [], ratings: {}, context: "",
  stage: "setup", // setup | rate | result
  result: null,
  // Deliberation clock, feeding the tempo trend on the Insights tab.
  // performance.now() rather than Date.now(), so a system clock change or an
  // NTP correction mid-decision cannot produce a negative elapsed time.
  timing: false,    // has the clock been started for this round at all?
  runningAt: 0,     // performance.now() when the current run began, 0 = paused
  bankedMs: 0,      // time from runs already closed off
};

/** Start a fresh deliberation clock. Called once per round of thinking:
 *  opening the modal, and again on Re-decide or Adjust ratings, because each
 *  of those is genuinely new deliberation rather than a continuation. */
function decClockReset() {
  dec.timing = true;
  dec.runningAt = performance.now();
  dec.bankedMs = 0;
}

/** Pause/resume with tab visibility.
 *
 *  A decision left open on a background tab over lunch is not two hours of
 *  deliberation. Banking the elapsed time on hide and restarting on show
 *  keeps the series measuring thinking rather than idle browser tabs. */
function decClockPause() {
  if (!dec.timing || !dec.runningAt) return;
  dec.bankedMs += performance.now() - dec.runningAt;
  dec.runningAt = 0;
}
function decClockResume() {
  if (!dec.timing || dec.runningAt) return;
  dec.runningAt = performance.now();
}

/** Seconds spent deliberating, or null if the clock never honestly ran.
 *
 *  null rather than 0 when there is nothing to report: the server treats a
 *  missing value as "no data point", which is the truth, whereas a 0 would
 *  enter the trend as an impossibly fast decision and drag it down. */
function decElapsedSeconds() {
  if (!dec.timing) return null;
  const ms = dec.bankedMs + (dec.runningAt ? performance.now() - dec.runningAt : 0);
  return ms > 0 ? Math.round(ms / 100) / 10 : null;
}

function openModal() { $("#modal-root").classList.remove("hidden"); }
function closeModal() {
  $("#modal-root").classList.add("hidden");
  dec.task = null;
  dec.timing = false;   // the clock has nothing left to time
}

async function openDecision(task) {
  dec.task = task;
  dec.stage = "setup";
  dec.result = null;
  decClockReset();
  openModal();
  $("#modal-body").innerHTML = `<div class="empty">Loading...</div>`;
  try {
    const [template, existing] = await Promise.all([
      api.decisionTemplate(task.category),
      api.getDecision(task.id).catch(() => null),
    ]);
    dec.template = template;
    if (existing) {
      dec.options = [...existing.options];
      dec.criteria = existing.criteria.map((c) => ({ name: c.name, weight: c.weight, tag: c.tag }));
      dec.ratings = JSON.parse(JSON.stringify(existing.ratings));
      dec.context = existing.context || "";
      dec.result = existing;
      dec.stage = "result";
    } else {
      dec.options = [];
      dec.criteria = template.criteria.map((c) => ({ ...c }));
      dec.ratings = {};
      dec.context = "";
    }
    renderDecision();
  } catch (err) {
    $("#modal-body").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

function decHeader(subtitle) {
  return `
    <div class="dec-kicker">DECIDE · ${escapeHtml(dec.task.category.toUpperCase())}</div>
    <div class="dec-title">${escapeHtml(dec.task.title)}</div>
    <div class="dec-sub">${subtitle}</div>`;
}

function renderDecision() {
  if (dec.stage === "setup") renderDecSetup();
  else if (dec.stage === "rate") renderDecRate();
  else renderDecResult();
}

/* ---- stage 1: options + criteria ---- */

function renderDecSetup() {
  const t = dec.template;
  $("#modal-body").innerHTML = `
    ${decHeader(`List the options and what matters - ${AI_NAME} rates them for you and the engine calls it.`)}

    <div class="dec-section">
      <div class="dec-label">${escapeHtml(t.options_prompt)}</div>
      <div class="dec-help">Add 2-8 options.</div>
      <div class="opt-chips">
        ${dec.options.map((o, i) => `
          <span class="opt-chip">${escapeHtml(o)}
            <button data-rm-opt="${i}" aria-label="Remove ${escapeHtml(o)}">✕</button>
          </span>`).join("")}
      </div>
      <div class="add-row">
        <input id="dec-opt-input" type="text" maxlength="80"
          placeholder="${escapeHtml(t.options_placeholder || "Add an option")}" />
        <button type="button" class="btn-dark" id="dec-opt-add">Add</button>
      </div>
    </div>

    <div class="dec-section">
      <div class="dec-label">What matters for this decision?</div>
      <div class="dec-help">Suggested for ${escapeHtml(dec.task.category)} - tune how much each counts (1-5), remove what doesn't apply, or add your own.</div>
      ${dec.criteria.map((c, i) => `
        <div class="crit-row">
          <span class="crit-name">${escapeHtml(c.name)}
            ${c.hint ? `<span class="hint">${escapeHtml(c.hint)}</span>` : ""}</span>
          <span class="dots" data-crit="${i}">
            ${[1, 2, 3, 4, 5].map((n) =>
              `<button data-w="${n}" class="${c.weight === n ? "sel" : ""}">${n}</button>`).join("")}
          </span>
          <button class="crit-remove" data-rm-crit="${i}" aria-label="Remove criterion">✕</button>
        </div>`).join("")}
      <div class="add-row" style="margin-top:10px">
        <input id="dec-crit-input" type="text" maxlength="60" placeholder="Add your own criterion" />
        <button type="button" class="btn-dark" id="dec-crit-add">Add</button>
      </div>
    </div>

    <div class="dec-section">
      <div class="dec-label">Anything ${AI_NAME} should know? <span class="opt-tag">optional</span></div>
      <div class="dec-help">Facts it can't guess - deadlines, budget, how you're feeling about each option.</div>
      <textarea id="dec-context" maxlength="400" rows="2"
        placeholder="e.g. Physics test on Friday, DBMS assignment already half done"
      >${escapeHtml(dec.context)}</textarea>
    </div>

    ${(t.personalization_notes || []).length ? `
      <ul class="dec-notes">${t.personalization_notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}

    <div class="dec-nav">
      <button class="ghost-btn" id="dec-manual">Rate the options myself</button>
      <span class="wiz-err hidden" id="dec-err"></span>
      <span class="spacer"></span>
      <button class="btn-primary" id="dec-auto">Decide for me</button>
    </div>`;

  $("#dec-context").addEventListener("input", (e) => { dec.context = e.target.value; });

  const addOption = () => {
    const input = $("#dec-opt-input");
    const value = input.value.trim();
    if (!value) return;
    if (dec.options.some((o) => o.toLowerCase() === value.toLowerCase())) {
      toast("That option is already added"); return;
    }
    if (dec.options.length >= 8) { toast("Maximum 8 options"); return; }
    dec.options.push(value);
    renderDecSetup();
    $("#dec-opt-input").focus();
  };
  $("#dec-opt-add").addEventListener("click", addOption);
  $("#dec-opt-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addOption(); }
  });

  $("#dec-crit-add").addEventListener("click", () => {
    const value = $("#dec-crit-input").value.trim();
    if (!value) return;
    if (dec.criteria.some((c) => c.name.toLowerCase() === value.toLowerCase())) {
      toast("That criterion already exists"); return;
    }
    if (dec.criteria.length >= 8) { toast("Maximum 8 criteria"); return; }
    dec.criteria.push({ name: value, weight: 3, tag: "other" });
    renderDecSetup();
  });

  $("#modal-body").querySelectorAll("[data-rm-opt]").forEach((btn) =>
    btn.addEventListener("click", () => {
      dec.options.splice(parseInt(btn.dataset.rmOpt, 10), 1);
      renderDecSetup();
    }));
  $("#modal-body").querySelectorAll("[data-rm-crit]").forEach((btn) =>
    btn.addEventListener("click", () => {
      dec.criteria.splice(parseInt(btn.dataset.rmCrit, 10), 1);
      renderDecSetup();
    }));
  $("#modal-body").querySelectorAll(".dots[data-crit]").forEach((group) =>
    group.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-w]");
      if (!btn) return;
      dec.criteria[parseInt(group.dataset.crit, 10)].weight = parseInt(btn.dataset.w, 10);
      renderDecSetup();
    }));

  const validateSetup = () => {
    const err = $("#dec-err");
    if (dec.options.length < 2) {
      err.textContent = "Add at least two options.";
      err.classList.remove("hidden"); return false;
    }
    if (!dec.criteria.length) {
      err.textContent = "Keep at least one criterion.";
      err.classList.remove("hidden"); return false;
    }
    err.classList.add("hidden");
    return true;
  };

  $("#dec-manual").addEventListener("click", () => {
    if (!validateSetup()) return;
    dec.stage = "rate";
    renderDecision();
  });

  // The AVEX path: no rating grid at all - AVEX scores every option from the
  // criteria plus the student's survey profile, then we jump to the result.
  $("#dec-auto").addEventListener("click", async () => {
    if (!validateSetup()) return;
    const err = $("#dec-err");
    const btn = $("#dec-auto");
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span> Rating your options...`;
    try {
      dec.result = await runDecision(api.decideTaskAuto);
      dec.ratings = JSON.parse(JSON.stringify(dec.result.ratings));
      dec.stage = "result";
      renderDecision();
      refreshAfterDecision();
    } catch (error) {
      err.textContent = error.message;
      err.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Decide for me";
    }
  });
}

/** POST the current setup (and, for the manual endpoint, the ratings). */
function runDecision(call) {
  const criteria = dec.criteria.map(({ name, weight, tag }) =>
    ({ name, weight, tag: tag || "other" }));
  const body = {
    options: dec.options,
    criteria,
    deliberation_seconds: decElapsedSeconds(),
  };
  if (call === api.decideTaskAuto) {
    body.context = dec.context.trim() || null;
  } else {
    // Only send ratings for the current options/criteria.
    body.ratings = {};
    for (const option of dec.options) {
      body.ratings[option] = {};
      for (const criterion of dec.criteria) {
        body.ratings[option][criterion.name] = dec.ratings[option][criterion.name];
      }
    }
  }
  return call(dec.task.id, body);
}

function refreshAfterDecision() {
  if (state.view === "decide") renderDecide();
  if (state.view === "tasks") renderTasks();
}

/* ---- stage 2: rating matrix ---- */

function renderDecRate() {
  $("#modal-body").innerHTML = `
    ${decHeader(dec.result && dec.result.ratings_source === "gemini"
      ? `Adjust ${AI_NAME}'s ratings - change anything that looks off, then re-decide.`
      : "Rate each option 1-5 on every criterion.")}
    ${dec.criteria.map((c, ci) => `
      <div class="rate-block">
        <div class="rate-head">${escapeHtml(c.name)}</div>
        <div class="rate-hint">${escapeHtml(c.hint || "5 = strongly in favour")} · counts ×${c.weight}</div>
        ${dec.options.map((o) => `
          <div class="rate-row">
            <span class="name">${escapeHtml(o)}</span>
            <span class="dots" data-ci="${ci}" data-opt="${escapeHtml(o)}">
              ${[1, 2, 3, 4, 5].map((n) => `
                <button data-r="${n}" class="${(dec.ratings[o]?.[c.name]) === n ? "sel" : ""}">${n}</button>`).join("")}
            </span>
          </div>`).join("")}
      </div>`).join("")}
    <div class="dec-nav">
      <button class="ghost-btn" id="dec-back">Back</button>
      <span class="wiz-err hidden" id="dec-err"></span>
      <span class="spacer"></span>
      <button class="btn-primary" id="dec-run">Decide for me</button>
    </div>`;

  $("#modal-body").querySelectorAll(".dots[data-ci]").forEach((group) =>
    group.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-r]");
      if (!btn) return;
      const criterion = dec.criteria[parseInt(group.dataset.ci, 10)].name;
      const option = group.dataset.opt;
      dec.ratings[option] = dec.ratings[option] || {};
      dec.ratings[option][criterion] = parseInt(btn.dataset.r, 10);
      group.querySelectorAll("button").forEach((b) => b.classList.toggle("sel", b === btn));
    }));

  $("#dec-back").addEventListener("click", () => { dec.stage = "setup"; renderDecision(); });

  $("#dec-run").addEventListener("click", async () => {
    const err = $("#dec-err");
    const missing = [];
    for (const option of dec.options) {
      for (const criterion of dec.criteria) {
        if (!dec.ratings[option]?.[criterion.name]) missing.push(option);
      }
    }
    if (missing.length) {
      err.textContent = `Rate everything first (missing: ${[...new Set(missing)][0]}...).`;
      err.classList.remove("hidden");
      return;
    }
    const btn = $("#dec-run");
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span> Deciding...`;
    try {
      dec.result = await runDecision(api.decideTask);
      dec.stage = "result";
      renderDecision();
      refreshAfterDecision();
    } catch (error) {
      err.textContent = error.message;
      err.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Decide for me";
    }
  });
}

/* ---- the audit: the decision re-derived so it can be checked ----
   Everything here comes from decision_engine.build_audit - the same numbers
   that produced the answer, shown as arithmetic rather than as a claim. */

/** Render a formula written as `S_i = ... SUM_j ...` with real subscripts.
    Escapes first, so only the markup this function adds can reach the DOM. */
function formula(text) {
  return escapeHtml(text)
    .replace(/SUM_([a-z]+)/g, "&Sigma;<sub>$1</sub>")
    .replace(/([A-Za-z])_([a-z]+)/g, "$1<sub>$2</sub>");
}

const VERDICT_COPY = {
  unconditional: "Unconditional",
  robust: "Robust",
  stable: "Stable",
  fragile: "Close call",
  tie: "Dead heat",
};

function auditWeights(audit) {
  const anyAdjusted = audit.weights.some((w) => w.adjusted);
  return `
    <div class="audit-scroll">
      <table class="audit-table">
        <thead>
          <tr>
            <th>Criterion</th><th>You said</th>
            ${anyAdjusted ? `<th>Your profile</th>` : ""}
            <th>Effective</th><th>Share of the score</th>
          </tr>
        </thead>
        <tbody>
          ${audit.weights.map((w) => `
            <tr${w.adjusted ? ` class="adjusted"` : ""}>
              <td>${escapeHtml(w.name)} <span class="audit-tag">${escapeHtml(w.tag)}</span></td>
              <td class="num">${w.user_weight}/5</td>
              ${anyAdjusted ? `<td class="num">${w.adjusted ? `×${w.multiplier}` : "-"}</td>` : ""}
              <td class="num">${w.effective_weight}</td>
              <td class="num">
                <div class="audit-share">
                  <div class="track"><div class="fill" style="width:${w.share_pct}%"></div></div>
                  <strong>${w.share_pct}%</strong>
                </div>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
    <p class="audit-note">
      Effective weight = your 1-5 importance × the multiplier your survey earned that
      criterion's type. The column on the right is those effective weights divided by
      their total (${audit.weight_total}), which is what makes every score land in 0-100%.
    </p>`;
}

function auditContributions(audit) {
  const names = audit.weights.map((w) => w.name);
  return `
    <div class="audit-scroll">
      <table class="audit-table audit-matrix">
        <thead>
          <tr>
            <th>Option</th>
            ${names.map((n) => `<th>${escapeHtml(n)}</th>`).join("")}
            <th>Score</th>
          </tr>
        </thead>
        <tbody>
          ${audit.contributions.map((row, i) => `
            <tr${i === 0 ? ` class="winner-row"` : ""}>
              <td class="opt-cell">${escapeHtml(row.option)}</td>
              ${names.map((n) => {
                const part = row.parts.find((p) => p.criterion === n) || {};
                return `<td class="num cell-pts">
                  <span class="pts">${(part.points ?? 0).toFixed(1)}</span>
                  <span class="raw">${part.rating ?? "-"}/5</span>
                </td>`;
              }).join("")}
              <td class="num total">${row.total.toFixed(1)}%</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
    <p class="audit-note">
      Each cell is 100 × share × rating ÷ 5 - the points that criterion handed that
      option. The row adds up to the score; nothing else is added anywhere.
    </p>`;
}

function auditRobustness(audit) {
  const r = audit.robustness;
  const checks = [
    {
      pass: r.dominates,
      label: "Wins on every criterion",
      yes: `${escapeHtml(audit.margin.winner)} is never worse than ${escapeHtml(audit.margin.runner_up)} on anything.`,
      no: `${escapeHtml(audit.margin.winner)} gives something up - it leads on ${r.criteria_won} of ${r.criteria_total} criteria${r.criteria_tied ? ` and ties on ${r.criteria_tied}` : ""}.`,
    },
    {
      pass: !r.weight_dependent,
      label: "Survives ignoring your weights",
      yes: "Score every criterion equally and the same option still wins - the result does not rest on the weighting.",
      no: `Weighted equally, ${escapeHtml(r.equal_weight_winner || "another option")} would win instead. This answer depends on the weights you set - worth a second look at them.`,
    },
    {
      pass: ["unconditional", "robust", "stable"].includes(r.verdict),
      label: "Survives a wrong rating",
      yes: audit.sensitivity.min_rating_change == null
        ? "No single rating, moved anywhere on the scale, changes the answer."
        : `A rating would have to be off by ${audit.sensitivity.min_rating_change.toFixed(1)} points before this flips.`,
      no: r.verdict === "tie"
        ? "The two are level on the numbers, so any nudge at all decides it."
        : `A single rating being off by ${(audit.sensitivity.min_rating_change ?? 0).toFixed(1)} points would flip it.`,
    },
  ];
  return `<ul class="audit-checks">${checks.map((c) => `
    <li class="${c.pass ? "pass" : "fail"}">
      <span class="mark" aria-hidden="true">${c.pass ? "✓" : "!"}</span>
      <div><strong>${c.label}</strong><span>${c.pass ? c.yes : c.no}</span></div>
    </li>`).join("")}</ul>`;
}

function auditSensitivity(audit) {
  const s = audit.sensitivity;
  const { winner, runner_up } = audit.margin;
  const weightRows = s.weight_tests.map((t) => `
    <li>
      <strong>${escapeHtml(t.criterion)}</strong> carries ${t.current_pct}% of the score.
      Move it ${t.direction} to <strong>${t.flip_at_pct}%</strong>
      (a ${t.relative_pct}% change) and ${escapeHtml(runner_up)} takes over.
    </li>`).join("");
  const ratingRows = s.rating_tests.map((t) => `
    <li>
      <strong>${escapeHtml(t.option)}</strong> on ${escapeHtml(t.criterion)}:
      ${t.direction === "down" ? "drop" : "raise"} it from ${t.from} to
      <strong>${t.to}</strong> (${t.points.toFixed(2)} points) and the answer changes.
    </li>`).join("");

  return `
    <p class="audit-lead">
      ${escapeHtml(winner)} leads ${escapeHtml(runner_up)} by
      <strong>${s.margin_pts} points</strong>. Here is exactly how far something
      would have to move before that stops being true.
    </p>
    <div class="audit-split">
      <div>
        <h5>If a criterion mattered more, or less</h5>
        ${weightRows ? `<ul class="audit-tests">${weightRows}</ul>`
          : `<p class="muted">No single criterion's weight can be changed enough to flip this.</p>`}
      </div>
      <div>
        <h5>If a rating were wrong</h5>
        ${ratingRows ? `<ul class="audit-tests">${ratingRows}</ul>`
          : `<p class="muted">No single rating change flips this result.</p>`}
      </div>
    </div>
    ${s.critical_criterion ? `
      <p class="audit-note">
        <strong>${escapeHtml(s.critical_criterion)}</strong> is the most critical
        criterion here - the smallest change to it does the most damage. If any
        number in this decision is worth double-checking, it is that one.
      </p>` : ""}`;
}

function auditPanel(audit, behavioralLinks) {
  if (!audit || !audit.weights || !audit.weights.length) return "";
  const { method, margin, robustness } = audit;
  return `
    <details class="audit">
      <summary class="audit-summary">
        <span class="audit-kicker">AUDIT</span>
        <span class="audit-summary-text">
          How this answer was produced - and how much it would take to change it
        </span>
        <span class="audit-verdict-chip v-${robustness.verdict}">
          ${VERDICT_COPY[robustness.verdict] || robustness.verdict}
        </span>
      </summary>

      <div class="audit-body">
        <div class="audit-headline v-${robustness.verdict}">${escapeHtml(robustness.headline)}</div>

        <section class="audit-sec">
          <h4><i>1</i> The method</h4>
          <p class="audit-lead">
            <strong>${escapeHtml(method.name)}</strong> - ${escapeHtml(method.family)}.
            It is a compensatory rule: a strong showing on a heavy criterion can
            offset a weak one elsewhere, which is what your head stops doing once
            a comparison gets big.
          </p>
          <code class="audit-formula">${formula(method.formula)}</code>
          <ol class="audit-steps">
            ${method.steps.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}
          </ol>
          <cite class="bs-source">${escapeHtml(method.source)}</cite>
        </section>

        <section class="audit-sec">
          <h4><i>2</i> What each criterion is worth</h4>
          ${auditWeights(audit)}
        </section>

        <section class="audit-sec">
          <h4><i>3</i> Where every point came from</h4>
          ${auditContributions(audit)}
        </section>

        <section class="audit-sec">
          <h4><i>4</i> Why this option and not the runner-up</h4>
          <p class="audit-lead">
            <strong>${escapeHtml(margin.winner)}</strong> scored
            ${margin.winner_score.toFixed(1)}% against
            ${escapeHtml(margin.runner_up)}'s ${margin.runner_up_score.toFixed(1)}% -
            a gap of ${margin.gap_pts} points, or ${margin.clarity_pct}% of the leader.
            ${margin.decisive_criterion ? `Most of that came from
              <strong>${escapeHtml(margin.decisive_criterion)}</strong>, which supplied
              ${margin.decisive_share_pct}% of the winner's total.` : ""}
          </p>
          ${auditRobustness(audit)}
        </section>

        <section class="audit-sec">
          <h4><i>5</i> How wrong could this be?</h4>
          ${auditSensitivity(audit)}
        </section>

        ${behavioralLinks && behavioralLinks.length ? `
          <section class="audit-sec">
            <h4><i>6</i> The behavioural science behind it</h4>
            <p class="audit-lead">
              The arithmetic above is only half the story. These are the documented
              effects this particular decision sits on top of.
            </p>
            ${bsList(behavioralLinks)}
          </section>` : ""}

        <section class="audit-sec">
          <h4><i>${behavioralLinks && behavioralLinks.length ? 7 : 6}</i> What this method assumes</h4>
          <p class="audit-lead">
            Stated plainly, because a model you cannot argue with is not an audit.
          </p>
          <ul class="audit-assumptions">
            ${method.assumptions.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}
          </ul>
        </section>
      </div>
    </details>`;
}

/* ---- confidence, predicted satisfaction, and the feedback loop ----
   Two gauges with different jobs, so they are labelled as different jobs.
   Confidence is a property of the decision itself and is computed the same
   way for everybody. Satisfaction is a prediction about *this* user, learned
   from the decisions they have rated. Both ship their own arithmetic: every
   note below names the points it contributed and why, so neither number is
   ever just asserted. */

const BAND_CLASS = {
  "Very high": "band-vhigh", High: "band-high", Moderate: "band-mid",
  Low: "band-low", "Very low": "band-vlow",
};

/* A semicircular meter rather than a full ring: at the width of a half-modal
   column a closed ring leaves the number nowhere to sit, and the digits end up
   colliding with the stroke. The open bottom gives the reading its own room,
   and the band word rides underneath it instead of in a separate pill that
   would wrap the title onto two lines. */
function gauge(score, label, band) {
  const R = 80;
  const LENGTH = Math.PI * R;                 // semicircle, centred at (100,100)
  const shown = Math.min(Math.max(score, 0), 100);
  const offset = LENGTH * (1 - shown / 100);
  const arc = `M20 100 A ${R} ${R} 0 0 1 180 100`;
  return `
    <div class="gauge ${BAND_CLASS[band] || "band-mid"}" role="img"
         aria-label="${escapeHtml(label)} ${score.toFixed(0)} percent, ${escapeHtml(band)}">
      <svg class="gauge-svg" viewBox="0 0 200 112" aria-hidden="true">
        <path class="gauge-track" d="${arc}"/>
        <path class="gauge-arc" d="${arc}"
              stroke-dasharray="${LENGTH.toFixed(2)}"
              stroke-dashoffset="${offset.toFixed(2)}"/>
      </svg>
      <div class="gauge-read">
        <span class="gauge-num">${score.toFixed(0)}<i>%</i></span>
        <span class="gauge-band">${escapeHtml(band)}</span>
      </div>
    </div>`;
}

function scorePanel(kind, data, title, subtitle) {
  // Confidence notes are "points out of a maximum"; satisfaction notes are
  // signed adjustments to a baseline. Same shape, different reading.
  const signed = kind === "satisfaction";
  return `
    <div class="score-card" data-score="${kind}">
      <div class="score-card-id">
        <div class="score-card-title">${escapeHtml(title)}</div>
        <p class="score-card-sub">${escapeHtml(subtitle)}</p>
      </div>
      ${gauge(data.score, title, data.label)}
      <p class="score-card-advice">${escapeHtml(data.advice || data.basis || "")}</p>
      <details class="score-audit">
        <summary>Why ${data.score.toFixed(0)}% - the audit</summary>
        <div class="score-notes">
          ${(data.notes || []).map((note) => {
            const value = signed
              ? `${note.points > 0 ? "+" : ""}${note.points.toFixed(0)} pts`
              : `${note.points.toFixed(1)} / ${note.max.toFixed(0)}`;
            const fill = signed
              ? Math.min(100, Math.abs(note.points) * 2)
              : (note.points / note.max) * 100;
            return `
              <div class="score-note${signed && note.points < 0 ? " is-negative" : ""}">
                <div class="score-note-head">
                  <span>${escapeHtml(note.label)}</span>
                  <strong>${value}</strong>
                </div>
                <div class="score-note-track">
                  <div class="score-note-fill" style="width:${fill.toFixed(0)}%"></div>
                </div>
                <p>${escapeHtml(note.why)}</p>
              </div>`;
          }).join("")}
        </div>
        <p class="score-method">${escapeHtml(data.method || "")}</p>
      </details>
    </div>`;
}

const levelDots = (level, max) =>
  Array.from({ length: max }, (_, i) => `<i class="${i < level ? "on" : ""}"></i>`).join("");

function assessmentPanel(assessment) {
  if (!assessment) return "";
  const conf = assessment.confidence;
  const sat = assessment.satisfaction;
  const learning = assessment.learning;
  return `
    <div class="dec-section assess">
      <div class="assess-head">
        <span class="dec-label" style="margin:0">How much to trust this</span>
        ${learning ? `
          <span class="level-chip" title="${escapeHtml(learning.blurb)}">
            <span class="level-dots">${levelDots(learning.level, learning.max_level)}</span>
            Level ${learning.level} · ${escapeHtml(learning.label)}
          </span>` : ""}
      </div>
      <div class="assess-grid">
        ${scorePanel("confidence", conf, "Confidence",
          "How much this recommendation deserves to be trusted, judged on the decision's own structure.")}
        ${scorePanel("satisfaction", sat, "Predicted satisfaction",
          "How happy you are likely to be with it, learned from the decisions you have rated.")}
      </div>
      ${learning && !learning.enabled ? `
        <p class="assess-off">Learning from your decisions is switched off for
          this account, so the satisfaction estimate cannot adapt to you. You can
          turn it on under Profile, in Data &amp; privacy.</p>` : ""}
    </div>`;
}

const OUTCOME_OPTIONS = [
  ["followed", "I went with it", "The recommendation was right."],
  ["modified", "I adapted it", "Roughly right, but I changed something."],
  ["rejected", "I did something else", "The call was wrong for me."],
];

function feedbackPanel(r) {
  const given = r.feedback;
  if (given) {
    const predicted = given.predicted_satisfaction;
    const actual = given.satisfaction_pct;
    const error = predicted == null ? null : actual - predicted;
    const label = (OUTCOME_OPTIONS.find((o) => o[0] === given.outcome) || [])[1] || given.outcome;
    return `
      <div class="dec-section fb-card is-done">
        <div class="fb-head">
          <span class="dec-label" style="margin:0">Your verdict</span>
          <span class="tag">Recorded</span>
        </div>
        <p class="fb-recorded">
          You <strong>${escapeHtml(label.toLowerCase())}</strong> and rated it
          <strong>${actual.toFixed(0)}%</strong>.
          ${predicted == null ? "" : (error === 0
            ? `We predicted ${predicted.toFixed(0)}% - exactly right.`
            : `We predicted ${predicted.toFixed(0)}%, so we were off by
               ${Math.abs(error).toFixed(0)} points; the engine has corrected for it.`)}
        </p>
        ${given.note ? `<p class="fb-note">${escapeHtml(given.note)}</p>` : ""}
        <button type="button" class="ghost-btn" id="fb-redo">Change my rating</button>
      </div>`;
  }

  return `
    <div class="dec-section fb-card" id="fb-form">
      <div class="fb-head">
        <span class="dec-label" style="margin:0">How did it actually go?</span>
        <span class="tag">Teaches the engine</span>
      </div>
      <p class="dec-help">
        This is the part that makes the next recommendation better. Your answer
        becomes a reward signal that re-weights the criteria this call was built
        on, and you can see exactly what moved straight afterwards.
      </p>
      <div class="fb-outcomes" role="radiogroup" aria-label="What did you do?">
        ${OUTCOME_OPTIONS.map(([key, label, hint]) => `
          <button type="button" class="fb-outcome" data-outcome="${key}"
                  role="radio" aria-checked="false">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(hint)}</span>
          </button>`).join("")}
      </div>
      <div class="fb-stars-row">
        <span class="fb-stars-label">How satisfied are you?</span>
        <div class="fb-stars" role="radiogroup" aria-label="Satisfaction">
          ${[1, 2, 3, 4, 5].map((n) => `
            <button type="button" class="fb-star" data-stars="${n}" role="radio"
                    aria-checked="false" aria-label="${n} out of 5">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m12 3.6 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.8l5.9-.9Z"
                      stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
              </svg>
            </button>`).join("")}
          <span class="fb-stars-pct" id="fb-pct">-</span>
        </div>
      </div>
      <input id="fb-note" type="text" maxlength="200" class="fb-note-input"
             placeholder="Anything the engine should know? (optional)" />
      <p id="fb-error" class="form-error hidden" role="alert"></p>
      <button type="button" class="btn-primary" id="fb-submit" disabled>Send feedback</button>
    </div>`;
}

/* What one piece of feedback actually changed, shown the moment it is sent.
   "The model learned from that" is a claim, and a claim about the user's own
   data should be checkable on the spot rather than taken on faith. */
function learningReceipt(response) {
  if (!response.learned) {
    return `<p class="fb-recorded">Rating saved. Learning is off for this
      account, so no weights were updated.</p>`;
  }
  const moved = response.changes || [];
  const error = response.calibration_error_pts;
  return `
    <div class="fb-receipt">
      <p><strong>Reward ${response.reward > 0 ? "+" : ""}${response.reward.toFixed(2)}</strong>
        recorded${error == null ? "" : `, and the prediction was off by
        ${Math.abs(error).toFixed(0)} points`}.</p>
      ${moved.length ? `
        <ul class="fb-changes">
          ${moved.map((change) => {
            const before = (change.before - 1) * 100;
            const after = (change.after - 1) * 100;
            const up = change.after > change.before;
            return `<li>
              <span class="fb-change-key">${escapeHtml(change.key)}</span>
              <span class="fb-change-move ${up ? "up" : "down"}">
                ${before >= 0 ? "+" : ""}${before.toFixed(0)}%
                &rarr; ${after >= 0 ? "+" : ""}${after.toFixed(0)}%
              </span>
              <em>${change.credit_pct.toFixed(0)}% of the credit</em>
            </li>`;
          }).join("")}
        </ul>` : `<p class="dec-help">No weight moved: this decision's credit
          landed on criteria already at their limit.</p>`}
    </div>`;
}

function bindFeedback() {
  const form = $("#fb-form");
  if (!form) {
    $("#fb-redo")?.addEventListener("click", () => {
      dec.result = { ...dec.result, feedback: null };
      renderDecision();
    });
    return;
  }

  const draft = { outcome: null, stars: 0 };
  const submit = $("#fb-submit");
  const sync = () => { submit.disabled = !(draft.outcome && draft.stars); };

  form.addEventListener("click", async (event) => {
    const outcome = event.target.closest(".fb-outcome");
    if (outcome) {
      draft.outcome = outcome.dataset.outcome;
      $$(".fb-outcome").forEach((b) => {
        const on = b === outcome;
        b.classList.toggle("active", on);
        b.setAttribute("aria-checked", String(on));
      });
      sync();
      return;
    }

    const star = event.target.closest(".fb-star");
    if (star) {
      draft.stars = Number(star.dataset.stars);
      $$(".fb-star").forEach((b) => {
        b.classList.toggle("on", Number(b.dataset.stars) <= draft.stars);
        b.setAttribute("aria-checked", String(Number(b.dataset.stars) === draft.stars));
      });
      // 1-5 stars map onto 0-100% exactly as the server does, so the number
      // shown here is the number the engine learns from.
      $("#fb-pct").textContent = `${((draft.stars - 1) / 4 * 100).toFixed(0)}%`;
      sync();
      return;
    }

    if (!event.target.closest("#fb-submit")) return;
    submit.disabled = true;
    submit.textContent = "Sending...";
    try {
      const response = await api.rateDecision(dec.task.id, {
        outcome: draft.outcome,
        satisfaction: draft.stars,
        chosen_option: draft.outcome === "followed" ? dec.result.best : null,
        note: $("#fb-note").value.trim() || null,
      });
      form.classList.add("is-done");
      form.innerHTML = `
        <div class="fb-head">
          <span class="dec-label" style="margin:0">Thank you - that taught it something</span>
          <span class="tag">Learned</span>
        </div>
        ${learningReceipt(response)}`;
      state.learning = null;   // the Insights panel has to refetch
      refreshAfterDecision();
    } catch (err) {
      submit.disabled = false;
      submit.textContent = "Send feedback";
      const error = $("#fb-error");
      error.textContent = err instanceof ApiError ? err.message : "Could not save that.";
      error.classList.remove("hidden");
    }
  });
}

/* ---- stage 3: result + AVEX insight ---- */

function renderDecResult() {
  const r = dec.result;
  const max = Math.max(...r.scores.map((s) => s.score), 1);
  $("#modal-body").innerHTML = `
    ${decHeader(r.ratings_source === "gemini"
      ? `Here's the call - ${AI_NAME} rated every option, the engine scored them against your profile.`
      : r.ratings_source === "engine"
        ? `Here's the call - but ${AI_NAME} couldn't rate the options, so every rating is a neutral 3.`
        : "Here's the call - based on your ratings and your decision profile.")}
    ${r.ratings_source === "engine" ? `
      <div class="dec-help" style="margin-bottom:12px">
        ${AI_NAME} rating was unavailable. Hit "Adjust ratings" below to score the options
        yourself - the result above is only a placeholder until you do.
      </div>` : ""}
    <div class="winner">
      <div>
        <div class="wk">GO WITH</div>
        <div class="wname">${escapeHtml(r.best)}</div>
      </div>
      <div class="wscore">${r.scores[0].score.toFixed(0)}%</div>
    </div>

    <div class="dec-section">
      ${r.scores.map((s) => `
        <div class="score-row">
          <span class="name">${escapeHtml(s.option)}</span>
          <div class="track"><div class="fill" style="width:${(s.score / max) * 100}%"></div></div>
          <strong>${s.score.toFixed(0)}%</strong>
        </div>`).join("")}
      <p class="clarity-note" style="margin-top:10px">
        ${Math.round(r.clarity * 100) >= 20
          ? `Clear winner - ${escapeHtml(r.best)} leads by ${Math.round(r.clarity * 100)}%. It won mainly on ${escapeHtml((r.drivers || []).join(" and ").toLowerCase())}.`
          : `Close call (${Math.round(r.clarity * 100)}% gap) - but the numbers say ${escapeHtml(r.best)}. Commit and start.`}
      </p>
    </div>

    ${r.ratings_source === "gemini" ? `
      <details class="dec-section ai-ratings">
        <summary>How ${AI_NAME} rated each option</summary>
        ${r.options.map((o) => `
          <div class="rate-block">
            <div class="rate-head">${escapeHtml(o)}</div>
            ${(r.criteria || []).map((c) => `
              <div class="rate-row ai-row">
                <span class="name">${escapeHtml(c.name)}</span>
                <span class="ai-why">${escapeHtml((r.rating_reasons?.[o]?.[c.name]) || "")}</span>
                <strong class="ai-score">${(r.ratings?.[o]?.[c.name]) ?? "-"}/5</strong>
              </div>`).join("")}
          </div>`).join("")}
      </details>` : ""}

    ${assessmentPanel(r.assessment)}

    ${r.insight ? `
      <div class="insight-panel" style="margin-bottom:16px">
        <div class="insight-head"><span>Why this choice</span>
          <span class="tag">${sourceLabel(r.insight_source)}</span></div>
        <div id="insight-body">${renderInsight(r.insight)}</div>
      </div>` : ""}

    ${feedbackPanel(r)}

    ${(r.personalization_notes || []).length ? `
      <ul class="dec-notes">${r.personalization_notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}

    ${auditPanel(r.audit, r.behavioral)}

    <div class="dec-nav">
      <button class="ghost-btn" id="dec-redo">Re-decide</button>
      <button class="ghost-btn" id="dec-adjust">Adjust ratings</button>
      <span class="spacer"></span>
      <button class="btn-primary" id="dec-close">Done</button>
    </div>`;

  bindFeedback();
  // Both of these start a fresh round of thinking, so the clock restarts:
  // the tempo series wants the time spent on *this* decision, not the time
  // since the result screen happened to be opened.
  $("#dec-redo").addEventListener("click", () => {
    decClockReset();
    dec.stage = "setup";
    renderDecision();
  });
  $("#dec-adjust").addEventListener("click", () => {
    decClockReset();
    dec.ratings = JSON.parse(JSON.stringify(r.ratings || {}));
    dec.stage = "rate";
    renderDecision();
  });
  $("#dec-close").addEventListener("click", () => {
    closeModal();
    if (state.view === "decide") renderDecide();
    if (state.view === "tasks") renderTasks();
  });
}

/* ================= assistant bubble ================= */
/* A circle in the bottom-right corner. Hovering it previews the panel;
   clicking pins the panel open so it stays put while you type. The thread
   lives in sessionStorage, so moving between tabs (or reloading) doesn't
   lose it, and logging out clears it with the rest of the user's state.

   The server rebuilds the account snapshot on every turn, so the only thing
   sent up is the conversation text - nothing here decides what AVEX is
   allowed to see. */

const CHAT_KEY = "dw_chat";
const CHAT_HISTORY_MAX = 20;     // turns replayed; matches ChatRequest's cap
const CHAT_STORE_MAX = 40;       // turns kept in sessionStorage
const CHAT_HOVER_OPEN_MS = 340;  // hover intent, so a passing cursor is ignored
const CHAT_HOVER_CLOSE_MS = 320;

const chat = {
  open: false,
  pinned: false,  // clicked open, or typed in - hover alone can't close it again
  busy: false,
  turns: [],      // { role, content, source?, error?, local? }
  openTimer: null,
  closeTimer: null,
};

const CHAT_CHIPS = [
  "What should I start with?",
  "Anything overdue?",
  "How is my score worked out?",
  "What's the matrix for?",
  "What have I decided so far?",
];

/** The opening line. Local to the browser, so it never goes into the prompt. */
function chatGreeting() {
  const who = (state.user?.name || "").trim().split(/\s+/)[0];
  return {
    role: "assistant",
    local: true,
    content:
      (who ? `Hey ${who}. ` : "Hey. ") +
      "I can see your tasks, what they score and why, and I know how every " +
      "part of this app works. Ask me either kind of thing.",
  };
}

/* The thread is stamped with whose it is. A session that ends by expiry
   rather than by the Log out button never runs chatClear, so without this the
   next person to sign in on the same tab would open the bubble on someone
   else's conversation. */
function chatLoad() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CHAT_KEY) || "null");
    if (saved && Array.isArray(saved.turns) && saved.uid === state.user?.id) {
      chat.turns = saved.turns;
    } else if (saved) {
      chatClear();
    }
  } catch { /* private mode: the thread just won't outlive the tab */ }
}

function chatSave() {
  try {
    sessionStorage.setItem(CHAT_KEY, JSON.stringify({
      uid: state.user?.id ?? null,
      turns: chat.turns.slice(-CHAT_STORE_MAX),
    }));
  } catch { /* nothing to do - the thread is still in memory */ }
}

function chatClear() {
  chat.turns = [];
  try { sessionStorage.removeItem(CHAT_KEY); } catch { /* fine */ }
}

function chatBubble(turn) {
  if (turn.role === "user") {
    return `<div class="chat-msg chat-msg--user">${escapeHtml(turn.content)}</div>`;
  }
  const cls = turn.error ? "chat-msg--error" : "chat-msg--avex";
  // Same vocabulary as the insight panels: say when the deterministic engine
  // answered instead of AVEX, rather than passing one off as the other.
  const tag = turn.source === "engine" ? `<span class="chat-tag">Engine</span>` : "";
  return `<div class="chat-msg ${cls}">${renderInsight(turn.content)}</div>${tag}`;
}

function renderChat() {
  const log = $("#chat-log");
  if (!log) return;
  log.innerHTML =
    chat.turns.map(chatBubble).join("") +
    (chat.busy
      ? `<div class="chat-msg chat-msg--avex chat-typing"><i></i><i></i><i></i></div>`
      : "");
  // The starters are a way in, so they go once the conversation has started.
  $("#chat-chips").innerHTML = chat.turns.some((t) => t.role === "user")
    ? ""
    : CHAT_CHIPS.map((c) => `<button type="button" class="chat-chip">${escapeHtml(c)}</button>`).join("");
  log.scrollTop = log.scrollHeight;
}

function chatSetOpen(open, { pin = false } = {}) {
  clearTimeout(chat.openTimer);
  clearTimeout(chat.closeTimer);
  chat.open = open;
  chat.pinned = open ? chat.pinned || pin : false;
  $("#chat-widget").dataset.open = String(open);
  $("#chat-fab").setAttribute("aria-expanded", String(open));
  $("#chat-panel").setAttribute("aria-hidden", String(!open));
  if (!open) return;
  // The opening line becomes a real turn the first time the panel is seen, so
  // it stays in the transcript instead of vanishing on the first question. It
  // is marked local, so it is never replayed into the prompt.
  if (!chat.turns.length) chat.turns.push(chatGreeting());
  renderChat();
  // Only steal focus when they meant to open it. A hover preview that grabbed
  // the caret would be maddening.
  if (pin) setTimeout(() => $("#chat-input")?.focus(), 120);
}

function chatGrow() {
  const el = $("#chat-input");
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
}

function chatError(err) {
  if (!(err instanceof ApiError)) {
    return "I couldn't reach the server. Check your connection and try again.";
  }
  if (err.status === 401) return "Your session has expired. Log in again to pick this up.";
  if (err.status === 429) return "That's a lot of questions at once. Give it a moment and try again.";
  return `Something went wrong: ${err.message}`;
}

async function chatSend(text) {
  const message = (text || "").trim();
  if (!message || chat.busy) return;

  // Built before the new message is pushed, so the server gets the turns that
  // came *before* it, oldest first, exactly as the endpoint expects.
  const history = chat.turns
    .filter((t) => !t.local && !t.error)
    .slice(-CHAT_HISTORY_MAX)
    .map((t) => ({ role: t.role, content: t.content }));

  chat.pinned = true;
  chat.busy = true;
  chat.turns.push({ role: "user", content: message });
  const input = $("#chat-input");
  input.value = "";
  chatGrow();
  $("#chat-send").disabled = true;
  renderChat();

  try {
    const result = await api.chat(message, history);
    chat.turns.push({
      role: "assistant",
      content: result.reply,
      source: result.source,
    });
  } catch (err) {
    chat.turns.push({ role: "assistant", error: true, content: chatError(err) });
  } finally {
    chat.busy = false;
    $("#chat-send").disabled = false;
    renderChat();
    chatSave();
    input.focus();
  }
}

function bindChat() {
  const widget = $("#chat-widget");
  if (!widget) return;
  const fab = $("#chat-fab");
  const input = $("#chat-input");

  // ---- hover to preview ----
  fab.addEventListener("pointerenter", (event) => {
    // Touch has no hover, and a task mid-drag must not trip this open.
    if (event.pointerType !== "mouse" || chat.open || (drag && drag.active)) return;
    clearTimeout(chat.closeTimer);
    chat.openTimer = setTimeout(() => chatSetOpen(true), CHAT_HOVER_OPEN_MS);
  });
  widget.addEventListener("pointerenter", () => clearTimeout(chat.closeTimer));
  widget.addEventListener("pointerleave", (event) => {
    clearTimeout(chat.openTimer);
    if (event.pointerType !== "mouse" || chat.pinned || !chat.open) return;
    chat.closeTimer = setTimeout(() => chatSetOpen(false), CHAT_HOVER_CLOSE_MS);
  });

  // ---- click to pin ----
  fab.addEventListener("click", () => chatSetOpen(!chat.open, { pin: true }));
  $("#chat-close").addEventListener("click", () => chatSetOpen(false));
  $("#chat-reset").addEventListener("click", () => {
    chatClear();
    chat.turns.push(chatGreeting());
    renderChat();
    input.focus();
  });

  $("#chat-chips").addEventListener("click", (event) => {
    const chip = event.target.closest(".chat-chip");
    if (chip) chatSend(chip.textContent);
  });

  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    chatSend(input.value);
  });
  input.addEventListener("input", chatGrow);
  input.addEventListener("focus", () => { chat.pinned = true; });
  input.addEventListener("keydown", (event) => {
    // Enter sends, Shift+Enter starts a new line.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      chatSend(input.value);
    }
  });

  document.addEventListener("keydown", (event) => {
    // The decision modal owns Escape while it is open.
    if (event.key !== "Escape" || !chat.open) return;
    if (!$("#modal-root").classList.contains("hidden")) return;
    chatSetOpen(false);
    fab.focus();
  });

}

/* ================= boot ================= */

function bindEvents() {
  $("#auth-form").addEventListener("submit", handleAuthSubmit);
  bindConsent();
  $("#privacy-card").addEventListener("click", handlePrivacyAction);
  $("#privacy-card").addEventListener("change", handlePrivacyToggle);
  $("#auth-switch-link").addEventListener("click", (e) => {
    e.preventDefault();
    state.authMode = state.authMode === "login" ? "register" : "login";
    applyAuthMode();
    revealAuthFields();
  });
  $("#new-user-btn").addEventListener("click", () => {
    state.authMode = "register";
    applyAuthMode();
    revealAuthFields();
    $("#auth-name").focus();
  });
  $("#theme-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-theme-choice]");
    if (btn) setTheme(btn.dataset.themeChoice);
  });
  // Only matters while the choice is "system"; applyTheme re-reads it anyway.
  darkQuery.addEventListener("change", applyTheme);

  $("#about-btn").addEventListener("click", () => {
    toast("Decide Well ranks what's on your plate with real math, then explains why.");
  });

  $("#app-nav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (btn) setView(btn.dataset.view);
  });
  $("#logout-btn").addEventListener("click", () => {
    setToken(null);
    Object.assign(state, {
      user: null, surveyAnswers: null, wizard: { step: 0, draft: {} },
      learning: null,
      consent: {
        privacy: false, personalization: false, ai_processing: false,
        age_confirmed: false,
      },
    });
    renderConsent();
    chatClear();
    chatSetOpen(false);
    showAuth();
  });

  $("#task-category").addEventListener("change", syncCategoryFields);
  bindDuePicker();
  $("#task-form").addEventListener("submit", handleTaskSubmit);
  $$(".estimate-chips button").forEach((chip) =>
    chip.addEventListener("click", () => { $("#task-estimate").value = chip.dataset.min; }));
  $("#task-filter").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.taskFilter = btn.dataset.f;
    $$("#task-filter button").forEach((b) => b.classList.toggle("active", b === btn));
    renderTasks();
  });
  $("#task-layout").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.taskLayout = btn.dataset.l;
    $$("#task-layout button").forEach((b) => b.classList.toggle("active", b === btn));
    renderTasks();
  });
  $("#task-list").addEventListener("click", handleTaskAction);
  $("#task-matrix").addEventListener("click", handleTaskAction);
  $("#task-matrix").addEventListener("pointerdown", onDragStart);
  $("#decide-list").addEventListener("click", handleTaskAction);
  $("#up-next").addEventListener("click", handleTaskAction);
  $("#explain-btn").addEventListener("click", handleExplain);

  bindChat();

  const grain = $("#tempo-grain");
  if (grain) {
    grain.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-g]");
      if (!btn) return;
      tempoGrain = btn.dataset.g;
      grain.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderTempo();
    });
  }
  // The charts are drawn at real pixel width, so a resize has to redraw them.
  // Redraw only - the numbers did not change, so there is nothing to refetch.
  let tempoResize;
  window.addEventListener("resize", () => {
    clearTimeout(tempoResize);
    tempoResize = setTimeout(() => {
      if (state.view === "insights") drawTempo();
    }, 150);
  });

  // Keep the deliberation clock honest across tab switches.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) decClockPause();
    else decClockResume();
  });

  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-root").addEventListener("click", (e) => {
    if (e.target === $("#modal-root")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#modal-root").classList.contains("hidden")) closeModal();
  });
}

async function boot() {
  bindEvents();
  applyTheme();
  applyAuthMode();
  syncCategoryFields();
  if (getToken()) {
    try {
      state.user = await api.me();
      await enterApp();
      return;
    } catch {
      setToken(null);
    }
  }
  showAuth();
}

boot();
