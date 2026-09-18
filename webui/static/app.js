"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const CATEGORIES = [["7:1", "政治理论课"], ["7:2", "第一外国语"], ["7:3", "专业外语"],
  ["8:4", "学位基础课"], ["8:5", "学位专业课"], ["8:6", "专业选修课"], ["9:9", "公共选修课"]];
const VIEWS = {dashboard: "准备与运行", config: "队列设置", logs: "活动记录"};
const FIELDS = {"start-time": "start_time", "end-time": "end_time", "cookie-source": "cookie_source",
  "browser-select": "browser", "request-interval": "request_interval", "poll-interval": "poll_interval",
  "poll-max": "poll_max", "cookie-refresh": "cookie_refresh_secs", "http-timeout": "http_timeout"};
const state = {config: null, saved: null, login: {saved: false}, verified: false, dirty: false,
  online: false, busy: false, polling: false, cursor: 0, taskId: null, output: "", status: {},
  preflight: {state: "idle", valid: false, checks: []}, catalog: [], category: "", catalogLoaded: false,
  catalogLoading: false, panel: "check", next: "login", notified: new Set(), initialized: false,
  checkError: "", runError: "", logViewStart: 0, app: null, quitting: false};
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const enabled = () => (state.config?.courses || []).filter(c => c.enabled !== false);
const date = value => value instanceof Date ? value : new Date(String(value || "").replace(" ", "T"));
const inputTime = value => String(value || "").replace(" ", "T");
const configTime = value => value.replace("T", " ") + (value.length === 16 ? ":00" : "");
const locked = () => state.busy || state.status.running || state.preflight.state === "checking";
const checkValid = () => state.preflight.valid && !state.dirty;
function icons() { window.lucide?.createIcons({attrs: {"aria-hidden": "true"}}); }
function text(id, value) { $(id).textContent = value; }
function errorAt(id, message = "") { text(id, message); $(id).hidden = !message; }
function toast(message, bad = false) {
  const region = $("#toast-region");
  ($$("dialog[open]").at(-1) || document.body).append(region);
  const item = document.createElement("div");
  item.className = `toast${bad ? " is-error" : ""}`;
  item.textContent = message;
  region.replaceChildren(item);
  setTimeout(() => item.remove(), 6000);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", ...options.headers}});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}
const post = (path, data = {}) => api(path, {method: "POST", body: JSON.stringify(data)});
function showView(view) {
  $$(".view").forEach(el => el.classList.toggle("is-active", el.id === `view-${view}`));
  $$(".nav-item").forEach(el => { el.classList.toggle("is-active", el.dataset.view === view);
    el.setAttribute("aria-current", el.dataset.view === view ? "page" : "false"); });
  text("#view-title", VIEWS[view]);
}
function openDialog(id) {
  // Keep a single modal on screen and return focus to the main action on dismissal.
  $$("dialog[open]").forEach(el => { if (el.id !== id) el.close(); });
  const dialog = $("#" + id);
  if (!dialog.open) {$("#toast-region").replaceChildren(); dialog.showModal();}
  dialog.append($("#toast-region"));
  icons();
}
function setDirty() {
  state.dirty = JSON.stringify(state.config) !== JSON.stringify(state.saved);
  renderDashboard();
}
function fillConfig() {
  for (const [id, key] of Object.entries(FIELDS)) {
    $("#" + id).value = id.endsWith("-time") ? inputTime(state.config[key]) : state.config[key];
  }
  renderEditor();
}
async function loadConfig(preserveDraft = false) {
  const payload = await api("/api/config");
  state.login = payload.login || {saved: false};
  state.app = payload.app || null;
  $("#app-options").hidden = !state.app?.control_token;
  if (state.app?.platform) text("#devtools-shortcut", state.app.platform === "darwin" ? "⌥⌘I" : "F12 或 Ctrl+Shift+I");
  state.saved = structuredClone(payload.config);
  if (!preserveDraft || !state.dirty) {
    state.config = payload.config; state.dirty = false; fillConfig();
  }
  renderDashboard();
}
async function saveConfig() {
  const payload = await api("/api/config", {method: "PUT", body: JSON.stringify({config: state.config})});
  state.config = payload.config; state.saved = structuredClone(payload.config); state.dirty = false;
  fillConfig(); renderDashboard();
}
function renderEditor() {
  $("#course-editor").innerHTML = state.config.courses.map((c, i) => `
    <div class="course-row" data-index="${i}">
      <span class="course-rank">${String(i + 1).padStart(2, "0")}</span>
      <div class="editor-course"><strong>${escapeHtml(c.name)}</strong><small>${escapeHtml(c.bjdm)} · ${escapeHtml(category(c))}</small></div>
      <label class="switch-field"><input type="checkbox" data-enabled ${c.enabled !== false ? "checked" : ""}><span>参与</span></label>
      <div class="course-tools"><button class="course-tool" type="button" data-move="-1" aria-label="提高 ${escapeHtml(c.name)} 优先级" ${i === 0 ? "disabled" : ""}>↑</button><button class="course-tool" type="button" data-move="1" aria-label="降低 ${escapeHtml(c.name)} 优先级" ${i === state.config.courses.length - 1 ? "disabled" : ""}>↓</button><button class="course-tool is-delete" type="button" data-remove aria-label="删除 ${escapeHtml(c.name)}">×</button></div>
    </div>`).join("");
  $("#editor-empty").hidden = state.config.courses.length > 0;
}
function category(c) { return CATEGORIES.find(([key]) => key === `${c.lx}:${c.bqmc}`)?.[1] || "其他课程"; }
function formatTime(value) {
  const parsed = date(value);
  return Number.isNaN(parsed.getTime()) ? "未设置" : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(parsed);
}
function renderDashboard() {
  if (!state.config) return;
  const checking = state.preflight.state === "checking";
  const task = state.status;
  const failure = state.preflight.checks?.find(c => !c.ok);
  const loginBad = failure?.key === "login";
  const logged = state.login.saved && !loginBad;
  const valid = checkValid();
  state.next = task.running ? "status" : checking ? "check" : !logged ? "login" : !enabled().length ? "courses" : !valid ? "check" : "run";
  const copy = {
    login: ["登录", "连接复旦选课系统", "保存登录态后，直接浏览当前账号可选的教学班。", "开始登录", "key-round"],
    courses: ["选课", "把想选的课加入队列", "按课程类别、教师与时间筛选，最多启用 10 个教学班。", "选择课程", "book-open-check"],
    check: ["自检", checking ? "正在检查运行条件" : "确认课程与登录态", checking ? "自检在后台继续，可以暂时收起窗口。" : "检查登录和选课资格，通过后设置运行时间。", checking ? "查看自检" : "运行前自检", "shield-check"],
    run: ["运行", "准备完成，可以安排运行", "选择按时运行或立即开始，并确认截止时间。", "设置运行", "clock-3"],
    status: ["运行中", task.stopping ? "正在停止任务" : "任务正在后台运行", "查看提交反馈与已确认的选课结果。", "查看实时状态", "radio-tower"],
  }[state.next];
  text("#next-action-kicker", `下一步 · ${copy[0]}`); text("#next-action-title", copy[1]);
  text("#next-action-detail", copy[2]); text("#next-action-label", copy[3]);
  if ($("#next-action-icon").dataset.icon !== copy[4]) {
    $("#next-action-icon").dataset.icon = copy[4]; $("#next-action-icon").innerHTML = `<i data-lucide="${copy[4]}"></i>`; icons();
  }
  const states = {login: logged ? "已保存" : "待登录", courses: enabled().length ? `${enabled().length} 个班次` : "待选择",
    check: checking ? "检查中" : valid ? "已通过" : state.preflight.state === "failed" ? "需处理" : "待自检",
    run: task.running ? "已启动" : "待启动", status: task.running ? "实时更新" : task.action ? "查看结果" : "暂无任务"};
  const done = {login: logged, courses: !!enabled().length, check: valid, run: !!task.running};
  text("#workflow-progress-label", `${Object.values(done).filter(Boolean).length} / 4 已就绪`);
  for (const [key, label] of Object.entries(states)) {
    text(`#flow-${key}-state`, label);
    const step = $(`[data-flow-step="${key}"]`);
    step.classList.toggle("is-current", key === state.next); step.classList.toggle("is-done", !!done[key]);
    step.setAttribute("aria-current", key === state.next ? "step" : "false");
  }
  text("#summary-cookie", loginBad ? "需重新登录" : valid ? "已验证" : logged ? "已保存" : "待登录");
  text("#summary-cookie-note", valid ? "最近自检通过" : logged ? "运行前会再次验证" : "先连接选课系统");
  text("#summary-courses", `${enabled().length} 个班次`);
  text("#summary-courses-note", `队列共 ${state.config.courses.length} 个班次`);
  text("#summary-start", formatTime(state.config.start_time));
  text("#config-file-state", state.dirty ? "有未保存的修改" : "设置已保存"); $("#dirty-dot").hidden = !state.dirty;
  $("#queue-empty").hidden = !!state.config.courses.length; $(".queue-table").hidden = !state.config.courses.length;
  $("#queue-body").innerHTML = state.config.courses.map((c, i) => `<tr><td>${String(i + 1).padStart(2, "0")}</td><td class="course-name-cell"><strong>${escapeHtml(c.name)}</strong><small>${escapeHtml(c.kcdm)}</small></td><td>${escapeHtml(c.bjdm)}</td><td>${escapeHtml(category(c))}</td><td><span class="status-tag ${c.enabled === false ? "is-off" : ""}">${c.enabled === false ? "停用" : "已加入"}</span></td></tr>`).join("");
  const expired = date(state.config.end_time) <= new Date();
  $("#config-notice").hidden = !expired && !state.dirty;
  text("#config-notice-text", expired ? "截止时间已过，启动前请更新运行计划。" : "队列有修改，请保存后重新自检。");
  $("#next-action-button").disabled = !state.online || state.busy;
  const freeze = locked();
  $$("#config-form input:not([data-static]), #config-form select, #config-form button").forEach(el => {el.disabled = freeze;});
  $$("#course-editor [data-move]").forEach(el => {
    const i = +el.closest("[data-index]").dataset.index;
    el.disabled = freeze || i + +el.dataset.move < 0 || i + +el.dataset.move >= state.config.courses.length;
  });
  $$("#dashboard-add-course, #empty-add-course").forEach(el => {el.disabled = freeze || !state.online;});
  updateCountdown();
}
function updateCountdown() {
  if (!state.config) return;
  const seconds = Math.ceil((date(state.config.start_time) - Date.now()) / 1000);
  text("#summary-countdown", seconds > 0 ? (seconds > 86400 ? `${Math.floor(seconds / 86400)} 天后` :
    `${String(Math.floor(seconds / 3600)).padStart(2, "0")}:${String(Math.floor(seconds % 3600 / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")} 后`) : "计划开始时间已到");
}
async function go(step) {
  if (!state.config) return;
  if (state.status.running) return openWorkflow("monitor");
  if (state.preflight.state === "checking") return openWorkflow("check");
  if (state.busy) return;
  if (step === "status") return openWorkflow("monitor");
  if (step === "login" || !state.login.saved || state.preflight.checks?.some(c => c.key === "login" && !c.ok)) return openLogin();
  if (step === "courses" || !enabled().length) return openCatalog();
  if (step === "run" && checkValid()) return openWorkflow("run");
  openWorkflow("check");
}
function openLogin() {
  errorAt("#cookie-error"); $("#cookie-input").value = ""; $("#cookie-input").classList.add("is-masked");
  $("#toggle-cookie").setAttribute("aria-label", "显示内容"); openDialog("cookie-dialog");
}
async function importCookie(event) {
  event.preventDefault(); if (locked()) return;
  state.busy = true; $("#save-cookie").disabled = true; errorAt("#cookie-error");
  try {
    await api("/api/cookie", {method: "PUT", body: JSON.stringify({cookie: $("#cookie-input").value})});
    $("#cookie-input").value = ""; state.verified = true;
    await loadConfig(true); state.preflight = {state: "idle", valid: false, checks: []};
    toast("登录态已验证，接下来选择课程");
    if ($("#cookie-dialog").open) { state.busy = false; await openCatalog(); }
  } catch (error) { errorAt("#cookie-error", error.message); }
  finally { state.busy = false; $("#save-cookie").disabled = false; renderDashboard(); }
}
async function openCatalog() {
  if (locked()) return;
  state.category = "";
  $$("#catalog-content input, #catalog-content select").forEach(el => {el.value = "";});
  openDialog("catalog-dialog"); await loadCatalog();
}
async function loadCatalog() {
  if (state.catalogLoading || locked()) return;
  state.catalogLoading = true; state.catalogLoaded = false;
  $("#catalog-loading").hidden = false; $("#catalog-error").hidden = true; $("#catalog-content").hidden = true;
  $("#refresh-catalog").disabled = true; $("#finish-catalog").disabled = true;
  try {
    const payload = await api("/api/courses"); state.catalog = payload.courses; state.verified = true;
    for (const key of ["department", "campus"]) {
      const values = [...new Set(state.catalog.map(c => c[key]).filter(Boolean))].sort();
      $(`#catalog-${key}`).innerHTML = `<option value="">全部${key === "department" ? "院系" : "校区"}</option>` + values.map(v => `<option>${escapeHtml(v)}</option>`).join("");
    }
    state.catalogLoaded = true; $("#catalog-content").hidden = false; renderCatalog();
  } catch (error) {
    $("#catalog-error").hidden = false; text("#catalog-error-text", error.message); text("#catalog-meta", "查询失败，可重试或重新登录");
  } finally {state.catalogLoading = false; $("#catalog-loading").hidden = true; $("#refresh-catalog").disabled = false; renderDashboard();}
}
function renderCatalog() {
  const focused = document.activeElement;
  const restoreFocus = focused?.hasAttribute("data-category") ? `[data-category="${focused.dataset.category}"]`
    : focused?.hasAttribute("data-catalog-index") ? `[data-catalog-index="${focused.dataset.catalogIndex}"]` : null;
  const query = $("#catalog-search").value.toLowerCase().trim();
  const selected = new Set(state.config.courses.map(c => c.bjdm));
  const categories = [["", "全部课程"], ...CATEGORIES];
  $("#catalog-tabs").innerHTML = categories.map(([key, label]) => `<button type="button" class="catalog-tab ${state.category === key ? "is-active" : ""}" data-category="${key}" aria-pressed="${state.category === key}">${label}<span>${state.catalog.filter(c => !key || `${c.lx}:${c.bqmc}` === key).length}</span></button>`).join("");
  const visible = state.catalog.map((c, i) => ({c, i})).filter(({c}) => {
    if (query && ![c.name, c.kcdm, c.teacher, c.bjdm].join(" ").toLowerCase().includes(query)) return false;
    if (state.category && `${c.lx}:${c.bqmc}` !== state.category) return false;
    for (const key of ["department", "campus"]) if ($(`#catalog-${key}`).value && c[key] !== $(`#catalog-${key}`).value) return false;
    const conflict = $("#catalog-conflict").value, capacity = $("#catalog-capacity").value;
    return !(conflict === "no" && c.conflict || conflict === "yes" && !c.conflict || capacity === "full" && !c.full || capacity === "available" && !(c.remaining > 0));
  });
  $("#catalog-list").innerHTML = visible.map(({c, i}) => `<div class="catalog-row ${selected.has(c.bjdm) ? "is-selected" : ""}">
    <div class="catalog-course"><strong>${escapeHtml(c.name)}</strong><small>${escapeHtml(c.bjdm)} · ${escapeHtml(c.category || category(c))}</small><small class="catalog-mobile-meta">${escapeHtml(c.teacher || "教师待公布")}</small></div>
    <div class="catalog-teacher"><span>${escapeHtml(c.teacher || "教师待公布")}</span><small>${escapeHtml(c.department || "院系待公布")}</small></div>
    <div class="catalog-schedule"><span>${escapeHtml(c.schedule || "时间待公布")}</span><small>${escapeHtml([c.location, c.campus].filter(Boolean).join(" · "))}${c.conflict ? ' <span class="conflict-text">时间冲突</span>' : ""}</small></div>
    <span class="capacity-state ${c.full ? "is-full" : ""}">${c.full ? "已满" : c.remaining == null ? "人数未知" : `余 ${Number(c.remaining)}`}<small>${c.full ? "可加入等待" : "以提交时为准"}</small></span>
    <button class="catalog-add course-tool ${selected.has(c.bjdm) ? "is-added" : ""}" type="button" data-catalog-index="${i}" aria-label="${selected.has(c.bjdm) ? "移除" : "加入"} ${escapeHtml(c.name)} ${escapeHtml(c.bjdm)}" ${!c.selectable && !selected.has(c.bjdm) ? "disabled" : ""}>${selected.has(c.bjdm) ? "✓" : "+"}</button></div>`).join("");
  $("#catalog-list").hidden = !visible.length; $("#catalog-empty").hidden = !!visible.length;
  text("#catalog-meta", `当前账号可选范围 · ${visible.length} / ${state.catalog.length} 个教学班`);
  text("#catalog-selection", `已启用 ${enabled().length} / 10 个班次 · 点击 ✓ 可移除`);
  $("#finish-catalog").disabled = !enabled().length || !state.catalogLoaded || state.busy;
  if (restoreFocus) $(restoreFocus)?.focus({preventScroll: true});
}
function toggleCourse(index) {
  if (locked()) return;
  const c = state.catalog[index]; if (!c) return;
  const existing = state.config.courses.findIndex(item => item.bjdm === c.bjdm);
  if (existing >= 0) state.config.courses.splice(existing, 1);
  else {
    if (!c.selectable) return;
    if (enabled().length >= 10) return toast("最多启用 10 个教学班，请先移除一个。", true);
    state.config.courses.push({name: c.name, kcdm: c.kcdm, bjdm: c.bjdm, lx: c.lx, bqmc: c.bqmc, enabled: true});
  }
  setDirty(); renderEditor(); renderCatalog();
}
function openWorkflow(panel) {
  state.panel = panel;
  const names = {check: ["03 / 自检", "运行前自检", "仅检查登录与课程资格，不提交选课。"],
    run: ["04 / 运行", "安排本次运行", "确认时间后启动，满员课程会持续尝试。"],
    monitor: ["05 / 抢课提示", "本次任务状态", "收起窗口不会停止后台任务。"]};
  text("#workflow-dialog-kicker", "步骤 " + names[panel][0]); text("#workflow-dialog-title", names[panel][1]);
  text("#workflow-dialog-subtitle", names[panel][2]);
  $$("[data-workflow-panel]").forEach(el => {el.hidden = el.dataset.workflowPanel !== panel;});
  if (panel === "run") {
    $("#run-start-time").value = inputTime(state.config.start_time); $("#run-end-time").value = inputTime(state.config.end_time);
    updateRun();
  }
  renderCheck(); renderMonitor(); openDialog("workflow-dialog");
}
function renderCheck() {
  const check = state.preflight, checking = check.state === "checking", valid = checkValid();
  const rows = [["login", "login"], ["courses", "courses"]];
  for (const [id, key] of rows) {
    const result = check.checks?.find(c => c.key === key);
    const ok = (valid || checking) && result?.ok;
    text(`#check-${id}-text`, result?.ok && !ok ? "上次验证通过，运行前需重新检查" : result?.message || (id === "login" ? "验证已保存的登录态" : `${enabled().length} 个教学班，检查当前选课资格`));
    text(`#check-${id}-badge`, result && !result.ok ? "需处理" : ok ? "已通过" : checking ? "检查中" : "待自检");
    $(`#check-${id}-row`).dataset.state = result && !result.ok ? "failed" : ok ? "passed" : "idle";
  }
  text("#check-system-text", valid ? "系统已响应，近期检查有效" : checking ? "正在连接复旦选课系统" : "自检结果在 5 分钟内有效，改课后需重新检查");
  text("#check-system-badge", valid ? "已通过" : checking ? "检查中" : "待自检");
  $("#check-system-row").dataset.state = valid ? "passed" : "idle";
  const failure = check.checks?.find(c => !c.ok);
  if (!state.busy) errorAt("#check-error", state.checkError || failure?.message || "");
  $("#check-terminal").hidden = true;
  $("#check-primary").disabled = checking || state.busy || !state.online || !!state.status.running;
  text("#check-primary span", checking ? "正在自检…" : valid ? "通过，设置运行" : failure ? "重新自检" : "开始自检");
  $("#check-back").disabled = checking || state.busy;
}
async function beginCheck() {
  if (locked()) return;
  if (!enabled().length) return go("courses");
  state.busy = true; state.checkError = ""; errorAt("#check-error"); renderDashboard(); renderCheck();
  try {
    if (state.dirty || !state.saved) await saveConfig();
    state.preflight = await post("/api/preflight");
  } catch (error) { state.checkError = error.message; toast(error.message, true); }
  finally {state.busy = false; renderDashboard(); renderCheck();}
}
function updateRun() {
  const nowMode = $('input[name="run-mode"]:checked').value === "now";
  $("#run-start-time").disabled = nowMode;
  text("#run-course-count", `${enabled().length} 个班次`); text("#run-burst-summary", "1 秒窗口内发起");
  const end = date($("#run-end-time").value), start = date($("#run-start-time").value);
  const invalid = !Number.isFinite(+end) || end <= new Date() || !nowMode && (!Number.isFinite(+start) || end <= start);
  const note = invalid ? "请设置有效的开始时间和未来的截止时间。" : nowMode || start <= new Date()
    ? "确认后将立即提交真实选课请求，持续到截止时间。" : `将在 ${formatTime(start)} 开始提交真实选课请求。`;
  text("#run-notice-text", state.runError || note);
  $("#start-run").disabled = invalid || !checkValid() || state.busy || !state.online;
}
async function startRun() {
  if (locked()) return;
  if (!checkValid()) return openWorkflow("check");
  const mode = $('input[name="run-mode"]:checked').value;
  const start = mode === "now" ? localInput(new Date()) : $("#run-start-time").value;
  state.config.start_time = configTime(start); state.config.end_time = configTime($("#run-end-time").value);
  state.runError = ""; state.busy = true; updateRun();
  try {
    await saveConfig();
    const response = await post("/api/tasks", {action: mode, require_preflight: true});
    state.output = ""; state.cursor = 0; state.taskId = null;
    applyTask(response);
    if ($("#workflow-dialog").open) openWorkflow("monitor");
    toast(mode === "now" ? "任务已启动" : "定时任务已就绪");
  } catch (error) {state.runError = error.message; toast(error.message, true);}
  finally {state.busy = false; renderDashboard(); if (state.panel === "run") updateRun();}
}
function localInput(d) {
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function nextWindow() {
  const release = new Date(); let found = false;
  for (let offset = 0; offset < 2 && !found; offset++) for (const hour of [10, 13]) {
    release.setTime(Date.now()); release.setDate(release.getDate() + offset); release.setHours(hour, 0, 0, 0);
    if (release > new Date()) {found = true; break;}
  }
  state.config.start_time = configTime(localInput(release));
  state.config.end_time = configTime(localInput(new Date(+release + 30 * 60 * 1000)));
  fillConfig(); setDirty();
}
function taskPresentation() {
  const s = state.status, tail = state.output.slice(-3500);
  if (!s.action) return ["idle", "尚未启动任务", "完成自检后安排运行。"];
  if (s.running) {
    if (s.stopping) return ["running", "正在停止", "正在等待后台进程结束。"];
    if (s.action === "scheduled" && date(state.config.start_time) > new Date()) return ["running", "已就绪，等待开抢", `计划 ${state.config.start_time} 开始。请保持电脑唤醒和网络连接。`];
    const last = tail.split("\n").filter(line => /★ 选上|数据缓存中|容量已满|满员|Cookie 已失效|恢复失败|操作频繁|请求失败/.test(line)).at(-1) || "";
    if (/★ 选上/.test(last)) return ["running", "已收到选课成功反馈", "其余教学班继续处理，结果请在学校系统核对。"];
    if (/数据缓存中/.test(last)) return ["running", "系统暂未开放", "服务器提示数据缓存中，任务将继续按配置重试。"];
    if (/满员|容量已满/.test(last)) return ["running", "暂无席位，继续尝试", "满员不会自动放弃，将持续到截止时间。"];
    if (/失效|恢复失败|操作频繁|请求失败/.test(last)) return ["attention", "请求遇到问题", "请查看下方反馈；可停止任务后处理登录或网络问题。"];
    return ["running", "正在提交并查询结果", "提交已受理不代表选上，以最终选课结果为准。"];
  }
  if (s.exit_code < 0) return ["idle", "任务已停止", "已发出的请求可能仍在处理，请到学校系统核对。"];
  return [s.exit_code > 0 ? "attention" : "idle", "本次任务已结束", "查看下面的完成记录，并在学校已选课程页面核对结果。"];
}
function renderMonitor() {
  if (!state.config) return;
  const [tone, title, detail] = taskPresentation(), s = state.status;
  $("#monitor-status").dataset.state = tone; $("#task-pill").dataset.state = s.running ? "running" : "idle";
  text("#task-pill-text", s.running ? (s.stopping ? "正在停止" : "任务运行中") : s.action ? "任务已结束" : "当前空闲");
  text("#monitor-title", title); text("#monitor-meta", detail); text("#monitor-kicker", s.running ? "实时反馈" : "本次结果");
  text("#activity-title", title); text("#activity-meta", detail);
  text("#log-title", s.label ? `${s.label}记录` : "活动记录");
  text("#log-meta", s.started_at ? `启动于 ${s.started_at.replace("T", " ")}${s.finished_at ? ` · 结束于 ${s.finished_at.replace("T", " ")}` : ""}` : "尚未运行任务");
  for (const id of ["#terminal-state", "#monitor-terminal-state"]) text(id, s.running ? "运行中" : "已同步");
  $("#log-badge").hidden = !s.running;
  for (const id of ["#monitor-stop", "#log-stop-task"]) $(id).disabled = !s.running || s.stopping || state.busy;
  $("#monitor-stop").hidden = !s.running; $("#monitor-close").hidden = !!s.running;
  const interactive = s.running && ["login", "preselect"].includes(s.action);
  $("#terminal-input-form").hidden = !interactive;
  $("#terminal-input").disabled = !interactive; $("#terminal-input-form button").disabled = !interactive;
}
function applyTask(status) {
  const id = status.action ? `${status.action}:${status.started_at}` : null;
  const changed = state.taskId !== id;
  if (changed) { state.output = ""; state.cursor = 0; state.taskId = id; state.logViewStart = 0; }
  // If a second browser started a new task, fetch its output from offset zero.
  if (!changed || !state.initialized || status.cursor === status.output?.length) {
    state.output = (status.truncated ? "[较早日志已截断]\n" : state.output) + (status.output || "");
    state.output = state.output.slice(-200000); state.cursor = status.cursor || 0;
  }
  state.status = status;
  for (const [id, limit] of [["#terminal-output", 200000], ["#workflow-monitor-output", 12000]]) {
    const el = $(id), atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const viewOffset = Math.max(0, state.logViewStart - (state.cursor - state.output.length));
    const value = (id === "#terminal-output" ? state.output.slice(viewOffset) : state.output).slice(-limit) || (state.logViewStart ? "视图已清空，等待新的输出…" : "等待任务启动…");
    if (el.textContent !== value) {el.textContent = value; if (atEnd || changed) el.scrollTop = el.scrollHeight;}
  }
  const doneKey = `${id}:${status.finished_at}`;
  if (state.initialized && !status.running && status.finished_at && !state.notified.has(doneKey)) {
    state.notified.add(doneKey); toast("任务已结束，请查看结果并到学校系统核对。");
    if (!$('dialog[open]') || $("#workflow-dialog").open && state.panel === "monitor") openWorkflow("monitor");
  }
  renderMonitor();
}
async function pollStatus() {
  if (state.polling || state.quitting) return;
  state.polling = true;
  try {
    const status = await api(`/api/status?cursor=${state.cursor}`);
    state.online = true;
    const wasChecking = state.preflight.state === "checking";
    state.preflight = status.preflight || {state: "idle", valid: false, checks: []};
    applyTask(status);
    if (state.preflight.valid) state.verified = true;
    if (wasChecking && state.preflight.state !== "checking") {
      toast(state.preflight.valid ? "自检通过，可以设置运行" : "自检未通过，请查看需要处理的项目", !state.preflight.valid);
    }
    text("#connection-text", "本地服务已连接"); $("#connection-dot").className = "connection-dot is-online";
    renderDashboard(); renderCheck(); if (state.panel === "run") updateRun();
    if (!state.initialized) {
      state.initialized = true;
      if (status.finished_at) state.notified.add(`${state.taskId}:${status.finished_at}`);
      if (status.running) openWorkflow("monitor");
      else if (state.preflight.state === "checking") openWorkflow("check");
    }
  } catch (error) {
    state.online = false; text("#connection-text", "连接中断，正在重连");
    $("#connection-dot").className = "connection-dot is-offline";
    renderDashboard(); renderCheck(); if (state.panel === "run") updateRun();
  } finally {state.polling = false;}
}
async function stopTask() {
  if (!state.status.running || state.busy) return;
  const dialog = $("#confirm-dialog"); dialog.returnValue = "";
  text("#dialog-title", "停止当前任务？"); text("#dialog-message", "停止后不再重试，已经提交的选课结果请到学校系统核对。");
  text("#dialog-confirm", "停止任务"); dialog.showModal();
  const confirmed = await new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
  if (!confirmed) return;
  try {await post("/api/stop"); await pollStatus();} catch (error) {toast(error.message, true);}
}
async function quitApp() {
  if (state.quitting) return;
  try {
    await api("/api/app/quit", {method: "POST", body: JSON.stringify({stop_task: !!state.status.running}), headers: {"X-FDU-App-Token": state.app.control_token}});
    state.quitting = true; state.online = false; state.dirty = false;
    $$("dialog[open]").forEach(el => el.close());
    text("#connection-text", "应用已退出"); renderDashboard(); toast("应用已退出，可以关闭此页面。");
  } catch (error) {toast(error.message, true);}
}
function bindEvents() {
  window.addEventListener("fdu-close-request", () => {
    if (!state.app?.control_token || $("#confirm-dialog").open) return;
    if (state.dirty || state.status.running) $("#quit-app").click();
    else quitApp();
  });
  $("#app-options").addEventListener("click", () => {
    text("#app-version", `版本 ${state.app.version} · ${state.app.platform === "darwin" ? "macOS" : state.app.platform === "win32" ? "Windows" : "桌面"}`);
    text("#app-data-path", state.app.data_dir);
    text("#app-lifecycle-note", state.app.desktop ? "最小化窗口可让任务继续。关闭应用窗口会退出；任务运行时会先询问是否停止。" : "关闭浏览器页面后本地服务仍会运行。使用下方“退出应用”可完整停止服务。");
    openDialog("app-dialog");
  });
  $("#close-app-dialog").addEventListener("click", () => $("#app-dialog").close());
  $("#open-data-folder").addEventListener("click", async () => {
    try {await api("/api/app/open-data", {method: "POST", body: "{}", headers: {"X-FDU-App-Token": state.app.control_token}});}
    catch (error) {toast(error.message, true);}
  });
  $("#quit-app").addEventListener("click", async () => {
    const dialog = $("#confirm-dialog"); dialog.returnValue = "";
    text("#dialog-title", "退出选课助手？");
    text("#dialog-message", state.status.running ? "退出会停止当前任务。若想继续运行，请取消并最小化窗口。" : state.dirty ? "还有未保存的修改，退出后这些修改将丢失。" : "已保存的登录态和课程设置会保留，下次打开可继续使用。");
    text("#dialog-confirm", state.status.running ? "停止并退出" : "退出应用"); dialog.showModal();
    const confirmed = await new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
    if (!confirmed) return;
    await quitApp();
  });
  $$(".nav-item").forEach(el => el.addEventListener("click", () => showView(el.dataset.view)));
  $$('[data-view-link]').forEach(el => el.addEventListener("click", () => showView(el.dataset.viewLink)));
  $$('[data-flow-step]').forEach(el => el.addEventListener("click", () => go(el.dataset.flowStep)));
  $("#next-action-button").addEventListener("click", () => go(state.next));
  $("#task-pill").addEventListener("click", () => go("status"));
  for (const id of ["dashboard-add-course", "empty-add-course", "config-empty-add-course", "add-course", "check-back"]) $("#"+id).addEventListener("click", () => go("courses"));
  for (const [button, dialog] of [["close-cookie-dialog", "cookie-dialog"], ["cancel-cookie-import", "cookie-dialog"], ["close-catalog", "catalog-dialog"], ["close-workflow-dialog", "workflow-dialog"], ["monitor-close", "workflow-dialog"]]) $("#"+button).addEventListener("click", () => $("#"+dialog).close());
  $$("dialog").forEach(dialog => dialog.addEventListener("close", () => {
    if (dialog.id === "cookie-dialog") $("#cookie-input").value = "";
    if (!$('dialog[open]')) {document.body.append($("#toast-region")); $("#next-action-button").focus();}
  }));
  $("#open-login-page").addEventListener("click", async () => {
    $("#open-login-page").disabled = true;
    try {await post("/api/login-page"); toast("已打开系统浏览器，登录后回到此窗口导入。");}
    catch (error) {errorAt("#cookie-error", error.message);} finally {$("#open-login-page").disabled = false;}
  });
  $("#cookie-import-form").addEventListener("submit", importCookie);
  $("#toggle-cookie").addEventListener("click", () => {const masked = $("#cookie-input").classList.toggle("is-masked"); $("#toggle-cookie").setAttribute("aria-label", masked ? "显示内容" : "隐藏内容");});
  $("#refresh-catalog").addEventListener("click", loadCatalog);
  $("#catalog-search").addEventListener("input", renderCatalog);
  $$(".catalog-filter select").forEach(el => el.addEventListener("change", renderCatalog));
  $("#catalog-tabs").addEventListener("click", e => {const tab = e.target.closest("[data-category]"); if (tab) {state.category = tab.dataset.category; renderCatalog();}});
  $("#catalog-list").addEventListener("click", e => {const button = e.target.closest("[data-catalog-index]"); if (button) toggleCourse(+button.dataset.catalogIndex);});
  $("[data-catalog-login]").addEventListener("click", openLogin);
  $("#finish-catalog").addEventListener("click", async () => {
    if (locked()) return;
    state.busy = true; $("#finish-catalog").disabled = true;
    try {await saveConfig(); state.preflight.valid = false;
      if ($("#catalog-dialog").open) openWorkflow("check");
      state.busy = false; await beginCheck();}
    catch (error) {toast(error.message, true);} finally {state.busy = false; renderDashboard(); renderCatalog();}
  });
  $("#check-primary").addEventListener("click", () => checkValid() ? openWorkflow("run") : beginCheck());
  $("#run-back").addEventListener("click", () => openWorkflow("check"));
  $$(".mode-switch input, .run-time-fields input").forEach(el => el.addEventListener("input", () => {state.runError = ""; updateRun();}));
  $("#start-run").addEventListener("click", startRun);
  $("#monitor-stop").addEventListener("click", stopTask); $("#log-stop-task").addEventListener("click", stopTask);
  $("#open-full-log").addEventListener("click", () => {$("#workflow-dialog").close(); showView("logs");});
  $("#next-window").addEventListener("click", nextWindow);
  for (const [id, key] of Object.entries(FIELDS)) $("#"+id).addEventListener("input", e => {
    state.config[key] = id.endsWith("-time") ? configTime(e.target.value) : e.target.type === "number" ? Number(e.target.value) : e.target.value;
    setDirty();
  });
  $("#config-form").addEventListener("submit", async e => {
    e.preventDefault(); if (locked()) return; state.busy = true;
    try {await saveConfig(); await pollStatus(); toast("设置已保存");} catch (error) {toast(error.message, true);}
    finally {state.busy = false; renderDashboard();}
  });
  $("#course-editor").addEventListener("change", e => {
    if (!e.target.matches("[data-enabled]") || locked()) return;
    const c = state.config.courses[+e.target.closest("[data-index]").dataset.index];
    if (e.target.checked && enabled().length >= 10) {e.target.checked = false; return toast("最多启用 10 个教学班", true);}
    c.enabled = e.target.checked; setDirty();
  });
  $("#course-editor").addEventListener("click", e => {
    const button = e.target.closest("[data-remove], [data-move]"); if (!button || locked()) return;
    const i = +button.closest("[data-index]").dataset.index;
    if (button.hasAttribute("data-remove")) state.config.courses.splice(i, 1);
    else {const j = i + +button.dataset.move; if (j >= 0 && j < state.config.courses.length) [state.config.courses[i], state.config.courses[j]] = [state.config.courses[j], state.config.courses[i]];}
    renderEditor(); setDirty();
  });
  $("#clear-log").addEventListener("click", () => {state.logViewStart = state.cursor; text("#terminal-output", "视图已清空，等待新的输出…");});
  $("#terminal-input-form").addEventListener("submit", async e => {e.preventDefault(); try {await post("/api/input", {value: $("#terminal-input").value}); $("#terminal-input").value = "";} catch (error) {toast(error.message, true);}});
  window.addEventListener("beforeunload", e => {if (state.dirty) {e.preventDefault(); e.returnValue = "";}});
}
async function init() {
  bindEvents(); icons();
  text("#today-label", new Intl.DateTimeFormat("zh-CN", {month: "long", day: "numeric", weekday: "long"}).format(new Date()));
  try {await loadConfig(); await pollStatus();} catch (error) {toast(error.message, true);}
  setInterval(async () => {if (!state.config) {try {await loadConfig();} catch {return;}} await pollStatus();}, 1000);
}
document.addEventListener("DOMContentLoaded", init);
