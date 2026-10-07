"use strict";

const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const state = {
  token: localStorage.getItem("oci_nt_token") || "",
  user: null,
  accounts: [],
  currentPage: "accounts",
  tenantId: null,
  tenantTab: "instances",
  instances: [],
  instanceCache: null,
  instanceSnapshots: new Map(),
  instanceLoadPromises: new Map(),
  bulkJob: null,
  bulkTimer: null,
  cfAccounts: [],
  cfAccountId: null,
  cfZones: [],
  cfRecords: [],
  cfRecordsLoaded: false,
  launchCatalog: null,
  launchResources: null,
  launchJobs: [],
  launchPollTimer: null,
  launchCatalogLoading: false,
  launchCatalogRequestId: 0,
  instanceCacheSource: "本地缓存",
  auditRows: [],
  proxyProfiles: [],
  tasks: [],
  taskDetail: null,
  taskTimer: null,
  systemDiagnostics: null,
  backupPolicy: null,
  backupSchedule: null,
  releaseInfo: null,
  releaseHistory: [],
  authSessions: [],
  loginAttempts: [],
  resourceStatus: null,
  resourceLimits: null,
  monitorSnapshot: null,
  monitorHistory: null,
  monitorSettings: null,
  monitorRange: "1h",
  monitorTimer: null,
  monitorHistoryTimer: null,
};

const pageMeta = {
  dashboard: ["仪表盘", "所有概览均读取本地缓存"],
  accounts: ["API 管理", "账户列表只读取本地缓存"],
  proxies: ["代理管理", "统一维护代理库"],
  tasks: ["任务中心", "查看进度、失败原因并重试失败项目"],
  instances: ["OCI 实例管理", "全部租户实例缓存与按需同步"],
  launch: ["OCI 开机管理", "全部租户开机配置与任务"],
  cloudflare: ["Cloudflare DNS", "按需读取 Cloudflare 数据"],
  audit: ["操作记录", "本地审计日志"],
  settings: ["系统设置", "登录、通知与手动检测策略"],
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtDate(value, withTime = true) {
  if (!value) return "从未";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : {}),
    hour12: false,
  }).format(date);
}

function toast(message, kind = "") {
  const box = $("toast");
  box.textContent = message;
  box.className = `toast ${kind}`.trim();
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 4200);
}

function restoreLocalControl(id, key) {
  const element = $(id);
  const value = localStorage.getItem(key);
  if (element && value !== null) element.value = value;
}

function saveLocalControl(id, key) {
  const element = $(id);
  if (!element) return;
  if (element.value) localStorage.setItem(key, element.value);
  else localStorage.removeItem(key);
}

function clearLocalControls(keys) {
  keys.forEach((key) => localStorage.removeItem(key));
}

function setBusy(button, busy, text = "处理中……") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}


function createIdempotencyKey(scope = "task") {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${scope}:${random}`;
}

function taskErrorCategoryLabel(category) {
  return ({
    RATE_LIMIT: "OCI 限流",
    AUTHENTICATION: "认证失败",
    PERMISSION: "权限不足",
    CAPACITY: "容量或配额不足",
    CONFIGURATION: "资源配置错误",
    NETWORK: "网络或代理异常",
    SERVICE: "OCI 服务异常",
    CONFLICT: "资源状态冲突",
    NOT_FOUND: "资源不存在",
    INTERRUPTED: "服务中断",
    UNKNOWN: "未分类错误",
  })[String(category || "").toUpperCase()] || String(category || "未分类");
}

function utilityDialogField(field) {
  const type = field.type || "text";
  const fullClass = field.full ? " span-2" : "";
  const required = field.required ? " required" : "";
  const min = field.min !== undefined ? ` min="${esc(field.min)}"` : "";
  const max = field.max !== undefined ? ` max="${esc(field.max)}"` : "";
  const step = field.step !== undefined ? ` step="${esc(field.step)}"` : "";
  const placeholder = field.placeholder ? ` placeholder="${esc(field.placeholder)}"` : "";
  const help = field.help ? `<small>${esc(field.help)}</small>` : "";
  if (type === "checkbox") {
    return `<label class="switch-row${fullClass}"><div><strong>${esc(field.label)}</strong>${help}</div><input data-utility-field="${esc(field.name)}" type="checkbox"${field.value ? " checked" : ""}></label>`;
  }
  if (type === "select") {
    const options = (field.options || []).map((item) => {
      const value = typeof item === "object" ? item.value : item;
      const label = typeof item === "object" ? item.label : item;
      return `<option value="${esc(value)}"${String(value) === String(field.value ?? "") ? " selected" : ""}>${esc(label)}</option>`;
    }).join("");
    return `<label class="${fullClass.trim()}"><span>${esc(field.label)}</span><select data-utility-field="${esc(field.name)}"${required}>${options}</select>${help}</label>`;
  }
  if (type === "multiselect") {
    const selected = new Set((field.value || []).map((item) => String(item)));
    const options = (field.options || []).map((item) => {
      const value = typeof item === "object" ? item.value : item;
      const label = typeof item === "object" ? item.label : item;
      return `<option value="${esc(value)}"${selected.has(String(value)) ? " selected" : ""}>${esc(label)}</option>`;
    }).join("");
    return `<label class="${fullClass.trim()}"><span>${esc(field.label)}</span><select class="utility-multiselect" data-utility-field="${esc(field.name)}" multiple size="${esc(field.size || Math.min(8, Math.max(3, (field.options || []).length)))}"${required}>${options}</select>${help}</label>`;
  }
  if (type === "textarea") {
    return `<label class="${fullClass.trim()}"><span>${esc(field.label)}</span><textarea data-utility-field="${esc(field.name)}" rows="${esc(field.rows || 4)}"${placeholder}${required}>${esc(field.value ?? "")}</textarea>${help}</label>`;
  }
  return `<label class="${fullClass.trim()}"><span>${esc(field.label)}</span><input data-utility-field="${esc(field.name)}" type="${esc(type)}" value="${esc(field.value ?? "")}"${placeholder}${required}${min}${max}${step}${field.disabled ? " disabled" : ""}>${help}</label>`;
}

window.openUtilityDialog = function openUtilityDialog(options = {}) {
  const dialog = $("utility-dialog");
  const form = $("utility-dialog-form");
  const title = $("utility-dialog-title");
  const copy = $("utility-dialog-copy");
  const body = $("utility-dialog-body");
  const errorBox = $("utility-dialog-error");
  const cancelButton = $("utility-dialog-cancel");
  const submitButton = $("utility-dialog-submit");
  const closeButton = $("utility-dialog-close");
  const fields = options.fields || [];
  title.textContent = options.title || "操作";
  copy.textContent = options.copy || "";
  copy.hidden = !options.copy;
  body.innerHTML = `${options.message ? `<div class="utility-message${options.preformatted ? " preformatted" : ""}">${options.preformatted ? esc(options.message) : esc(options.message)}</div>` : ""}<div class="utility-fields${fields.some((field) => field.full) ? " two-col" : ""}">${fields.map(utilityDialogField).join("")}</div>`;
  errorBox.hidden = true;
  errorBox.textContent = "";
  cancelButton.textContent = options.cancelText || "取消";
  cancelButton.hidden = Boolean(options.messageOnly);
  submitButton.textContent = options.submitText || (options.messageOnly ? "关闭" : "确认");
  submitButton.className = `button ${options.danger ? "danger solid-danger" : "primary"}`;
  if (dialog.open) dialog.close();
  dialog.showModal();
  const first = body.querySelector("input, textarea, select");
  queueMicrotask(() => first?.focus());

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      form.onsubmit = null;
      cancelButton.onclick = null;
      closeButton.onclick = null;
      dialog.oncancel = null;
      if (dialog.open) dialog.close();
      resolve(value);
    };
    cancelButton.onclick = () => finish(null);
    closeButton.onclick = () => finish(null);
    dialog.oncancel = (event) => { event.preventDefault(); finish(null); };
    form.onsubmit = (event) => {
      event.preventDefault();
      if (options.messageOnly) return finish({});
      if (!form.reportValidity()) return;
      const values = {};
      for (const field of fields) {
        const element = body.querySelector(`[data-utility-field="${CSS.escape(field.name)}"]`);
        if (!element) continue;
        if (field.type === "checkbox") values[field.name] = element.checked;
        else if (field.type === "number") values[field.name] = element.value === "" ? null : Number(element.value);
        else if (field.type === "multiselect") values[field.name] = Array.from(element.selectedOptions).map((option) => option.value);
        else values[field.name] = element.value;
      }
      if (typeof options.validate === "function") {
        const message = options.validate(values);
        if (message) {
          errorBox.textContent = message;
          errorBox.hidden = false;
          return;
        }
      }
      finish(values);
    };
  });
};

window.openConfirmDialog = async function openConfirmDialog(options = {}) {
  const fields = options.confirmationText ? [{
    name: "confirmation",
    label: `请输入 ${options.confirmationText} 确认`,
    placeholder: options.confirmationText,
    required: true,
    full: true,
  }] : [];
  const result = await window.openUtilityDialog({
    title: options.title || "确认操作",
    copy: options.copy || "",
    message: options.message || "",
    fields,
    submitText: options.submitText || "确认",
    danger: Boolean(options.danger),
    validate: options.confirmationText
      ? (values) => values.confirmation === options.confirmationText ? "" : `请输入 ${options.confirmationText} 后继续`
      : null,
  });
  return result !== null;
};

window.openMessageDialog = function openMessageDialog(options = {}) {
  return window.openUtilityDialog({
    title: options.title || "提示",
    copy: options.copy || "",
    message: options.message || "",
    preformatted: Boolean(options.preformatted),
    messageOnly: true,
    submitText: options.submitText || "关闭",
  });
};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(`${API}${path}`, { ...options, headers, body });
  if (response.status === 401 && !path.startsWith("/auth/")) {
    logout(false);
    throw new Error("登录状态已失效，请重新登录");
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(typeof detail === "string" ? detail : "请求失败");
  }
  return payload;
}

function saveToken(token) {
  state.token = token;
  localStorage.setItem("oci_nt_token", token);
}

function logout(showMessage = true) {
  stopLaunchPolling();
  stopDashboardMonitor();
  const token = state.token;
  if (token) {
    fetch(`${API}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      keepalive: true,
    }).catch(() => {});
  }
  state.token = "";
  state.user = null;
  state.tenantId = null;
  localStorage.removeItem("oci_nt_token");
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  if (showMessage) toast("已退出登录");
}

async function exchangeGoogleCode() {
  const url = new URL(location.href);
  const code = url.searchParams.get("google_code");
  const googleError = url.searchParams.get("google_error");
  if (!code && !googleError) return false;
  url.searchParams.delete("google_code");
  url.searchParams.delete("google_error");
  history.replaceState({}, "", url.pathname + url.search + url.hash);
  if (googleError) {
    $("login-error").textContent = googleError;
    $("login-error").hidden = false;
    return false;
  }
  try {
    const result = await api("/auth/google/exchange", { method: "POST", body: { code } });
    saveToken(result.access_token);
    return true;
  } catch (error) {
    $("login-error").textContent = error.message;
    $("login-error").hidden = false;
    return false;
  }
}

async function loadGoogleLoginStatus() {
  try {
    const result = await api("/auth/google/status");
    $("google-login").hidden = !result.enabled;
  } catch {
    $("google-login").hidden = true;
  }
}

function syncActionButton(inputId, buttonId, enableLabel, disableLabel) {
  const input = $(inputId);
  const button = $(buttonId);
  if (!input || !button) return;
  const enabled = Boolean(input.checked);
  button.textContent = enabled ? disableLabel : enableLabel;
  button.classList.toggle("active", enabled);
  button.setAttribute("aria-pressed", enabled ? "true" : "false");
}

function syncTelegramAction() {
  syncActionButton("telegram-enabled", "telegram-toggle", "启用 Telegram", "停用 Telegram");
}

function syncGoogleAuthAction() {
  syncActionButton("google-auth-enabled", "google-auth-toggle", "启用 Gmail 登录", "停用 Gmail 登录");
  const status = $("google-status");
  if (status) status.textContent = "本地用户名密码登录继续保留。";
}

function syncMonitorActionButtons(pending = false) {
  syncActionButton("monitor-enabled", "monitor-enabled-toggle", "启用历史指标", "停用历史指标");
  const enabled = Boolean($("monitor-enabled")?.checked);
  const badge = $("monitor-enabled-badge");
  if (badge) {
    badge.className = `status-pill ${pending ? "warn" : (enabled ? "good" : "muted")}`;
    badge.textContent = pending ? (enabled ? "待保存启用" : "待保存停用") : (enabled ? "已启用" : "已停用");
  }
  const allContainers = Boolean($("monitor-all-containers")?.checked);
  const allButton = $("monitor-all-containers-toggle");
  if (allButton) {
    allButton.textContent = allContainers ? "仅显示 OCI-N&T" : "显示全部容器";
    allButton.classList.toggle("active", allContainers);
    allButton.setAttribute("aria-pressed", allContainers ? "true" : "false");
  }
}

function renderGoogleAuthSettings(google) {
  $("google-auth-enabled").checked = Boolean(google.enabled);
  $("google-client-id").value = google.client_id || "";
  $("google-client-secret").value = "";
  $("google-client-secret").placeholder = google.has_client_secret ? "已保存；留空表示不修改" : "请输入 Client Secret";
  $("google-allowed-emails").value = (google.allowed_emails || []).join("\n");
  $("google-redirect-uri").value = google.redirect_uri || "";
  $("google-frontend-return-url").value = google.frontend_return_url || "";
  syncGoogleAuthAction();
}

async function saveGoogleAuth(event) {
  event.preventDefault();
  const button = $("google-auth-save");
  setBusy(button, true, "保存中……");
  try {
    const result = await api("/settings/google-auth", { method: "PUT", body: {
      enabled: $("google-auth-enabled").checked,
      client_id: $("google-client-id").value.trim(),
      client_secret: $("google-client-secret").value || null,
      redirect_uri: $("google-redirect-uri").value.trim(),
      frontend_return_url: $("google-frontend-return-url").value.trim() || "/",
      allowed_emails: $("google-allowed-emails").value.split(/[\n,;]+/).map((v) => v.trim()).filter(Boolean),
    }});
    renderGoogleAuthSettings(result);
    await loadGoogleLoginStatus();
    toast("Gmail 登录设置已保存", "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function testGoogleAuth() {
  const button = $("google-auth-test");
  setBusy(button, true, "测试中……");
  try {
    const result = await api("/settings/google-auth/test", { method: "POST" });
    toast(result.message, "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function login(event) {
  event.preventDefault();
  const form = new URLSearchParams();
  form.set("username", $("login-username").value);
  form.set("password", $("login-password").value);
  const button = event.submitter;
  setBusy(button, true, "登录中……");
  $("login-error").hidden = true;
  try {
    const response = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "登录失败");
    saveToken(result.access_token);
    await startApp();
  } catch (error) {
    $("login-error").textContent = error.message;
    $("login-error").hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function startApp() {
  if (!state.token) return;
  try {
    state.user = await api("/auth/me");
  } catch {
    logout(false);
    return;
  }
  $("current-username").textContent = state.user.username;
  $("credential-username").placeholder = `当前：${state.user.username}；留空不修改`;
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  showPage("dashboard");
  await Promise.all([loadAccounts(), loadBulkCurrent()]);
}

function showPage(page) {
  stopLaunchPolling();
  if (page !== "tasks") stopTaskPolling();
  if (page !== "dashboard") stopDashboardMonitor();
  document.body.classList.remove("tenant-mode");
  state.currentPage = page;
  state.tenantId = null;
  $("tenant-page").hidden = true;
  document.querySelectorAll(".page").forEach((section) => { section.hidden = true; });
  $(`${page}-page`).hidden = false;
  document.querySelectorAll(".nav").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  const meta = pageMeta[page];
  $("page-title").textContent = meta[0];
  $("page-copy").textContent = meta[1];
  if (page === "proxies") prepareProxyManagementPage();
  if (page === "tasks") loadTasks();
  if (page === "cloudflare") loadCloudflare();
  if (page === "dashboard") {
    loadSystemDiagnostics();
    startDashboardMonitor();
  }
  if (page === "audit") loadAudit();
  if (page === "settings") loadSettings();
  if (typeof window.ui2PageOpened === "function") window.ui2PageOpened(page);
}

function accountStatus(account) {
  const score = Number(account?.health_score);
  const suffix = Number.isFinite(score) ? ` ${Math.round(score)}` : "";
  if (account?.health_level === "HEALTHY") return [`健康${suffix}`, "good"];
  if (account?.health_level === "WARNING") return [`警告${suffix}`, "warn"];
  if (account?.health_level === "CRITICAL") return [`异常${suffix}`, "bad"];
  const status = account.account_status || "UNKNOWN";
  if (status === "ALIVE") return ["有效", "good"];
  if (status === "UNKNOWN") return ["待检测", "muted"];
  return ["异常", "bad"];
}

function accountType(account) {
  if (account.account_type === "PERSONAL_FREE") return "个人免费";
  if (account.account_type === "PERSONAL_UPGRADED") return "个人升级";
  return "类型未知";
}

function regionName(account) {
  return account.home_region_name || account.home_region_key || account.region || "未读取";
}

const IMPORT_REGION_NAMES = Object.freeze({
  "af-johannesburg-1": "南非约翰内斯堡",
  "ap-batam-1": "印度尼西亚巴淡岛",
  "ap-chuncheon-1": "韩国春川",
  "ap-delhi-1": "印度德里",
  "ap-hyderabad-1": "印度海得拉巴",
  "ap-kulai-1": "马来西亚古来",
  "ap-kulai-2": "马来西亚古来 2",
  "ap-melbourne-1": "澳大利亚墨尔本",
  "ap-mumbai-1": "印度孟买",
  "ap-osaka-1": "日本大阪",
  "ap-seoul-1": "韩国首尔",
  "ap-singapore-1": "新加坡",
  "ap-singapore-2": "新加坡西部",
  "ap-sydney-1": "澳大利亚悉尼",
  "ap-tokyo-1": "日本东京",
  "ca-montreal-1": "加拿大蒙特利尔",
  "ca-toronto-1": "加拿大多伦多",
  "eu-amsterdam-1": "荷兰阿姆斯特丹",
  "eu-frankfurt-1": "德国法兰克福",
  "eu-madrid-1": "西班牙马德里",
  "eu-marseille-1": "法国马赛",
  "eu-milan-1": "意大利米兰",
  "eu-paris-1": "法国巴黎",
  "eu-stockholm-1": "瑞典斯德哥尔摩",
  "eu-turin-1": "意大利都灵",
  "eu-zurich-1": "瑞士苏黎世",
  "il-jerusalem-1": "以色列耶路撒冷",
  "me-abudhabi-1": "阿联酋阿布扎比",
  "me-dubai-1": "阿联酋迪拜",
  "me-jeddah-1": "沙特阿拉伯吉达",
  "mx-monterrey-1": "墨西哥蒙特雷",
  "mx-queretaro-1": "墨西哥克雷塔罗",
  "sa-bogota-1": "哥伦比亚波哥大",
  "sa-santiago-1": "智利圣地亚哥",
  "sa-saopaulo-1": "巴西圣保罗",
  "sa-vinhedo-1": "巴西维涅杜",
  "uk-cardiff-1": "英国卡迪夫",
  "uk-london-1": "英国伦敦",
  "uk-newport-1": "英国纽波特",
  "us-ashburn-1": "美国阿什本",
  "us-chicago-1": "美国芝加哥",
  "us-phoenix-1": "美国凤凰城",
  "us-sanjose-1": "美国圣何塞",
});

function extractOciConfigRegion(text) {
  const match = String(text || "").match(/^\s*region\s*=\s*([^#;\r\n]+?)\s*$/im);
  return String(match?.[1] || "").trim().toLowerCase();
}


const OCI_CONFIG_FILE_LIMIT = 262144;
const OCI_PRIVATE_KEY_FILE_LIMIT = 131072;
let importedOciConfigState = null;
let importedPrivateKeyFile = null;
let applyingImportedOciConfig = false;

function humanFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function parseOciConfigProfiles(rawText) {
  const normalized = String(rawText || "").replace(/^\ufeff/, "").replace(/\r\n?/g, "\n");
  const privateKeyMatch = normalized.match(/-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END \1-----/);
  const privateKeyPem = privateKeyMatch ? `${privateKeyMatch[0].trim()}\n` : "";
  const configText = privateKeyMatch
    ? `${normalized.slice(0, privateKeyMatch.index)}\n${normalized.slice((privateKeyMatch.index || 0) + privateKeyMatch[0].length)}`
    : normalized;
  const profiles = [];
  const preamble = [];
  let current = null;
  const ensureProfile = (name = "DEFAULT") => {
    let profile = profiles.find((item) => item.name === name);
    if (!profile) {
      profile = { name, lines: [], values: {} };
      profiles.push(profile);
    }
    return profile;
  };
  for (const rawLine of configText.split("\n")) {
    const section = rawLine.trim().match(/^\[([^\]]+)\]$/);
    if (section) {
      current = ensureProfile(section[1].trim() || "DEFAULT");
      if (preamble.length && current.lines.length === 0) current.lines.push(...preamble.splice(0));
      continue;
    }
    const line = rawLine.trim();
    if (!current && (!line || line.startsWith("#") || line.startsWith(";"))) {
      preamble.push(rawLine);
      continue;
    }
    if (!current) {
      current = ensureProfile("DEFAULT");
      if (preamble.length) current.lines.push(...preamble.splice(0));
    }
    current.lines.push(rawLine);
    if (!line || line.startsWith("#") || line.startsWith(";") || !line.includes("=")) continue;
    const splitAt = line.indexOf("=");
    const key = line.slice(0, splitAt).trim().toLowerCase();
    let value = line.slice(splitAt + 1).trim();
    for (const marker of [" #", " ;"]) {
      if (value.includes(marker)) value = value.split(marker, 1)[0].trim();
    }
    current.values[key] = value.replace(/^['"]|['"]$/g, "");
  }
  return {
    profiles: profiles.filter((profile) => Object.keys(profile.values).length || profile.lines.some((line) => line.trim())),
    privateKeyPem,
  };
}

function ociConfigProfileText(profile, privateKeyPem = "") {
  if (!profile) return "";
  const body = profile.lines.join("\n").trim();
  const section = `[${profile.name || "DEFAULT"}]`;
  return `${section}\n${body}${privateKeyPem ? `\n\n${privateKeyPem.trim()}\n` : ""}`.trim() + "\n";
}

function ociConfigRequiredFields(profile) {
  const required = ["tenancy", "user", "fingerprint", "region"];
  return {
    present: required.filter((key) => String(profile?.values?.[key] || "").trim()),
    missing: required.filter((key) => !String(profile?.values?.[key] || "").trim()),
  };
}

function setOciConfigFileStatus(message, tone = "muted") {
  const status = $("api-config-file-status");
  if (!status) return;
  status.textContent = message;
  status.className = `oci-config-file-status ${tone}`;
}

function resetOciConfigFileImport({ preserveTextarea = false } = {}) {
  importedOciConfigState = null;
  importedPrivateKeyFile = null;
  const input = $("api-config-file");
  const profile = $("api-config-profile");
  const row = $("api-config-profile-row");
  const clear = $("api-config-file-clear");
  const privateCopy = $("api-private-key-copy");
  if (input) input.value = "";
  if (profile) profile.innerHTML = "";
  if (row) row.hidden = true;
  if (clear) clear.hidden = true;
  if (privateCopy) privateCopy.textContent = "如果 Config 中只有 key_file 路径，请在这里选择对应 PEM 文件。";
  setOciConfigFileStatus("未选择文件，也可以在下方直接粘贴 Config。", "muted");
  if (!preserveTextarea && $("api-config")) {
    applyingImportedOciConfig = true;
    $("api-config").value = "";
    $("api-config").dispatchEvent(new Event("input", { bubbles: true }));
    applyingImportedOciConfig = false;
  }
}

function applyImportedOciConfigProfile(profileName) {
  const stateValue = importedOciConfigState;
  if (!stateValue) return;
  const profile = stateValue.parsed.profiles.find((item) => item.name === profileName)
    || stateValue.parsed.profiles[0];
  if (!profile) return;
  const textarea = $("api-config");
  applyingImportedOciConfig = true;
  textarea.value = ociConfigProfileText(profile, stateValue.parsed.privateKeyPem);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  applyingImportedOciConfig = false;
  const fields = ociConfigRequiredFields(profile);
  const keyFile = String(profile.values.key_file || "").trim();
  const profileLabel = stateValue.parsed.profiles.length > 1 ? ` · 档案 ${profile.name}` : "";
  const keyLabel = importedPrivateKeyFile
    ? ` · 已识别私钥 ${importedPrivateKeyFile.name}`
    : stateValue.parsed.privateKeyPem
      ? " · 文件内含私钥"
      : keyFile
        ? ` · 仍需选择私钥 ${keyFile.split(/[\\/]/).pop()}`
        : " · 仍需选择 PEM 私钥";
  if (fields.missing.length) {
    setOciConfigFileStatus(`已读取 ${stateValue.configFile.name}${profileLabel}，缺少：${fields.missing.join("、")}${keyLabel}`, "bad-text");
  } else {
    setOciConfigFileStatus(`已读取 ${stateValue.configFile.name}（${humanFileSize(stateValue.configFile.size)}）${profileLabel}${keyLabel}`, "good-text");
  }
  const privateCopy = $("api-private-key-copy");
  if (privateCopy) {
    if (importedPrivateKeyFile) privateCopy.textContent = `已从同一次选择中识别私钥：${importedPrivateKeyFile.name}`;
    else if (stateValue.parsed.privateKeyPem) privateCopy.textContent = "Config 文件中已经包含 PEM 私钥，无需重复选择。";
    else if (keyFile) privateCopy.textContent = `Config 引用了 ${keyFile}；浏览器无法读取本地路径，请选择对应 PEM 文件。`;
    else privateCopy.textContent = "Config 未包含私钥，请选择对应 PEM 文件。";
  }
}

async function importOciConfigFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (files.length > 2) throw new Error("一次最多选择一个 Config 和一个 PEM 私钥文件");
  let configCandidate = null;
  let privateKeyCandidate = null;
  for (const file of files) {
    const limit = /\.pem$|\.key$/i.test(file.name) ? OCI_PRIVATE_KEY_FILE_LIMIT : OCI_CONFIG_FILE_LIMIT;
    if (file.size > limit) throw new Error(`${file.name} 过大，最大允许 ${humanFileSize(limit)}`);
    const text = await file.text();
    if (text.includes("\0")) throw new Error(`${file.name} 不是可识别的文本文件`);
    if (/-----BEGIN ([A-Z ]*PRIVATE KEY)-----/.test(text)) {
      if (privateKeyCandidate) throw new Error("一次只能导入一个 PEM 私钥文件");
      privateKeyCandidate = { file, text };
      if (/^\s*(?:\[[^\]]+\]\s*)?(?:[\s\S]*?\n)?\s*(?:tenancy|user|fingerprint|region)\s*=/im.test(text)) {
        configCandidate = configCandidate || { file, text };
      }
      continue;
    }
    const parsed = parseOciConfigProfiles(text);
    const looksLikeConfig = parsed.profiles.some((profile) => {
      const values = profile.values || {};
      return ["tenancy", "user", "fingerprint", "region"].filter((key) => values[key]).length >= 2;
    });
    if (looksLikeConfig) {
      if (configCandidate) throw new Error("一次只能导入一个 OCI Config 文件");
      configCandidate = { file, text };
    } else if (/\.pem$|\.key$/i.test(file.name)) {
      privateKeyCandidate = { file, text };
    } else {
      throw new Error(`${file.name} 中未识别到 OCI Config 或 PEM 私钥`);
    }
  }
  if (!configCandidate) throw new Error("未找到 OCI Config 文件");
  const parsed = parseOciConfigProfiles(configCandidate.text);
  if (!parsed.profiles.length) throw new Error("Config 文件中没有可识别的配置档案");
  importedPrivateKeyFile = privateKeyCandidate?.file && privateKeyCandidate.file !== configCandidate.file
    ? privateKeyCandidate.file
    : null;
  importedOciConfigState = { configFile: configCandidate.file, parsed };
  const select = $("api-config-profile");
  const row = $("api-config-profile-row");
  select.innerHTML = parsed.profiles.map((profile) => `<option value="${esc(profile.name)}">${esc(profile.name)}</option>`).join("");
  const preferred = parsed.profiles.find((profile) => profile.name.toUpperCase() === "DEFAULT") || parsed.profiles[0];
  select.value = preferred.name;
  row.hidden = parsed.profiles.length <= 1;
  $("api-config-file-clear").hidden = false;
  applyImportedOciConfigProfile(preferred.name);
}

async function handleOciConfigFileSelection(event) {
  try {
    await importOciConfigFiles(event.currentTarget.files);
  } catch (error) {
    resetOciConfigFileImport({ preserveTextarea: true });
    setOciConfigFileStatus(error.message, "bad-text");
    const errorBox = $("account-error");
    if (errorBox) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    }
  }
}

function updateImportRegionDetection() {
  const configText = String($("api-config")?.value || "");
  const region = extractOciConfigRegion(configText);
  const box = $("import-region-detected");
  const copy = $("import-region-copy");
  if (!box || !copy) return;
  if (!configText.trim()) {
    box.textContent = "等待输入 OCI Config";
    box.className = "";
    copy.textContent = "粘贴配置后自动识别 region，并在导入时再次由后端校验。";
    return;
  }
  if (!region) {
    box.textContent = "未找到 region 配置";
    box.className = "warn-text";
    copy.textContent = "请确认 Config 中包含 region=区域代码，例如 region=us-phoenix-1。";
    return;
  }
  const name = IMPORT_REGION_NAMES[region];
  if (name) {
    box.textContent = `${name} · ${region}`;
    box.className = "good-text";
    copy.textContent = "区域已识别；导入成功后会以 OCI 返回的主区域为准。";
  } else if (/^[a-z]{2,4}-[a-z0-9-]+-\d+$/.test(region)) {
    box.textContent = `有效 OCI 区域代码 · ${region}`;
    box.className = "good-text";
    copy.textContent = "该区域暂未收录中文名称，但不会影响验证和导入。";
  } else {
    box.textContent = `区域格式可能不正确 · ${region}`;
    box.className = "bad-text";
    copy.textContent = "OCI 区域通常类似 us-phoenix-1、uk-london-1。";
  }
}

function survival(account) {
  return Number.isFinite(account.survival_days) ? `${account.survival_days} 天` : "—";
}

async function loadAccounts() {
  state.accounts = await api("/accounts");
  /* BEGIN OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 account health merge */
  try {
    const health = await api("/account-health");
    const healthById = new Map((health.accounts || []).map((item) => [Number(item.id), item]));
    state.accounts = state.accounts.map((account) => ({
      ...account,
      ...(healthById.get(Number(account.id)) || {}),
    }));
    state.accountHealth = health;
  } catch (_) {
    state.accountHealth = null;
  }
  /* END OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 account health merge */
  renderAccounts();
  renderProxyManagement();
  if (typeof window.ui2AccountsChanged === "function") window.ui2AccountsChanged();
  if (state.tenantId) renderTenantHeader();
}

function renderAccounts() {
  if (typeof window.ui2RenderAccounts === "function" && window.ui2RenderAccounts()) return;
  const query = $("account-search").value.trim().toLowerCase();
  const accounts = state.accounts.filter((account) => {
    const content = [account.custom_name, account.email, account.tenancy_name,
      account.home_region_name, account.home_region_key, account.region]
      .filter(Boolean).join(" ").toLowerCase();
    return !query || content.includes(query);
  });
  if (!accounts.length) {
    $("account-list").innerHTML = `<div class="empty">${state.accounts.length ? "没有匹配账户" : "暂无 OCI 账户"}</div>`;
    return;
  }
  $("account-list").innerHTML = accounts.map((account) => {
    const [statusText, statusClass] = accountStatus(account);
    return `<article class="account-card" data-account-card="${account.id}">
      <div class="account-main">
        <div class="account-avatar">${esc((account.custom_name || "N")[0].toUpperCase())}</div>
        <div><strong title="${esc(account.custom_name)}">${esc(account.custom_name)}</strong><small title="${esc(account.email || "")}">${esc(account.email || "未读取邮箱")}</small></div>
      </div>
      <div class="account-cell"><span>${esc(accountType(account))}</span><small>${esc(account.tenancy_name || "未读取租户")}</small></div>
      <div class="account-cell"><span>${esc(regionName(account))}</span><small>${esc(account.home_region_key || account.region || "—")} · ${survival(account)}</small></div>
      <div class="account-actions"><span class="badge ${statusClass}" title="${esc(account.last_error || "")}">${statusText}</span><button class="button primary" data-manage-account="${account.id}" type="button">管理</button><button class="button" data-check-account="${account.id}" type="button">检测</button></div>
    </article>`;
  }).join("");
}

async function checkAccount(id, button) {
  setBusy(button, true, "检测中……");
  try {
    await api(`/accounts/${id}/check`, { method: "POST" });
    await loadAccounts();
    toast("账户检测完成", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

function manualProxyConnectionIds(prefix) {
  return [`${prefix}-scheme`, `${prefix}-host`, `${prefix}-port`, `${prefix}-username`, `${prefix}-password`];
}

function clearManualProxyFields(prefix) {
  for (const id of manualProxyConnectionIds(prefix)) {
    const field = $(id);
    if (!field) continue;
    field.value = id.endsWith("-scheme") ? "socks5" : "";
  }
}

function setManualProxyDisabled(prefix, disabled, includeLabel = false) {
  const ids = manualProxyConnectionIds(prefix);
  if (includeLabel) ids.push(`${prefix}-label`);
  for (const id of ids) {
    const field = $(id);
    if (field) field.disabled = disabled;
  }
}

function manualProxyHasConnectionInput(prefix) {
  return [`${prefix}-host`, `${prefix}-port`, `${prefix}-username`, `${prefix}-password`]
    .some((id) => String($(id)?.value || "").trim());
}

function buildManualProxyUrl(prefix, allowEmpty = false) {
  const scheme = String($(`${prefix}-scheme`)?.value || "socks5").trim().toLowerCase();
  const hostRaw = String($(`${prefix}-host`)?.value || "").trim();
  const portRaw = String($(`${prefix}-port`)?.value || "").trim();
  const username = String($(`${prefix}-username`)?.value || "").trim();
  const password = String($(`${prefix}-password`)?.value || "");
  const hasAny = Boolean(hostRaw || portRaw || username || password);
  if (!hasAny && allowEmpty) return "";
  if (!hostRaw) throw new Error("请填写代理服务器地址");
  if (!portRaw) throw new Error("请填写代理端口");
  const port = Number(portRaw);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("代理端口必须在 1–65535 之间");
  if (password && !username) throw new Error("填写代理密码时必须同时填写用户名");
  const cleanHost = hostRaw.replace(/^\[|\]$/g, "");
  const host = cleanHost.includes(":") ? `[${cleanHost}]` : cleanHost;
  const auth = username
    ? `${encodeURIComponent(username)}${password ? `:${encodeURIComponent(password)}` : ""}@`
    : "";
  return `${scheme}://${auth}${host}:${port}`;
}

function importProxyMode() {
  return $("import-proxy-mode-new")?.checked ? "new" : "existing";
}

function populateImportProxyProfileSelect(preferredId = null) {
  const select = $("import-proxy-profile-select");
  if (!select) return;
  const previous = preferredId ?? select.value;
  const choices = state.proxyProfiles.filter((profile) => profile.is_enabled !== false && !Number(profile.assigned_count || 0));
  select.innerHTML = choices.length
    ? choices.map((profile) => `<option value="${profile.id}">${esc(profile.name)} · ${esc(String(profile.scheme || "").toUpperCase())} · ${esc(profile.host)}:${profile.port}</option>`).join("")
    : '<option value="">暂无未分配代理，请切换到“新增代理”</option>';
  if (choices.some((profile) => String(profile.id) === String(previous))) {
    select.value = String(previous);
  } else {
    select.value = String(choices[0]?.id || "");
  }
}

function setImportProxyState() {
  const enabled = $("import-proxy-enabled").checked;
  const mode = importProxyMode();
  const hasProfiles = state.proxyProfiles.some((profile) => profile.is_enabled !== false && !Number(profile.assigned_count || 0));
  $("import-proxy-options").classList.toggle("is-disabled", !enabled);
  $("import-proxy-existing-section").hidden = mode !== "existing";
  $("import-proxy-new-section").hidden = mode !== "new";
  $("import-proxy-mode-existing").disabled = !enabled || !hasProfiles;
  $("import-proxy-mode-new").disabled = !enabled;
  $("import-proxy-profile-select").disabled = !enabled || mode !== "existing" || !hasProfiles;
  $("import-proxy-profile-id").disabled = !enabled || mode !== "existing";
  setManualProxyDisabled("import-proxy", !enabled || mode !== "new", true);
  $("import-proxy-test").disabled = !enabled || (mode === "existing" ? !$("import-proxy-profile-select").value : false);
  $("import-proxy-test").textContent = mode === "existing" ? "测试所选代理" : "测试新增代理";
  if (!enabled) {
    $("import-proxy-profile-id").value = "";
    $("import-proxy-url").value = "";
    $("import-proxy-result").textContent = "未启用，导入验证将直连 OCI";
    $("import-proxy-result").className = "muted";
  } else if (mode === "existing") {
    $("import-proxy-result").textContent = hasProfiles ? "将使用代理库中的所选代理" : "代理库为空，请切换到新增代理";
    $("import-proxy-result").className = hasProfiles ? "muted" : "warn-text";
  } else {
    $("import-proxy-result").textContent = "填写新代理后可先测试，再验证 OCI";
    $("import-proxy-result").className = "muted";
  }
}

async function openAccountDialog() {
  $("account-form").reset();
  resetOciConfigFileImport({ preserveTextarea: true });
  clearManualProxyFields("import-proxy");
  $("import-proxy-profile-id").value = "";
  $("import-proxy-url").value = "";
  $("account-error").hidden = true;
  $("import-proxy-result").textContent = "未启用，导入验证将直连 OCI";
  $("import-proxy-result").className = "muted";
  $("import-proxy-profile-copy").textContent = "代理账号和密码不会在页面回显。";
  updateImportRegionDetection();
  $("import-proxy-mode-existing").checked = true;
  setImportProxyState();
  $("account-dialog").showModal();
  try {
    await loadProxyProfiles();
  } catch (error) {
    state.proxyProfiles = [];
    populateImportProxyProfileSelect();
    $("import-proxy-profile-copy").textContent = `代理库读取失败：${error.message}`;
  }
  if (!state.proxyProfiles.length) $("import-proxy-mode-new").checked = true;
  else $("import-proxy-mode-existing").checked = true;
  setImportProxyState();
}

async function testUnsavedProxy(url, button, resultBox) {
  const proxyUrl = String(url || "").trim();
  resultBox.hidden = false;
  if (!proxyUrl) {
    resultBox.textContent = "请先填写代理信息";
    resultBox.className = "bad-text";
    return null;
  }
  setBusy(button, true, "测试中……");
  try {
    const result = await api("/proxy/test", { method: "POST", body: { proxy_url: proxyUrl } });
    resultBox.textContent = result.message;
    resultBox.className = "good-text";
    return result;
  } catch (error) {
    resultBox.textContent = error.message;
    resultBox.className = "bad-text";
    return null;
  } finally {
    setBusy(button, false);
  }
}

async function testImportProxy() {
  const resultBox = $("import-proxy-result");
  resultBox.hidden = false;
  if (!$("import-proxy-enabled").checked) {
    resultBox.textContent = "请先启用独立代理";
    resultBox.className = "bad-text";
    return;
  }
  try {
    if (importProxyMode() === "existing") {
      const profileId = Number($("import-proxy-profile-select").value);
      if (!profileId) throw new Error("请选择已保存代理");
      setBusy($("import-proxy-test"), true, "测试中……");
      try {
        const result = await api(`/proxies/${profileId}/test`, { method: "POST" });
        resultBox.textContent = result.message;
        resultBox.className = "good-text";
        await loadProxyProfiles().catch(() => {});
      } finally {
        setBusy($("import-proxy-test"), false);
      }
      return;
    }
    const proxyUrl = buildManualProxyUrl("import-proxy");
    $("import-proxy-url").value = proxyUrl;
    await testUnsavedProxy(proxyUrl, $("import-proxy-test"), resultBox);
  } catch (error) {
    resultBox.textContent = error.message;
    resultBox.className = "bad-text";
  }
}

function accountImportFormData(form) {
  const formData = new FormData(form);
  const manuallySelectedKey = formData.get("private_key_file");
  if (importedPrivateKeyFile && !(manuallySelectedKey instanceof File && manuallySelectedKey.name)) {
    formData.set("private_key_file", importedPrivateKeyFile, importedPrivateKeyFile.name);
  }
  return formData;
}

function accountImportPreviewMessage(preview) {
  const action = preview.action === "UPDATE"
    ? `更新已有账户${preview.existing_account_name ? `：${preview.existing_account_name}` : ""}`
    : "新增账户";
  const proxy = preview.proxy_enabled
    ? `${preview.proxy_available ? "可用" : "不可用"}${preview.proxy_exit_ip ? ` · 出口 ${preview.proxy_exit_ip}` : ""}`
    : "未启用（直连 OCI）";
  const fingerprint = preview.fingerprint_matches
    ? `匹配 · ${preview.fingerprint}`
    : `不匹配\nConfig：${preview.fingerprint}\n私钥：${preview.computed_fingerprint || "无法计算"}`;
  return [
    `写入方式：${action}`,
    `租户：${preview.tenancy_name || "未读取"}`,
    `租户 OCID：${preview.tenancy_ocid}`,
    `用户邮箱：${preview.email || "未读取"}`,
    `用户 OCID：${preview.user_ocid}`,
    `区域：${preview.home_region_name || preview.region} (${preview.region})`,
    `账户类型：${accountType({ account_type: preview.account_type })}`,
    `私钥指纹：${fingerprint}`,
    `独立代理：${proxy}`,
    `验证结果：${preview.validation_ok ? "通过" : preview.validation_error || "未通过"}`,
  ].join("\n");
}

async function confirmAccountImportPreview(preview) {
  const result = await window.openUtilityDialog({
    title: preview.action === "UPDATE" ? "确认更新 OCI API" : "确认导入 OCI API",
    copy: "预检不会写入数据库；确认后才保存账户和代理绑定。",
    message: accountImportPreviewMessage(preview),
    preformatted: true,
    submitText: preview.action === "UPDATE" ? "确认更新" : "确认导入",
    danger: false,
  });
  return result !== null;
}

async function importAccount(event) {
  event.preventDefault();
  const ntAccountImportForm = $("account-form");
  const button = $("account-submit");
  const errorBox = $("account-error");
  errorBox.hidden = true;
  try {
    const enabled = $("import-proxy-enabled").checked;
    $("import-proxy-profile-id").value = "";
    $("import-proxy-profile-id").disabled = true;
    $("import-proxy-url").value = "";
    if (enabled && importProxyMode() === "existing") {
      const profileId = Number($("import-proxy-profile-select").value);
      if (!profileId) throw new Error("请选择已保存代理，或切换为新增代理");
      $("import-proxy-profile-id").value = String(profileId);
      $("import-proxy-profile-id").disabled = false;
    } else if (enabled) {
      $("import-proxy-url").value = buildManualProxyUrl("import-proxy");
    }
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    return;
  }

  setBusy(button, true, "正在执行导入预检……");
  try {
    const preview = await api("/accounts/import-preview", {
      method: "POST",
      body: accountImportFormData(ntAccountImportForm),
    });
    if (!preview.validation_ok) {
      await window.openMessageDialog({
        title: "OCI 导入预检未通过",
        copy: "数据库没有写入任何账户信息。",
        message: accountImportPreviewMessage(preview),
        preformatted: true,
      });
      throw new Error(preview.validation_error || "OCI 导入预检未通过");
    }
    const confirmed = await confirmAccountImportPreview(preview);
    if (!confirmed) return;

    setBusy(button, true, preview.action === "UPDATE" ? "正在更新 OCI API……" : "正在导入 OCI API……");
    const formData = accountImportFormData(ntAccountImportForm);
    formData.set("update_existing", preview.action === "UPDATE" ? "true" : "false");
    await api("/accounts", { method: "POST", body: formData });
    $("account-dialog").close();
    await loadAccounts();
    /* OCI-N&T API IMPORT SUCCESS NAV */
    if (typeof showPage === "function") showPage("accounts");
    toast(preview.action === "UPDATE" ? "已有 OCI API 已验证并更新" : "OCI API 已验证并导入", "good");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function loadBulkCurrent() {
  try {
    const job = await api("/bulk/account-checks/current");
    renderBulkJob(job);
  } catch {
    renderBulkJob(null);
  }
}

function waitingSeconds(job) {
  if (!job?.next_account_at) return 0;
  return Math.max(0, Math.ceil((new Date(job.next_account_at).getTime() - Date.now()) / 1000));
}

function renderBulkJob(job) {
  state.bulkJob = job;
  if (!job) {
    $("bulk-progress").hidden = true;
    stopBulkPolling();
    return;
  }
  const active = Boolean(job.is_active);
  $("bulk-progress").hidden = false;
  $("bulk-cancel").hidden = !active;
  $("bulk-title").textContent = `批量检测 #${job.id} · ${job.status}`;
  if (job.status === "WAITING") {
    $("bulk-copy").textContent = `已完成 ${job.completed_count}/${job.total}，下一个账户将在 ${waitingSeconds(job)} 秒后开始`;
  } else if (job.status === "RUNNING") {
    $("bulk-copy").textContent = `正在检测：${job.current_account_name || "当前账户"} · ${job.current_index}/${job.total}`;
  } else {
    $("bulk-copy").textContent = `有效 ${job.alive} · 异常 ${job.abnormal} · 待检测 ${job.unknown}${job.error ? ` · ${job.error}` : ""}`;
  }
  $("bulk-bar").style.width = `${Math.min(100, Number(job.progress_percent || 0))}%`;
  if (active) startBulkPolling(job.id);
  else {
    stopBulkPolling();
    loadAccounts().catch(() => {});
  }
}

function startBulkPolling(jobId) {
  if (state.bulkTimer) return;
  state.bulkTimer = setInterval(async () => {
    try {
      const job = await api(`/bulk/account-checks/${jobId}`);
      renderBulkJob(job);
    } catch {
      stopBulkPolling();
    }
  }, 2000);
}

function stopBulkPolling() {
  if (state.bulkTimer) clearInterval(state.bulkTimer);
  state.bulkTimer = null;
}

async function startBulkCheck(button) {
  let defaultInterval = 30;
  try {
    const saved = await api("/settings/account-check");
    defaultInterval = Math.max(0, Math.min(86400, Number(saved.interval_seconds ?? 30)));
  } catch {
    // Compatibility fallback: the current bulk endpoint accepts an explicit interval.
  }
  const rememberedRaw = localStorage.getItem("oci_nt_bulk_check_interval_seconds");
  if (rememberedRaw !== null) {
    const remembered = Number(rememberedRaw);
    if (
      Number.isFinite(remembered)
      && Number.isInteger(remembered)
      && remembered >= 0
      && remembered <= 86400
    ) {
      defaultInterval = remembered;
    }
  }

  const accountCount = Array.isArray(state.accounts) ? state.accounts.length : 0;
  const values = await openUtilityDialog({
    title: "批量检测 OCI 账户",
    copy: `${accountCount} 个账户 · 严格串行执行`,
    message: "严格按顺序检测：当前账户完成后才进入下一个；间隔可设 0 秒或自定义等待时间。",
    fields: [{
      name: "interval_seconds",
      label: "账户间隔（秒）",
      type: "number",
      value: defaultInterval,
      min: 0,
      max: 86400,
      step: 1,
      required: true,
      full: true,
      help: "0 秒表示检测完一个后立即检测下一个；也可设置等待秒数。",
    }],
    submitText: "确认检测",
    validate: (formValues) => {
      const interval = Number(formValues.interval_seconds);
      if (!Number.isFinite(interval) || !Number.isInteger(interval)) return "请输入整数秒数。";
      if (interval < 0) return "账户间隔不能小于 0 秒。";
      if (interval > 86400) return "账户间隔不能超过 86400 秒。";
      return "";
    },
  });
  if (!values) return;

  const intervalSeconds = Number(values.interval_seconds);
  localStorage.setItem("oci_nt_bulk_check_interval_seconds", String(intervalSeconds));
  setBusy(button, true, "启动中……");
  try {
    const job = await api("/bulk/account-checks", {
      method: "POST",
      body: {
        interval_seconds: intervalSeconds,
        idempotency_key: createIdempotencyKey("account-check"),
      },
    });
    renderBulkJob(job);
    toast(job.deduplicated ? `相同检测请求已存在，继续查看任务 #${job.id}` : `批量检测已启动 · 间隔 ${intervalSeconds} 秒`, "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function cancelBulk() {
  if (!state.bulkJob?.id) return;
  try {
    await api(`/bulk/account-checks/${state.bulkJob.id}/cancel`, { method: "POST" });
    toast("已请求取消；当前账户检测结束后停止");
  } catch (error) {
    toast(error.message, "bad");
  }
}

function selectedTenant() {
  return state.accounts.find((account) => account.id === Number(state.tenantId));
}

function openTenant(accountId, initialTab = "instances") {
  stopLaunchPolling();
  document.body.classList.add("tenant-mode");
  state.tenantId = Number(accountId);
  const snapshot = state.instanceSnapshots.get(Number(accountId));
  state.instances = Array.isArray(snapshot?.instances) ? snapshot.instances : [];
  state.instanceCache = snapshot?.result || null;
  state.instanceCacheAccountId = snapshot ? Number(accountId) : null;
  state.instanceCacheSource = "本地缓存";
  state.launchCatalog = null;
  state.launchResources = null;
  state.launchJobs = [];
  state.launchCatalogLoading = false;
  state.launchCatalogRequestId += 1;
  state.tenantTab = initialTab;
  document.querySelectorAll(".page").forEach((section) => { section.hidden = true; });
  $("tenant-page").hidden = false;
  document.querySelectorAll(".nav").forEach((button) => button.classList.remove("active"));
  $("page-title").textContent = "租户管理";
  $("page-copy").textContent = "所有资源请求均绑定当前租户";
  renderTenantHeader();
  selectTenantTab(initialTab, { automatic: true });
}

function renderTenantHeader() {
  const account = selectedTenant();
  if (!account) return;
  const [statusText] = accountStatus(account);
  $("tenant-avatar").textContent = (account.custom_name || "N")[0].toUpperCase();
  $("tenant-name").textContent = account.custom_name;
  $("tenant-email").textContent = account.email || "未读取邮箱";
  $("tenant-type").textContent = accountType(account);
  $("tenant-region").textContent = `${regionName(account)} · ${account.home_region_key || account.region || "—"}`;
  $("tenant-status").textContent = statusText;
  $("tenant-checked").textContent = `上次检测：${fmtDate(account.last_checked_at)}`;
  if ($("tenant-proxy")) {
    $("tenant-proxy").textContent = account.proxy_enabled
      ? `代理：${account.proxy_last_ip || account.proxy_label || "已启用"}`
      : "代理：未启用";
    $("tenant-proxy").classList.toggle("proxy-active", Boolean(account.proxy_enabled));
  }
}

function stopLaunchPolling() {
  if (state.launchPollTimer) clearTimeout(state.launchPollTimer);
  state.launchPollTimer = null;
}

async function ensureTenantWorkspaceInstances() {
  const account = selectedTenant();
  if (!account) return [];

  if (
    Number(state.instanceCacheAccountId) === Number(account.id) &&
    Array.isArray(state.instances)
  ) {
    return state.instances;
  }

  const accountId = Number(account.id);
  let promise = state.instanceLoadPromises.get(accountId);
  if (!promise) {
    promise = api(`/accounts/${account.id}/instances`).finally(() => state.instanceLoadPromises.delete(accountId));
    state.instanceLoadPromises.set(accountId, promise);
  }
  const result = await promise;

  state.instanceCache = result || {};
  state.instanceCacheAccountId = Number(account.id);
  state.instanceCacheSource = "本地缓存";
  state.instances = Array.isArray(result?.instances) ? result.instances : [];
  state.instanceSnapshots.set(accountId, { result, instances: state.instances });

  return state.instances;
}

async function selectTenantTab(tab, options = {}) {
  stopLaunchPolling();
  state.tenantViewEpoch = Number(state.tenantViewEpoch || 0) + 1;
  state.tenantTab = tab;
  document.querySelectorAll("[data-tenant-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tenantTab === tab);
  });
  if (tab === "instances") {
    loadTenantInstances(false, { automatic: Boolean(options.automatic) });
    return;
  }

  if (tab === "create" || tab === "launch") {
    renderLaunchWorkspace(tab, { automatic: true });
    return;
  }

  if (tab === "account") {
    window.renderTenantAccountSettings();
    return;
  }

  if (window.rcRenderTenantTab) {
    const needsInstances = new Set([
      "network",
      "security",
      "volumes",
      "vnc",
      "metrics",
      "insights",
      "costs",
    ]);

    if (needsInstances.has(tab)) {
      if (!state.instances.length) {
        $("tenant-content").innerHTML = '<section class="panel"><div class="empty">正在读取当前租户的本地实例缓存……</div></section>';
      }

      try {
        await ensureTenantWorkspaceInstances();

        if (state.tenantTab !== tab) return;

        window.rcRenderTenantTab(tab);
      } catch (error) {
        if (state.tenantTab !== tab) return;

        $("tenant-content").innerHTML =
          `<section class="panel"><div class="form-error">${esc(error.message)}</div></section>`;
      }

      return;
    }

    window.rcRenderTenantTab(tab);
    return;
  }

  renderPhaseTab(tab);
}

const phaseTabs = {
  network: ["IP 与 VNIC", "下一阶段接入指定 CIDR 换 IP、IP 质量、主/附属 VNIC、批量 VNIC 与 IPv6。"],
  security: ["安全规则", "下一阶段实现当前租户安全列表读取、添加、删除与批量开放。"],
  volumes: ["引导卷与镜像", "下一阶段完成名称、容量、VPU、备份、镜像与恢复流程。"],
  vnc: ["浏览器 VNC", "下一阶段接入 Console Connection、临时密钥、旧连接清理、websockify 与 noVNC。"],
  storage: ["对象存储", "后续接入 Bucket、对象分页、上传、下载、预览、预签名 URL 与分片续传。"],
  insights: ["流量与费用", "只允许手动查询当前租户并缓存结果，不添加后台采集。"],
  tenancy: ["区域、IAM、配额与审计", "后续接入区域订阅、IAM 用户与组、密码策略、配额和 OCI 审计。"],
};

function renderPhaseTab(tab) {
  const [title, copy] = phaseTabs[tab] || ["功能", "正在重构"];
  $("tenant-content").innerHTML = `<section class="phase-card"><h2>${esc(title)}</h2><p>${esc(copy)}</p><span class="scope-banner">当前租户：${esc(selectedTenant()?.custom_name || "—")} · 当前页面不会自动查询 OCI</span></section>`;
}

function selectLaunchSideTab(tab) {
  const normalized = tab === "profiles" ? "profiles" : "jobs";
  document.querySelectorAll("[data-launch-side-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.launchSideTab === normalized);
  });
  const profiles = $("tenant-launch-side-profiles");
  const jobs = $("tenant-launch-side-jobs");
  if (profiles) profiles.hidden = normalized !== "profiles";
  if (jobs) jobs.hidden = normalized !== "jobs";
}

function setLaunchEnvironmentState(kind, label) {
  const stateBadge = $("tenant-launch-env-state");
  const card = $("tenant-launch-network-card");
  if (stateBadge) {
    stateBadge.className = `launch-state ${kind || "loading"}`;
    stateBadge.textContent = label || "读取中";
  }
  if (card) {
    ["is-loading", "is-ready", "is-warning", "is-error"].forEach((name) => card.classList.remove(name));
    card.classList.add(`is-${kind || "loading"}`);
  }
}

function renderLaunchEnvironmentChips(resources = state.launchResources, catalog = state.launchCatalog) {
  const box = $("tenant-launch-env-chips");
  if (!box) return;
  const subnet = resources?.recommended_subnet;
  const image = resources?.images?.find((item) => item.id === $("tenant-launch-image")?.value) || resources?.images?.[0];
  const chips = [
    ["区域", catalog?.region || $("tenant-launch-region")?.value || "读取中"],
    ["可用域", $("tenant-launch-ad")?.value || "自动选择"],
    ["网络", subnet ? `${subnet.display_name || "子网"} · ${subnet.cidr_block || "—"}` : "等待可用网络"],
    ["镜像", image?.display_name || "等待 Ubuntu 镜像"],
  ];
  box.innerHTML = chips.map(([key, value]) => `<span><small>${esc(key)}</small><strong title="${esc(value)}">${esc(value)}</strong></span>`).join("");
}

function renderLaunchWorkspace(tab, { automatic = true } = {}) {
  const isRetry = tab === "launch";
  state.launchCatalog = null;
  state.launchResources = null;
  const account = selectedTenant();
  if (!account) return;
  $("tenant-content").innerHTML = `<div id="tenant-launch-layout" class="launch-layout launch-workspace-v2">
    <form id="tenant-launch-form" class="panel form-panel launch-form launch-editor">
      <div class="panel-head launch-editor-head">
        <div>
          <h2>${isRetry ? "抢机任务" : "创建实例"}</h2>
          <p>当前租户：${esc(account.custom_name)} · 自动读取区域、网络和兼容 Ubuntu 镜像</p>
        </div>
        <div class="launch-head-actions">
          ${isRetry ? '<button id="tenant-launch-save-profile" class="button" type="button">保存当前配置</button>' : ""}
          <button id="tenant-launch-catalog" class="button" type="button">重新读取</button>
        </div>
      </div>

      <section class="launch-section">
        <div class="launch-section-title"><span>01</span><div><strong>基础配置</strong><small>只保留 ARM 与 AMD；实例名称默认为 N&amp;T。</small></div></div>
        <div class="launch-presets" aria-label="常用实例规格"><button class="active" type="button" data-launch-preset="arm-small"><strong>ARM 日常</strong><small>1 OCPU · 6 GB · 50 GB</small></button><button type="button" data-launch-preset="arm-free"><strong>ARM 免费额度</strong><small>4 OCPU · 24 GB · 50 GB</small></button><button type="button" data-launch-preset="amd-free"><strong>AMD 免费额度</strong><small>E2.1.Micro · 1 GB · 50 GB</small></button></div>
        <div class="form-grid launch-basic-grid">
          <label><span>架构</span><select id="tenant-launch-architecture" required><option value="ARM">ARM · VM.Standard.A1.Flex</option><option value="AMD">AMD · VM.Standard.E2.1.Micro</option></select></label>
          <label><span>实例名称</span><input id="tenant-launch-name" value="N&amp;T" maxlength="255" required><small>多台自动命名为 N&amp;T-1、N&amp;T-2。</small></label>
          <label><span>创建数量</span><input id="tenant-launch-count" type="number" min="1" max="20" value="1" required></label>
          <label><span>OCPU</span><input id="tenant-launch-ocpus" type="number" min="1" max="160" step="1" value="1"><small id="tenant-launch-ocpus-help">ARM 灵活配置</small></label>
          <label><span>内存 GB</span><input id="tenant-launch-memory" type="number" min="1" max="2048" step="1" value="6"><small id="tenant-launch-memory-help">ARM 灵活配置</small></label>
          <label><span>引导卷 GB</span><input id="tenant-launch-boot" type="number" min="50" max="32768" value="50"></label>
        </div>
      </section>

      ${isRetry ? `<section class="launch-section launch-retry-section"><div class="launch-section-title"><span>02</span><div><strong>开机策略</strong><small>不限执行次数，创建成功后自动停止；参数可随时调整。</small></div></div><div class="launch-interval-presets"><span>常用间隔</span><button type="button" data-launch-interval="30">30 秒</button><button type="button" data-launch-interval="60">1 分钟</button><button class="active" type="button" data-launch-interval="120">2 分钟</button><button type="button" data-launch-interval="300">5 分钟</button></div><div class="form-grid launch-retry-grid"><input id="tenant-launch-attempts" type="hidden" value="0"><label><span>每轮间隔（秒）</span><input id="tenant-launch-interval" type="number" min="15" max="86400" value="120" required><small>失败后等待多久再执行下一轮。</small></label><label><span>每轮并发请求</span><input id="tenant-launch-concurrency" type="number" min="1" max="5" value="1" required><small>建议保持 1，降低 OCI 限流概率。</small></label><label class="launch-login-row"><span>登录方式</span><select id="tenant-launch-login-mode"><option value="SSH_KEY">ubuntu + SSH 密钥 + 公网 IP</option><option value="ROOT_PASSWORD">root + 密码 + 公网 IP</option></select><small>只影响新创建的实例。</small></label><label id="tenant-launch-root-password-field" class="launch-password-row" hidden><span>Root 密码</span><div class="launch-password-control"><input id="tenant-launch-root-password" type="password" minlength="12" maxlength="128" autocomplete="new-password" placeholder="留空则由服务器自动生成"><button id="tenant-launch-password-generate" class="button" type="button">随机生成</button><button id="tenant-launch-password-show" class="button" type="button">显示</button></div><small>至少 12 位；成功后通过 Telegram 发送一次。</small></label></div></section>` : `<input id="tenant-launch-attempts" type="hidden" value="1"><input id="tenant-launch-interval" type="hidden" value="120"><input id="tenant-launch-concurrency" type="hidden" value="1">`}

      <section id="tenant-launch-network-card" class="launch-auto-card is-loading">
        <div class="launch-auto-main">
          <div class="launch-auto-title"><span id="tenant-launch-env-state" class="launch-state loading">读取中</span><div><strong>自动环境</strong><p id="tenant-launch-network-summary">正在读取当前租户配置……</p></div></div>
          <div id="tenant-launch-env-chips" class="launch-env-chips"><span><small>区域</small><strong>${esc(account.home_region_key || account.region || "读取中")}</strong></span><span><small>网络</small><strong>自动选择</strong></span><span><small>镜像</small><strong>Ubuntu</strong></span></div>
        </div>
        <button id="tenant-launch-network-create" class="button primary-soft" type="button" hidden>一键创建 N&amp;T 网络</button>
      </section>

      <details class="launch-advanced">
        <summary><span>高级设置</span><small>创建位置、Ubuntu 镜像、SSH 公钥及 IP 分配</small></summary>
        <div class="form-grid two-col">
          <label><span>区域</span><input id="tenant-launch-region" value="${esc(account.home_region_key || account.region || "")}" readonly></label>
          <label><span>配置</span><input id="tenant-launch-shape" value="VM.Standard.A1.Flex" readonly></label>
          <label><span>实例区间</span><select id="tenant-launch-compartment" required disabled><option value="">自动选择</option></select></label>
          <label><span>可用域</span><select id="tenant-launch-ad" required disabled><option value="">自动选择</option></select></label>
          <label class="wide"><span>Ubuntu 镜像</span><select id="tenant-launch-image" required disabled><option value="">自动选择最新版兼容镜像</option></select></label>
          <label id="tenant-launch-ssh-field" class="wide"><span>SSH 公钥</span><textarea id="tenant-launch-ssh" rows="3" placeholder="ssh-ed25519 ..."></textarea><small>选择 ubuntu + SSH 密钥登录时必填。</small></label>
          <div class="launch-switches wide"><label class="toggle"><span>分配临时公网 IPv4<small id="tenant-launch-public-help">自动网络默认支持</small></span><input id="tenant-launch-public" type="checkbox" checked></label><label class="toggle"><span>分配 IPv6<small id="tenant-launch-ipv6-help">读取网络后自动判断</small></span><input id="tenant-launch-ipv6" type="checkbox" disabled></label></div>
        </div>
      </details>

      <input id="tenant-launch-network-compartment" type="hidden" value="">
      <input id="tenant-launch-subnet" type="hidden" value="">

      <footer class="launch-submit-bar">
        <div><strong id="tenant-launch-submit-title">${isRetry ? "准备启动抢机任务" : "准备创建实例"}</strong><p id="tenant-launch-status" class="muted">正在自动读取当前租户 OCI 配置……</p></div>
        <button id="tenant-launch-submit" class="button primary" type="submit" disabled>${isRetry ? "启动抢机任务" : "创建实例"}</button>
      </footer>
    </form>

    <section id="tenant-launch-side" class="panel launch-side-panel">
      <div class="launch-live-summary" aria-label="当前开机配置摘要">
        <div class="launch-live-summary-head">
          <div><strong>当前配置</strong><small>随左侧表单实时更新</small></div>
          <span id="tenant-launch-summary-state" class="badge muted">准备中</span>
        </div>
        <dl class="launch-live-summary-grid">
          <div><dt>规格</dt><dd id="tenant-launch-summary-shape">ARM · A1.Flex</dd></div>
          <div><dt>数量</dt><dd id="tenant-launch-summary-count">1 台</dd></div>
          <div><dt>计算</dt><dd id="tenant-launch-summary-compute">1 OCPU · 6 GB</dd></div>
          <div><dt>引导卷</dt><dd id="tenant-launch-summary-boot">50 GB</dd></div>
          <div><dt>区域</dt><dd id="tenant-launch-summary-region">${esc(account.home_region_key || account.region || "读取中")}</dd></div>
          <div><dt>可用域</dt><dd id="tenant-launch-summary-ad">自动选择</dd></div>
          <div><dt>登录</dt><dd id="tenant-launch-summary-login">SSH 密钥</dd></div>
          <div><dt>策略</dt><dd id="tenant-launch-summary-strategy">${isRetry ? "每 120 秒 · 并发 1" : "单次创建"}</dd></div>
        </dl>
        <div class="launch-live-summary-network"><span>网络</span><strong id="tenant-launch-summary-network">正在读取自动环境</strong></div>
      </div>
      <div class="launch-side-head"><div class="launch-side-tabs"><button class="active" data-launch-side-tab="profiles" type="button">保存配置 <span id="tenant-launch-profile-count">0</span></button><button data-launch-side-tab="jobs" type="button">任务 <span id="tenant-launch-job-count">0</span></button></div><button class="button small" data-refresh-launch-jobs type="button">刷新</button></div>
      <div id="tenant-launch-side-profiles" class="launch-side-body"><div id="tenant-launch-profile-host"><div class="empty compact">正在读取保存配置……</div></div></div>
      <div id="tenant-launch-side-jobs" class="launch-side-body" hidden><div id="tenant-launch-jobs"><div class="empty compact">正在读取任务……</div></div></div>
    </section>
  </div>`;
  $("tenant-launch-form").addEventListener("submit", submitLaunchJob);
  $("tenant-launch-form").addEventListener("input", renderLaunchLiveSummary);
  $("tenant-launch-form").addEventListener("change", renderLaunchLiveSummary);

  const syncLaunchLoginMode = () => {
    const rootMode = $("tenant-launch-login-mode")?.value === "ROOT_PASSWORD";
    if ($("tenant-launch-root-password-field")) {
      $("tenant-launch-root-password-field").hidden = !rootMode;
    }
    if ($("tenant-launch-ssh-field")) {
      $("tenant-launch-ssh-field").hidden = rootMode;
    }
    if ($("tenant-launch-ssh")) {
      $("tenant-launch-ssh").required = !rootMode;
    }
  };

  $("tenant-launch-login-mode")?.addEventListener(
    "change",
    () => {
      syncLaunchLoginMode();
      renderLaunchLiveSummary();
    },
  );

  $("tenant-launch-password-generate")?.addEventListener("click", () => {
    const alphabet =
      "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789@#%+=_-";
    const random = new Uint32Array(20);
    crypto.getRandomValues(random);
    $("tenant-launch-root-password").value = Array.from(
      random,
      (value) => alphabet[value % alphabet.length],
    ).join("");
    $("tenant-launch-root-password").type = "text";
    $("tenant-launch-password-show").textContent = "隐藏";
  });

  $("tenant-launch-password-show")?.addEventListener("click", () => {
    const input = $("tenant-launch-root-password");
    input.type = input.type === "password" ? "text" : "password";
    $("tenant-launch-password-show").textContent =
      input.type === "password" ? "显示" : "隐藏";
  });

  syncLaunchLoginMode();
  document.querySelectorAll("[data-launch-preset]").forEach((button) => button.addEventListener("click", () => {
    const preset = button.dataset.launchPreset;
    const architecture = preset === "amd-free" ? "AMD" : "ARM";
    $("tenant-launch-architecture").value = architecture;
    $("tenant-launch-architecture").dispatchEvent(new Event("change", { bubbles: true }));
    if (architecture === "ARM") {
      $("tenant-launch-ocpus").value = preset === "arm-free" ? "4" : "1";
      $("tenant-launch-memory").value = preset === "arm-free" ? "24" : "6";
    }
    $("tenant-launch-boot").value = "50";
    document.querySelectorAll("[data-launch-preset]").forEach((item) => item.classList.toggle("active", item === button));
    renderLaunchLiveSummary();
  }));
  document.querySelectorAll("[data-launch-interval]").forEach((button) => button.addEventListener("click", () => {
    $("tenant-launch-interval").value = button.dataset.launchInterval;
    document.querySelectorAll("[data-launch-interval]").forEach((item) => item.classList.toggle("active", item === button));
    renderLaunchLiveSummary();
  }));

  $("tenant-launch-catalog").addEventListener("click", loadLaunchCatalog);
  $("tenant-launch-network-create").addEventListener("click", ensureLaunchNetwork);
  $("tenant-launch-compartment").addEventListener("change", loadLaunchResources);
  $("tenant-launch-ad").addEventListener("change", loadLaunchResources);
  $("tenant-launch-image").addEventListener("change", () => { renderLaunchEnvironmentChips(); updateLaunchSubmitState(); });
  document.querySelectorAll("[data-launch-side-tab]").forEach((button) => button.addEventListener("click", () => selectLaunchSideTab(button.dataset.launchSideTab)));
  $("tenant-launch-architecture").addEventListener("change", () => {
    const architecture = $("tenant-launch-architecture").value;
    $("tenant-launch-shape").value = architecture === "ARM" ? "VM.Standard.A1.Flex" : "VM.Standard.E2.1.Micro";
    const flexible = architecture === "ARM";
    $("tenant-launch-ocpus").disabled = !flexible;
    $("tenant-launch-memory").disabled = !flexible;
    $("tenant-launch-ocpus").value = flexible ? "1" : "";
    $("tenant-launch-memory").value = flexible ? "6" : "";
    $("tenant-launch-ocpus-help").textContent = flexible ? "ARM 灵活配置" : "AMD E2.1.Micro 固定配置";
    $("tenant-launch-memory-help").textContent = flexible ? "ARM 灵活配置" : "AMD E2.1.Micro 固定配置";
    $("tenant-launch-submit").disabled = true;
    $("tenant-launch-image").innerHTML = '<option value="">正在重新读取兼容 Ubuntu 镜像</option>';
    $("tenant-launch-image").disabled = true;
    setLaunchEnvironmentState("loading", "读取中");
    renderLaunchEnvironmentChips(null, state.launchCatalog);
    renderLaunchLiveSummary();
    if (state.launchCatalog) loadLaunchResources();
  });
  selectLaunchSideTab("profiles");
  renderLaunchLiveSummary();
  if (window.rcEnhanceLaunchWorkspace) window.rcEnhanceLaunchWorkspace();

  $("tenant-launch-save-profile")?.addEventListener("click", () => {
    const nativeSaveButton = $("rc-profile-save");

    if (!nativeSaveButton) {
      toast("保存配置组件尚未就绪，请稍后再试", "bad");
      return;
    }

    nativeSaveButton.click();
  });

  loadLaunchJobs(true);
  if (automatic) queueMicrotask(() => loadLaunchCatalog({ automatic: true }));
}


function launchDomAvailable(accountId) {
  return (
    Number(state.tenantId) === Number(accountId) &&
    (state.tenantTab === "create" || state.tenantTab === "launch") &&
    Boolean($("tenant-launch-form")) &&
    Boolean($("tenant-launch-region")) &&
    Boolean($("tenant-launch-compartment")) &&
    Boolean($("tenant-launch-ad")) &&
    Boolean($("tenant-launch-network-compartment")) &&
    Boolean($("tenant-launch-status"))
  );
}

async function loadLaunchCatalog({ automatic = false } = {}) {
  const account = selectedTenant();
  const button = $("tenant-launch-catalog");
  if (!account || !button || state.launchCatalogLoading) return;
  const accountId = account.id;
  const requestId = ++state.launchCatalogRequestId;
  state.launchCatalogLoading = true;
  setBusy(button, true, automatic ? "自动读取中……" : "读取中……");
  const status = $("tenant-launch-status");
  if (status) status.textContent = "正在自动读取当前租户区域、区间、网络和 Ubuntu 镜像……";
  setLaunchEnvironmentState("loading", "读取中");
  try {
    const catalog = await api(`/accounts/${accountId}/launch/catalog`);
    if (requestId !== state.launchCatalogRequestId || !launchDomAvailable(accountId)) return;
    state.launchCatalog = catalog;
    const regionInput = $("tenant-launch-region");
    const compartmentSelect = $("tenant-launch-compartment");
    const adSelect = $("tenant-launch-ad");
    const networkCompartment = $("tenant-launch-network-compartment");
    const currentStatus = $("tenant-launch-status");

    if (
      !regionInput ||
      !compartmentSelect ||
      !adSelect ||
      !networkCompartment ||
      !currentStatus
    ) return;

    regionInput.value = catalog.region;
    const options = catalog.compartments.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.path || "")}</option>`).join("");
    compartmentSelect.innerHTML = options || '<option value="">没有可用区间</option>';
    adSelect.innerHTML = catalog.availability_domains.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("") || '<option value="">没有可用域</option>';
    compartmentSelect.disabled = !catalog.compartments.length;
    adSelect.disabled = !catalog.availability_domains.length;
    if (!catalog.compartments.length || !catalog.availability_domains.length) throw new Error("当前租户没有可用区间或可用域");
    networkCompartment.value = catalog.compartments[0].id;
    currentStatus.textContent = "已读取租户目录，正在自动选择网络和 Ubuntu 镜像……";
    renderLaunchEnvironmentChips(null, catalog);
    await loadLaunchResources();
    if (!automatic) toast("当前租户配置已重新读取", "good");
  } catch (error) {
    if (requestId !== state.launchCatalogRequestId || !launchDomAvailable(accountId)) return;
    const errorStatus = $("tenant-launch-status");
    if (errorStatus) {
      errorStatus.textContent = `${error.message}。可点击“重新读取”重试。`;
    }
    setLaunchEnvironmentState("error", "读取失败");
    toast(error.message, "bad");
  } finally {
    if (requestId === state.launchCatalogRequestId) {
      state.launchCatalogLoading = false;
      if (button?.isConnected) setBusy(button, false);
    }
  }
}

function renderLaunchLiveSummary() {
  const form = $("tenant-launch-form");
  if (!form) return;

  const architecture = $("tenant-launch-architecture")?.value || "ARM";
  const count = Math.max(1, Number($("tenant-launch-count")?.value || 1));
  const ocpus = architecture === "ARM" ? Number($("tenant-launch-ocpus")?.value || 0) : null;
  const memory = architecture === "ARM" ? Number($("tenant-launch-memory")?.value || 0) : 1;
  const boot = Number($("tenant-launch-boot")?.value || 50);
  const region = $("tenant-launch-region")?.value || selectedTenant()?.home_region_key || selectedTenant()?.region || "读取中";
  const adSelect = $("tenant-launch-ad");
  const ad = adSelect?.selectedOptions?.[0]?.textContent?.trim() || "自动选择";
  const loginMode = $("tenant-launch-login-mode")?.value || "SSH_KEY";
  const interval = Number($("tenant-launch-interval")?.value || 120);
  const concurrency = Math.max(1, Number($("tenant-launch-concurrency")?.value || 1));
  const retryMode = state.tenantTab === "launch";
  const subnet = state.launchResources?.recommended_subnet || null;
  const imageReady = Boolean($("tenant-launch-image")?.value);
  const ready = Boolean(state.launchResources?.shape && $("tenant-launch-subnet")?.value && imageReady);

  const setText = (id, value) => {
    const node = $(id);
    if (node) node.textContent = value;
  };

  setText("tenant-launch-summary-shape", architecture === "ARM" ? "ARM · A1.Flex" : "AMD · E2.1.Micro");
  setText("tenant-launch-summary-count", `${count} 台`);
  setText(
    "tenant-launch-summary-compute",
    architecture === "ARM"
      ? `${ocpus || "—"} OCPU · ${memory || "—"} GB`
      : "固定规格 · 1 GB",
  );
  setText("tenant-launch-summary-boot", `${boot || "—"} GB`);
  setText("tenant-launch-summary-region", region);
  setText("tenant-launch-summary-ad", ad);
  setText("tenant-launch-summary-login", loginMode === "ROOT_PASSWORD" ? "Root 密码" : "SSH 密钥");
  setText(
    "tenant-launch-summary-strategy",
    retryMode ? `每 ${interval} 秒 · 并发 ${concurrency}` : "单次创建",
  );
  setText(
    "tenant-launch-summary-network",
    subnet
      ? `${subnet.display_name || "已选择子网"} · ${subnet.cidr_block || "网络已就绪"}`
      : state.launchCatalog ? "等待可用网络" : "正在读取自动环境",
  );

  const stateBadge = $("tenant-launch-summary-state");
  if (stateBadge) {
    stateBadge.className = `badge ${ready ? "good" : state.launchCatalog ? "warn" : "muted"}`;
    stateBadge.textContent = ready ? "已就绪" : state.launchCatalog ? "待完善" : "准备中";
  }
}

function updateLaunchSubmitState() {
  const resources = state.launchResources;
  const ready = Boolean(resources?.shape && $("tenant-launch-subnet")?.value && $("tenant-launch-image")?.value);
  const button = $("tenant-launch-submit");
  const title = $("tenant-launch-submit-title");
  if (button) button.disabled = !ready;
  if (title) title.textContent = ready
    ? (state.tenantTab === "launch" ? "配置就绪，可启动抢机" : "配置就绪，可创建实例")
    : (state.tenantTab === "launch" ? "等待抢机配置就绪" : "等待创建配置就绪");
  renderLaunchLiveSummary();
}

function renderAutoNetwork(resources, catalog) {
  const summary = $("tenant-launch-network-summary");
  const create = $("tenant-launch-network-create");
  const subnetInput = $("tenant-launch-subnet");
  const networkCompartment = $("tenant-launch-network-compartment");
  const publicToggle = $("tenant-launch-public");
  const publicHelp = $("tenant-launch-public-help");
  const ipv6Toggle = $("tenant-launch-ipv6");
  const ipv6Help = $("tenant-launch-ipv6-help");
  const subnet = resources?.recommended_subnet || null;
  if (
    !summary ||
    !create ||
    !subnetInput ||
    !networkCompartment ||
    !publicToggle ||
    !publicHelp ||
    !ipv6Toggle ||
    !ipv6Help
  ) return;
  if (!subnet) {
    summary.textContent = "当前区域没有可用子网。确认后可自动创建 N&T-VCN、Internet Gateway、默认路由和区域子网。";
    create.hidden = false;
    subnetInput.value = "";
    publicToggle.checked = true;
    publicToggle.disabled = false;
    publicHelp.textContent = "创建 N&T 网络后可分配";
    ipv6Toggle.checked = false;
    ipv6Toggle.disabled = true;
    ipv6Help.textContent = "创建网络后自动判断";
    renderLaunchEnvironmentChips(resources, catalog);
    setLaunchEnvironmentState("warning", "缺少网络");
    return;
  }
  const owner = catalog.compartments.find((item) => item.id === subnet.compartment_id)?.name || "可访问区间";
  const publicText = subnet.prohibit_public_ip_on_vnic ? "私有子网" : "可分配公网 IPv4";
  const ipv6Text = subnet.has_ipv6 ? `IPv6 ${subnet.ipv6_cidr_blocks.join(", ")}` : "无 IPv6";
  summary.textContent = `${subnet.display_name || "子网"} · ${subnet.cidr_block || "—"} · ${owner} · ${publicText} · ${ipv6Text}`;
  create.hidden = true;
  subnetInput.value = subnet.id;
  networkCompartment.value = subnet.compartment_id || catalog.compartments[0].id;
  const publicCapable = !subnet.prohibit_public_ip_on_vnic && subnet.has_ipv4_internet_route;
  publicToggle.disabled = !publicCapable;
  publicToggle.checked = publicCapable;
  publicHelp.textContent = publicCapable ? "当前自动子网可直连公网" : "当前子网不支持公网 IPv4";
  ipv6Toggle.disabled = !subnet.has_ipv6;
  if (!subnet.has_ipv6) ipv6Toggle.checked = false;
  ipv6Help.textContent = subnet.has_ipv6 ? "当前自动子网支持 IPv6" : "当前自动子网未启用 IPv6";
  renderLaunchEnvironmentChips(resources, catalog);
}

async function loadLaunchResources(options = {}) {
  // The ensure endpoint can return a usable existing subnet even when the
  // follow-up catalog scan does not recommend it. Keep that confirmed subnet
  // for this refresh so the form does not fall back to "missing network".
  const preferredSubnet = options?.preferredSubnet?.id ? options.preferredSubnet : null;
  const forceRefresh = options?.forceRefresh === true;
  const account = selectedTenant();
  const resourceRequestId =
    Number(state.launchResourceRequestId || 0) + 1;
  state.launchResourceRequestId = resourceRequestId;
  const catalog = state.launchCatalog;
  const compartment = $("tenant-launch-compartment")?.value;
  const ad = $("tenant-launch-ad")?.value;
  const architecture = $("tenant-launch-architecture")?.value || "ARM";
  if (!account || !catalog || !compartment || !ad) return;
  if (!launchDomAvailable(account.id)) return;

  const launchSubmit = $("tenant-launch-submit");
  const launchImage = $("tenant-launch-image");
  const launchStatus = $("tenant-launch-status");
  const launchShape = $("tenant-launch-shape");

  if (!launchSubmit || !launchImage || !launchStatus || !launchShape) return;

  const previousResources = state.launchResources;
  state.launchResources = null;
  launchSubmit.disabled = true;
  launchImage.disabled = true;
  launchStatus.textContent = `正在自动查找网络及 ${architecture} 兼容 Ubuntu 镜像……`;
  setLaunchEnvironmentState("loading", "配置中");
  try {
    const query = new URLSearchParams({
      catalog_token: catalog.catalog_token,
      compartment_id: compartment,
      network_compartment_id: catalog.compartments[0].id,
      availability_domain: ad,
      architecture,
    });
    // ui2.js caches launch catalog reads in SQLite. After ensuring a network,
    // use a one-off key so this request reaches the API and registers the new
    // subnet in launch_catalog_resources before a job is submitted.
    if (forceRefresh) query.set("network_refresh", `${Date.now()}`);
    if (preferredSubnet?.id) query.set("preferred_subnet_id", preferredSubnet.id);
    const resources = await api(`/accounts/${account.id}/launch/catalog/resources?${query}`);

    if (
      resourceRequestId !== Number(state.launchResourceRequestId) ||
      !launchDomAvailable(account.id) ||
      !launchStatus.isConnected
    ) {
      return null;
    }
    if (!resources.recommended_subnet && preferredSubnet) {
      resources.recommended_subnet = {
        ...preferredSubnet,
        ipv6_cidr_blocks: Array.isArray(preferredSubnet.ipv6_cidr_blocks)
          ? preferredSubnet.ipv6_cidr_blocks
          : [],
      };
    }
    state.launchResources = resources;
    renderAutoNetwork(resources, catalog);
    launchImage.innerHTML = resources.images.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name || "Ubuntu")} · ${esc(item.operating_system_version || "")}</option>`).join("") || '<option value="">没有兼容 Ubuntu 镜像</option>';
    launchImage.disabled = !resources.images.length;
    launchShape.value = resources.shape || (architecture === "ARM" ? "VM.Standard.A1.Flex" : "VM.Standard.E2.1.Micro");
    renderLaunchEnvironmentChips(resources, catalog);
    updateLaunchSubmitState();
    const notes = [];
    if (!resources.recommended_subnet) notes.push("没有网络，请使用一键创建 N&T 网络");
    if (resources.shape_error) notes.push(resources.shape_error);
    if (resources.image_error && resources.image_error !== resources.shape_error) notes.push(resources.image_error);
    launchStatus.textContent = `自动配置完成：${resources.recommended_subnet ? "网络已选" : "等待创建网络"}，Ubuntu 镜像 ${resources.images.length} 个${resources.shape ? `，配置 ${resources.architecture} · ${resources.shape}` : ""}${notes.length ? `。${notes.join("；")}` : ""}`;
    setLaunchEnvironmentState(resources.recommended_subnet && resources.images.length && resources.shape ? "ready" : "warning", resources.recommended_subnet ? "已就绪" : "缺少网络");
    return resources;
  } catch (error) {
    state.launchResources = previousResources;
    if (previousResources) renderAutoNetwork(previousResources, catalog);
    if (
      resourceRequestId === Number(state.launchResourceRequestId) &&
      launchDomAvailable(account?.id) &&
      launchStatus?.isConnected
    ) {
      launchStatus.textContent = error.message;
    }
    setLaunchEnvironmentState("error", "配置失败");
    toast(error.message, "bad");
    return null;
  }
}

async function ensureLaunchNetwork() {
  const account = selectedTenant();
  const catalog = state.launchCatalog;
  if (!account || !catalog) return;
  if (!await openConfirmDialog({ title: "创建 N&T 网络", message: "当前租户没有可用子网。将创建 N&T-VCN、Internet Gateway、默认公网路由和区域子网。", submitText: "创建网络" })) return;
  const button = $("tenant-launch-network-create");
  setBusy(button, true, "创建中……");
  setLaunchEnvironmentState("loading", "创建网络");
  try {
    const result = await api(`/accounts/${account.id}/launch/network/ensure`, {
      method: "POST",
      body: {
        catalog_token: catalog.catalog_token,
        compartment_id: $("tenant-launch-compartment").value,
        availability_domain: $("tenant-launch-ad").value,
      },
    });
    const ensuredSubnet = result?.subnet?.id ? result.subnet : null;
    toast(result.message || "N&T 网络已准备", "good");
    await loadLaunchResources({ preferredSubnet: ensuredSubnet, forceRefresh: true });
  } catch (error) {
    const status = $("tenant-launch-status");
    if (status?.isConnected) {
      status.textContent = error.message;
      setLaunchEnvironmentState("error", "创建失败");
    }
    toast(error.message, "bad");
  } finally {
    if (button?.isConnected) setBusy(button, false);
  }
}

function optionalNumber(id) {
  const value = $(id)?.value.trim();
  return value ? Number(value) : null;
}

async function submitLaunchJob(event) {
  event.preventDefault();
  const account = selectedTenant();
  const catalog = state.launchCatalog;
  const button = $("tenant-launch-submit");
  if (!account || !catalog) { toast("请先读取当前租户配置", "bad"); return; }
  setBusy(button, true, state.tenantTab === "launch" ? "启动中……" : "提交中……");
  try {
    // Always perform one live resource read immediately before submission.
    // This keeps the server-side launch catalog in sync even if the page was
    // originally rendered from SQLite cache.
    const previousSubnet = state.launchResources?.recommended_subnet || null;
    await loadLaunchResources({
      forceRefresh: true,
      preferredSubnet: previousSubnet,
    });
    if (!state.launchResources?.recommended_subnet || !$("tenant-launch-subnet")?.value) {
      throw new Error("网络目录尚未同步，请重新准备网络后再试");
    }
    const payload = {
      mode: state.tenantTab === "launch" ? "CAPACITY_RETRY" : "CREATE",
      catalog_token: catalog.catalog_token,
      compartment_id: $("tenant-launch-compartment").value,
      availability_domain: $("tenant-launch-ad").value,
      subnet_id: $("tenant-launch-subnet").value,
      image_id: $("tenant-launch-image").value,
      architecture: $("tenant-launch-architecture").value,
      display_name: $("tenant-launch-name").value.trim() || "N&T",
      login_mode: $("tenant-launch-login-mode")?.value || "SSH_KEY",
      root_password:
        $("tenant-launch-login-mode")?.value === "ROOT_PASSWORD"
          ? ($("tenant-launch-root-password")?.value.trim() || null)
          : null,
      ssh_public_key:
        $("tenant-launch-login-mode")?.value === "SSH_KEY"
          ? ($("tenant-launch-ssh").value.trim() || null)
          : null,
      assign_public_ip: $("tenant-launch-public").checked,
      assign_ipv6_ip: $("tenant-launch-ipv6").checked,
      ocpus: optionalNumber("tenant-launch-ocpus"),
      memory_in_gbs: optionalNumber("tenant-launch-memory"),
      boot_volume_size_in_gbs: optionalNumber("tenant-launch-boot"),
      requested_count: Number($("tenant-launch-count").value),
      max_attempts: Number($("tenant-launch-attempts").value),
      retry_interval_seconds: Number($("tenant-launch-interval").value),
      concurrency: Number($("tenant-launch-concurrency").value),
    };
    const job = await api(`/accounts/${account.id}/launch/jobs`, { method: "POST", body: payload });
    toast(state.tenantTab === "launch" ? "开机任务已启动，将持续运行直到成功" : "创建任务已提交", "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function loadLaunchJobs(schedule = true) {
  const account = selectedTenant();
  const box = $("tenant-launch-jobs");
  if (!account || !box) return;
  try {
    state.launchJobs = await api(`/accounts/${account.id}/launch/jobs?limit=50`);
    renderLaunchJobs();
    if (schedule && state.launchJobs.some((job) => job.is_active) && ["create", "launch"].includes(state.tenantTab)) {
      stopLaunchPolling();
      state.launchPollTimer = setTimeout(() => loadLaunchJobs(true), 2000);
    }
  } catch (error) { box.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; }
}

function launchStatusLabel(status) {
  return ({ PENDING: "等待", RUNNING: "运行中", WAITING: "等待重试", CANCELLING: "取消中", COMPLETED: "完成", CANCELLED: "已取消", FAILED: "失败", INTERRUPTED: "已中断" })[status] || status;
}

function launchJobStatusLabel(job) {
  const capacity = /out of host capacity|outofhostcapacity|容量不足/i.test(String(job?.last_error || ""));
  if (capacity && job?.is_active) return "等待容量";
  if (capacity && job?.status === "FAILED") return "容量不足";
  return launchStatusLabel(job?.status);
}

function renderLaunchJobs() {
  const box = $("tenant-launch-jobs");
  const count = $("tenant-launch-job-count");
  const side = $("tenant-launch-side");
  if (!box) return;
  if (count) count.textContent = String(state.launchJobs.length);
  const activeJobs = state.launchJobs.filter((job) => job.is_active).length;
  if (side) {
    side.classList.toggle("has-active-jobs", activeJobs > 0);
    side.classList.remove("launch-side-empty");
  }
  if (activeJobs > 0 && side && !side.dataset.jobsAutoOpened) {
    side.dataset.jobsAutoOpened = "1";
    selectLaunchSideTab("jobs");
  }
  if (!state.launchJobs.length) {
    box.innerHTML = '<div class="empty compact"><strong>暂无任务</strong><span>创建实例或运行保存配置后，任务状态会显示在这里。</span></div>';
    return;
  }
  box.innerHTML = `<div class="launch-job-list">${state.launchJobs.map((job) => `<article class="launch-job-card"><div class="launch-job-title"><div><strong>${job.mode === "CAPACITY_RETRY" ? "开机任务" : "创建任务"}</strong><small>${esc(job.request?.display_name || "N&T")} · ${esc(job.request?.architecture || "—")} · ${esc(job.request?.shape || "—")}</small></div><span class="badge ${job.status === "COMPLETED" ? "good" : job.status === "FAILED" ? "bad" : job.is_active ? "warn" : "muted"}">${esc(launchJobStatusLabel(job))}</span></div><div class="launch-job-progress"><div class="progress-track"><span style="width:${Math.min(100, Number(job.progress_percent || 0))}%"></span></div><small>成功 ${job.success_count}/${job.requested_count} · 失败 ${job.failure_count} · 已运行 ${job.current_attempt} 轮</small>${job.next_attempt_at ? `<small>下次：${fmtDate(job.next_attempt_at)}</small>` : ""}${job.last_error ? `<small class="bad-text" title="${esc(job.last_error)}">${esc(job.last_error)}</small>` : ""}</div><div class="inline-actions compact"><button class="button small" data-launch-detail="${job.id}" type="button">详情</button>${job.is_active ? `<button class="button small danger" data-launch-cancel="${job.id}" type="button">停止</button>` : `${Number(job.success_count || 0) < Number(job.requested_count || 0) ? `<button class="button small primary" data-launch-retry-failed="${job.id}" type="button">继续运行</button>` : ""}<button class="button small" data-launch-clone="${job.id}" type="button">重新运行</button><button class="button small" data-launch-reset="${job.id}" type="button">重置</button><button class="button small danger" data-launch-delete="${job.id}" type="button">删除</button>`}</div></article>`).join("")}</div>`;
}
function closeLaunchJobDrawer() {
  document.querySelector(".launch-job-drawer-backdrop")?.remove();
  document.querySelector(".launch-job-drawer")?.remove();
}

function openLaunchJobDrawer(job) {
  closeLaunchJobDrawer();
  const attempts = Array.isArray(job.attempts) ? [...job.attempts].reverse() : [];
  const backdrop = document.createElement("button");
  backdrop.type = "button";
  backdrop.className = "launch-job-drawer-backdrop";
  backdrop.setAttribute("aria-label", "关闭任务详情");
  const drawer = document.createElement("aside");
  drawer.className = "launch-job-drawer";
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.innerHTML = `<header><div><small>抢机任务 #${job.id}</small><h2>${esc(job.request?.display_name || "N&T")}</h2></div><button class="button" data-close-launch-drawer type="button">关闭</button></header><div class="launch-job-drawer-summary"><span><small>状态</small><strong>${esc(launchJobStatusLabel(job))}</strong></span><span><small>成功</small><strong>${Number(job.success_count || 0)} / ${Number(job.requested_count || 0)}</strong></span><span><small>执行轮次</small><strong>${Number(job.current_attempt || 0)}</strong></span><span><small>重试间隔</small><strong>${Number(job.retry_interval_seconds || 0)} 秒</strong></span></div>${job.last_error ? `<div class="launch-job-drawer-error"><strong>最近错误</strong><p>${esc(job.last_error)}</p></div>` : ""}<section class="launch-job-attempts"><div class="launch-job-attempts-head"><strong>执行记录</strong><span>${attempts.length} 条</span></div>${attempts.length ? attempts.map((attempt) => `<article><span class="launch-attempt-dot ${attempt.status === "SUCCESS" ? "good" : "bad"}"></span><div><strong>第 ${attempt.round_no} 轮 · ${esc(attempt.display_name || `实例 ${attempt.sequence_no}`)}</strong><small>${esc(attempt.status || "—")} · ${fmtDate(attempt.created_at)}</small>${attempt.instance_id ? `<code title="${esc(attempt.instance_id)}">${esc(attempt.instance_id)}</code>` : ""}${attempt.error ? `<p>${esc(attempt.error)}</p>` : ""}</div></article>`).join("") : `<div class="empty compact">尚无执行记录</div>`}</section>`;
  backdrop.addEventListener("click", closeLaunchJobDrawer);
  drawer.querySelector("[data-close-launch-drawer]").addEventListener("click", closeLaunchJobDrawer);
  document.body.append(backdrop, drawer);
}

async function showLaunchJobDetail(jobId) {
  const account = selectedTenant();
  if (!account) return;
  try {
    const job = await api(`/accounts/${account.id}/launch/jobs/${jobId}`);
    openLaunchJobDrawer(job);
  } catch (error) { toast(error.message, "bad"); }
}

async function cancelLaunchJob(jobId) {
  const account = selectedTenant();
  if (!account || !await openConfirmDialog({ title: "停止开机任务", message: "当前开机流程会在本次请求结束后停止。", submitText: "确认停止", danger: true })) return;
  try {
    const result = await api(`/accounts/${account.id}/launch/jobs/${jobId}/cancel`, { method: "POST" });
    toast(result.message);
    await loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function cloneLaunchJob(jobId) {
  const account = selectedTenant();
  if (!account || !await openConfirmDialog({ title: "重新运行开机任务", message: "将按相同配置执行资源预检并启动。", submitText: "重新运行" })) return;
  try {
    const result = await api(`/accounts/${account.id}/launch/jobs/${jobId}/clone`, { method: "POST" });
    toast("开机任务已启动", "good");
    await loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function retryFailedLaunchJob(jobId) {
  const account = selectedTenant();
  const job = state.launchJobs.find((item) => Number(item.id) === Number(jobId));
  const remaining = Math.max(0, Number(job?.requested_count || 0) - Number(job?.success_count || 0));
  if (!account || !remaining) return;
  if (!confirm(`继续运行尚未成功的 ${remaining} 个配置吗？已成功项目不会重跑。`)) return;
  try {
    const result = await api(`/accounts/${account.id}/launch/jobs/${jobId}/retry-failed`, { method: "POST" });
    toast(`开机任务已启动，仅处理 ${result.retried_count} 个未成功配置`, "good");
    await loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function resetLaunchJob(jobId) {
  const account = selectedTenant();
  if (!account || !await openConfirmDialog({ title: "重置任务统计", message: "将清除失败次数和尝试记录，但保留任务配置。", submitText: "确认重置", danger: true })) return;
  try {
    await api(`/accounts/${account.id}/launch/jobs/${jobId}/reset`, { method: "POST" });
    toast("任务统计已重置", "good");
    await loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteLaunchJob(jobId) {
  const account = selectedTenant();
  if (!account || !await openConfirmDialog({ title: "删除开机任务", message: "将永久删除该任务及全部尝试记录，不会删除已创建的 OCI 实例。", submitText: "删除任务", danger: true })) return;
  try {
    await api(`/accounts/${account.id}/launch/jobs/${jobId}`, { method: "DELETE" });
    toast("任务已删除", "good");
    await loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function loadTenantInstances(sync, { automatic = false } = {}) {
  const account = selectedTenant();
  if (!account) return;

  const requestedTenantId = Number(account.id);
  const requestedViewEpoch = Number(state.tenantViewEpoch || 0);

  const snapshot = state.instanceSnapshots.get(requestedTenantId);
  if (snapshot && !sync) {
    state.instanceCache = snapshot.result;
    state.instances = snapshot.instances;
    state.instanceCacheAccountId = requestedTenantId;
    renderTenantInstances({ refreshing: true });
  } else {
    $("tenant-content").innerHTML = `<div class="empty">${sync ? "正在从 OCI 同步当前租户实例……" : "正在读取本地实例缓存……"}</div>`;
  }
  try {
    let promise = state.instanceLoadPromises.get(requestedTenantId);
    if (!promise || sync) {
      promise = sync ? api(`/accounts/${account.id}/instances/sync`, { method: "POST" }) : api(`/accounts/${account.id}/instances`);
      state.instanceLoadPromises.set(requestedTenantId, promise);
    }
    const result = await promise.finally(() => {
      if (state.instanceLoadPromises.get(requestedTenantId) === promise) state.instanceLoadPromises.delete(requestedTenantId);
    });
    if (Number(state.tenantId) !== requestedTenantId) return;

    state.instanceCache = result;
    state.instanceCacheAccountId = requestedTenantId;
    state.instanceCacheSource = sync ? "OCI 实时同步" : "本地缓存";
    state.instances = result.instances || [];
    state.instanceSnapshots.set(requestedTenantId, { result, instances: state.instances });

    if (
      state.tenantTab === "instances" &&
      Number(state.tenantViewEpoch || 0) === requestedViewEpoch
    ) {
      renderTenantInstances();
      if (sync && !automatic) toast("当前租户实例已同步", "good");
    }
  } catch (error) {
    if (snapshot && !sync) toast(`实例缓存刷新失败：${error.message}`, "bad");
    else $("tenant-content").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  }
}

function primaryVnic(instance) {
  return (instance.vnics || []).find((vnic) => vnic.is_primary) || (instance.vnics || [])[0] || null;
}

function renderTenantInstances({ refreshing = false } = {}) {
  const account = selectedTenant();
  const result = state.instanceCache || {};
  const list = state.instances;
  const runningCount = list.filter((item) => item.lifecycle_state === "RUNNING").length;
  const header = `<div class="tenant-toolbar tenant-instance-head"><div><h2>实例 <span class="count-pill">${list.length}</span></h2><p>${refreshing ? "已先显示缓存，正在后台更新" : `本地缓存 · 上次同步 ${fmtDate(result.last_synced_at)}`} · 运行中 ${runningCount}</p></div><button class="button primary" data-sync-tenant-instances type="button">从 OCI 同步</button></div>`;
  if (!list.length) {
    $("tenant-content").innerHTML = `${header}<section class="panel"><div class="empty">缓存中没有实例。点击“从 OCI 同步当前租户”后查询。</div></section>`;
    return;
  }
  $("tenant-content").innerHTML = `${header}<section class="panel tenant-instance-panel"><div class="tenant-instance-table-wrap"><table class="tenant-instance-table"><thead><tr><th>实例</th><th>状态 / 区域</th><th>配置</th><th>网络</th><th>操作</th></tr></thead><tbody>${list.map((instance) => {
    const vnic = primaryVnic(instance);
    const running = instance.lifecycle_state === "RUNNING";
    const stopped = instance.lifecycle_state === "STOPPED";
    const instanceName = instance.display_name || "N&T";
    return `<tr><td><button class="instance-detail-link" data-instance-detail data-account-id="${account.id}" data-instance-id="${esc(instance.id)}" type="button">${esc(instanceName)}</button><small class="mono tenant-instance-ocid" title="${esc(instance.id)}">${esc(instance.id)}</small></td><td><span class="badge ${running ? "good" : stopped ? "muted" : "warn"}">${esc(instance.lifecycle_state || "UNKNOWN")}</span><small>${esc(instance.region || account.region)}</small></td><td><strong>${esc(instance.shape || "—")}</strong><small>${instance.ocpus ?? "?"} OCPU · ${instance.memory_in_gbs ?? "?"} GB</small></td><td><strong>${esc(vnic?.public_ip || "无公网 IP")}</strong><small>${esc(vnic?.private_ip || "无私网 IP")}</small></td><td><div class="instance-actions"><button class="button primary-soft" data-instance-detail data-account-id="${account.id}" data-instance-id="${esc(instance.id)}" type="button">详情</button>
        ${stopped ? `<button class="button" data-instance-action="START" data-instance-id="${esc(instance.id)}" data-region="${esc(instance.region)}" type="button">启动</button>` : ""}
        ${running ? `<button class="button" data-instance-action="SOFTSTOP" data-instance-id="${esc(instance.id)}" data-region="${esc(instance.region)}" type="button">停止</button><button class="button" data-instance-action="SOFTRESET" data-instance-id="${esc(instance.id)}" data-region="${esc(instance.region)}" type="button">重启</button>` : ""}
        ${vnic?.private_ip_id ? `<button class="button danger" data-replace-ip="${esc(vnic.private_ip_id)}" data-region="${esc(instance.region)}" type="button">更换 IP</button>` : ""}
        <button class="button" data-edit-instance="${esc(instance.id)}" data-region="${esc(instance.region)}" type="button">修改</button>
        <button class="button danger" data-terminate-instance="${esc(instance.id)}" data-region="${esc(instance.region)}" type="button">终止</button>
      </div></td></tr>`;
  }).join("")}</tbody></table></div></section>`;
}

async function instanceAction(button) {
  const account = selectedTenant();
  if (!account) return;
  const action = button.dataset.instanceAction;
  setBusy(button, true);
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(button.dataset.instanceId)}/actions`, {
      method: "POST",
      body: { action, region: button.dataset.region },
    });
    await loadTenantInstances(false);
    toast("实例操作已提交", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally { setBusy(button, false); }
}

async function replaceIp(button) {
  const account = selectedTenant();
  if (!account || !await openConfirmDialog({ title: "更换公网 IP", message: "将释放当前临时公网 IP 并申请新 IP，旧 IP 无法恢复。", submitText: "确认更换", danger: true })) return;
  setBusy(button, true, "更换中……");
  try {
    const result = await api(`/accounts/${account.id}/public-ips/${encodeURIComponent(button.dataset.replaceIp)}/replace`, {
      method: "POST", body: { region: button.dataset.region },
    });
    await loadTenantInstances(false);
    toast(`新公网 IP：${result.new_ip || "正在分配"}`, "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function editInstance(button) {
  const account = selectedTenant();
  const instance = state.instances.find((item) => item.id === button.dataset.editInstance);
  if (!account || !instance) return;
  const values = await openUtilityDialog({
    title: "修改实例",
    copy: `${instance.display_name || "N&T"} · ${instance.shape || "未知配置"}`,
    fields: [
      { name: "display_name", label: "实例名称", value: instance.display_name || "N&T", required: true },
      { name: "note", label: "实例备注", value: instance.freeform_tags?.["OCI-N&T-Note"] || "", placeholder: "留空表示清除备注" },
      { name: "ocpus", label: "OCPU", type: "number", value: instance.ocpus ?? "", min: 1, step: 1, help: "留空表示不修改" },
      { name: "memory_in_gbs", label: "内存 GB", type: "number", value: instance.memory_in_gbs ?? "", min: 1, step: 1, help: "留空表示不修改" },
    ],
    submitText: "保存修改",
    validate: (data) => !String(data.display_name || "").trim() ? "实例名称不能为空" : "",
  });
  if (!values) return;
  const body = {
    region: button.dataset.region,
    display_name: values.display_name.trim() || "N&T",
    note: String(values.note || "").trim(),
    ocpus: values.ocpus,
    memory_in_gbs: values.memory_in_gbs,
  };
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/details`, { method: "PUT", body });
    await loadTenantInstances(false);
    toast("实例配置已提交", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function terminateInstance(button) {
  const account = selectedTenant();
  if (!account) return;
  const confirmed = await openConfirmDialog({
    title: "终止实例",
    copy: "此操作不可撤销，引导卷将按当前保留策略处理。",
    message: "终止后实例将无法恢复。",
    confirmationText: "TERMINATE",
    submitText: "永久终止",
    danger: true,
  });
  if (!confirmed) return;
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(button.dataset.terminateInstance)}/terminate`, {
      method: "POST",
      body: { region: button.dataset.region, preserve_boot_volume: true, confirmation: "TERMINATE" },
    });
    await loadTenantInstances(false);
    toast("实例终止请求已提交");
  } catch (error) { toast(error.message, "bad"); }
}

function renderTenantAccountSettings() {
  const account = selectedTenant();
  if (!account) return;
  $("tenant-content").innerHTML = `<div class="account-settings-grid">
    <section class="panel"><div class="panel-head"><div><h2>账户检测</h2><p>只检测当前账户，并发送完整邮箱通知。</p></div></div><p>上次检测：${fmtDate(account.last_checked_at)}</p><button class="button primary" data-check-account="${account.id}" type="button">立即检测</button></section>
    <section class="panel"><div class="panel-head"><div><h2>独立代理</h2><p>${account.proxy_enabled ? "已启用" : "未启用"} · ${esc(account.proxy_label || "未命名")}</p></div></div><p>${esc(account.proxy_last_ip || "尚未测试出口 IP")}</p><div class="inline-actions"><button class="button" data-config-proxy="${account.id}" type="button">配置代理</button><button class="button" data-test-proxy="${account.id}" type="button">测试代理</button></div></section>
    <section class="panel"><div class="panel-head"><div><h2>本地缓存</h2><p>查看页面不会刷新 OCI。</p></div></div><p>账户资料上次查询：${fmtDate(account.last_checked_at)}</p><p>实例资料只在“从 OCI 同步当前租户”时更新。</p></section>
    <section class="panel"><div class="panel-head"><div><h2>删除账户</h2><p>删除本地凭据、缓存及关联记录。</p></div></div><button class="button danger" data-delete-account="${account.id}" type="button">删除当前 OCI 账户</button></section>
  </div>`;
}

function proxyMode() {
  return $("proxy-mode-new")?.checked ? "new" : "existing";
}

function populateProxyProfileSelect(preferredId = null) {
  const select = $("proxy-profile-select");
  if (!select) return;
  const previous = preferredId ?? select.value;
  const choices = Array.isArray(state.proxyBindingChoices)
    ? state.proxyBindingChoices
    : state.proxyProfiles.filter((profile) => profile.is_enabled !== false && !Number(profile.assigned_count || 0));
  select.innerHTML = choices.length
    ? choices.map((profile) => `<option value="${profile.id}">${esc(profile.name)} · ${esc(profile.scheme.toUpperCase())} · ${esc(profile.host)}:${profile.port}${profile.assigned_account_id ? " · 当前代理" : ""}</option>`).join("")
    : '<option value="">暂无未分配代理</option>';
  if (choices.some((profile) => String(profile.id) === String(previous))) {
    select.value = String(previous);
  } else {
    select.value = String(choices[0]?.id || "");
  }
}

async function loadProxyProfiles() {
  state.proxyProfiles = await api("/proxies");
  populateProxyProfileSelect();
  populateImportProxyProfileSelect();
  renderProxyManagement();
  return state.proxyProfiles;
}

function setProxyEditorState() {
  const enabled = $("proxy-enabled").checked;
  const mode = proxyMode();
  $("proxy-existing-section").hidden = mode !== "existing";
  $("proxy-new-section").hidden = mode !== "new";
  $("proxy-profile-select").disabled = !enabled || mode !== "existing" || !state.proxyProfiles.length;
  setManualProxyDisabled("proxy", !enabled || mode !== "new", true);
  $("proxy-test-unsaved").disabled = !enabled || (mode === "existing" && !$("proxy-profile-select").value);
}

async function configureProxy(accountId) {
  const account = state.accounts.find((item) => item.id === Number(accountId));
  const errorBox = $("proxy-form-error");
  const testBox = $("proxy-test-result");
  errorBox.hidden = true;
  testBox.hidden = true;
  try {
    const [current, choices] = await Promise.all([
      api(`/accounts/${accountId}/proxy`),
      api(`/accounts/${accountId}/available-proxies`),
      loadProxyProfiles(),
    ]);
    state.proxyBindingChoices = choices;
    $("proxy-account-id").value = String(accountId);
    $("proxy-dialog-copy").textContent = `${account?.custom_name || "当前租户"} · 选择代理库或新增代理`;
    $("proxy-enabled").checked = Boolean(current.enabled);
    clearManualProxyFields("proxy");
    $("proxy-label").value = "";
    $("proxy-current-url").textContent = current.proxy_profile_name || current.proxy_label || (current.enabled ? "已启用" : "未启用");
    $("proxy-saved-copy").textContent = current.display_url || "尚未绑定代理";
    $("proxy-last-ip").textContent = current.last_ip || "尚未测试";
    $("proxy-last-test").textContent = current.last_test_at ? fmtDate(current.last_test_at) : "从未测试";
    if (current.proxy_profile_id && choices.some((item) => Number(item.id) === Number(current.proxy_profile_id))) {
      $("proxy-mode-existing").checked = true;
      populateProxyProfileSelect(current.proxy_profile_id);
    } else if (choices.length) {
      $("proxy-mode-existing").checked = true;
      populateProxyProfileSelect();
    } else {
      $("proxy-mode-new").checked = true;
    }
    setProxyEditorState();
    $("proxy-dialog").showModal();
  } catch (error) { toast(error.message, "bad"); }
}

async function createProxyFromManual(prefix = "proxy") {
  const name = String($(`${prefix}-label`)?.value || "").trim();
  if (!name) throw new Error("请填写代理名称");
  const proxyUrl = buildManualProxyUrl(prefix);
  return api("/proxies", { method: "POST", body: { name, proxy_url: proxyUrl } });
}

async function saveProxyForm(event) {
  event.preventDefault();
  const accountId = Number($("proxy-account-id").value);
  const enabled = $("proxy-enabled").checked;
  const errorBox = $("proxy-form-error");
  errorBox.hidden = true;
  setBusy($("proxy-save"), true, "保存中……");
  try {
    let profileId = null;
    if (enabled) {
      if (proxyMode() === "existing") {
        profileId = Number($("proxy-profile-select").value);
        if (!profileId) throw new Error("请选择已保存代理，或切换为新增代理");
      } else {
        const profile = await createProxyFromManual("proxy");
        profileId = Number(profile.id);
      }
    } else {
      const current = await api(`/accounts/${accountId}/proxy`);
      profileId = current.proxy_profile_id || null;
    }
    await api(`/accounts/${accountId}/proxy`, {
      method: "PUT",
      body: { enabled, proxy_profile_id: profileId, clear_proxy: false },
    });
    $("proxy-dialog").close();
    state.proxyBindingChoices = null;
    await Promise.all([loadAccounts(), loadProxyProfiles()]);
    if (state.tenantId === accountId && state.tenantTab === "account") window.renderTenantAccountSettings();
    toast(enabled ? "租户代理已保存" : "租户代理已停用", "good");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally { setBusy($("proxy-save"), false); }
}

async function clearProxyForm() {
  const accountId = Number($("proxy-account-id").value);
  if (!accountId || !confirm("确定取消该租户与代理的绑定吗？代理库中的代理不会被删除。")) return;
  setBusy($("proxy-clear"), true, "处理中……");
  try {
    await api(`/accounts/${accountId}/proxy`, {
      method: "PUT",
      body: { enabled: false, proxy_profile_id: null, clear_proxy: true },
    });
    $("proxy-dialog").close();
    state.proxyBindingChoices = null;
    await loadAccounts();
    if (state.tenantId === accountId && state.tenantTab === "account") window.renderTenantAccountSettings();
    toast("已取消租户代理绑定", "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy($("proxy-clear"), false); }
}

async function testProxyDialogAddress() {
  const resultBox = $("proxy-test-result");
  $("proxy-form-error").hidden = true;
  resultBox.hidden = false;
  setBusy($("proxy-test-unsaved"), true, "测试中……");
  try {
    let result;
    if (proxyMode() === "existing") {
      const profileId = Number($("proxy-profile-select").value);
      if (!profileId) throw new Error("请选择代理");
      result = await api(`/proxies/${profileId}/test`, { method: "POST" });
      await loadProxyProfiles();
    } else {
      const url = buildManualProxyUrl("proxy");
      result = await api("/proxy/test", { method: "POST", body: { proxy_url: url } });
    }
    resultBox.textContent = result.message;
    resultBox.className = "proxy-inline-result good-text";
    $("proxy-last-ip").textContent = result.ip_address;
    $("proxy-last-test").textContent = proxyMode() === "existing" ? "刚刚" : "刚刚（尚未保存）";
  } catch (error) {
    resultBox.textContent = error.message;
    resultBox.className = "proxy-inline-result bad-text";
  } finally { setBusy($("proxy-test-unsaved"), false); }
}

async function testProxy(accountId) {
  try {
    const result = await api(`/accounts/${accountId}/proxy/test`, { method: "POST" });
    toast(result.message, "good");
    await Promise.all([loadAccounts(), loadProxyProfiles()]);
    if (state.tenantId === Number(accountId) && state.tenantTab === "account") window.renderTenantAccountSettings();
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteAccount(accountId) {
  const account = state.accounts.find((item) => item.id === Number(accountId));
  if (!confirm(`确定删除“${account?.custom_name || accountId}”吗？`)) return;
  try {
    await api(`/accounts/${accountId}`, { method: "DELETE" });
    state.tenantId = null;
    await loadAccounts();
    showPage("accounts");
    toast("OCI 账户已删除");
  } catch (error) { toast(error.message, "bad"); }
}

async function openProxyTextImportDialog() {
  const values = await openUtilityDialog({
    title: "批量导入代理",
    copy: "每行一个代理，系统会自动识别常见格式；最多 500 个。",
    fields: [
      {
        name: "text",
        label: "代理内容",
        type: "textarea",
        rows: 12,
        required: true,
        full: true,
        placeholder: [
          "socks5://user:pass@host:1080",
          "host:port",
          "host:port:user:pass",
          "user:pass@host:port",
          "名称|代理地址|更换IP API",
          "名称|协议|服务器|端口|用户名|密码|更换IP API|GET|3",
        ].join("\n"),
        help: "还支持逗号、竖线、Tab、分号、JSON、JSON 数组和 JSONL；JSON 可包含 rotate_api_url、rotate_api_method、rotate_wait_seconds。",
      },
      { name: "default_scheme", label: "无协议时默认", type: "select", value: "socks5", options: ["socks5", "socks5h", "http", "https"] },
      { name: "name_prefix", label: "自动名称前缀", value: "代理", required: true },
      { name: "skip_existing", label: "跳过已存在的相同代理", type: "checkbox", value: true, full: true, help: "按完整代理地址（含账号密码）判断，不会把重复代理再次写入数据库。" },
    ],
    submitText: "开始导入",
  });
  if (!values) return;
  try {
    const result = await api("/proxies/import", {
      method: "POST",
      body: {
        text: String(values.text || ""),
        default_scheme: values.default_scheme,
        name_prefix: String(values.name_prefix || "代理").trim(),
        skip_existing: Boolean(values.skip_existing),
      },
    });
    await loadProxyProfiles();
    await loadAccounts();
    const failures = (result.items || []).filter((item) => item.status === "FAILED");
    const summary = `导入完成：新增 ${result.created} 个，跳过 ${result.skipped} 个，失败 ${result.failed} 个。`;
    toast(summary, failures.length ? "bad" : "good");
    if (failures.length) {
      await openUtilityDialog({
        title: "部分代理导入失败",
        message: `${summary}\n\n${failures.slice(0, 30).map((item) => `第 ${item.line_number} 行 ${item.name}：${item.message}`).join("\n")}`,
        preformatted: true,
        messageOnly: true,
      });
    }
  } catch (error) {
    toast(error.message, "bad");
  }
}


function proxyProfileSearchText(profile) {
  return [profile.name, profile.scheme, profile.host, profile.port, profile.last_ip]
    .filter(Boolean).join(" ").toLowerCase();
}

function renderProxyManagement() {
  const box = $("proxy-account-list");
  if (!box) return;
  const status = $("proxy-status-filter")?.value || "";
  const query = String($("proxy-search")?.value || "").trim().toLowerCase();
  const visible = state.proxyProfiles.filter((profile) => {
    const available = Boolean(profile.last_success_at && !profile.last_error);
    const abnormal = Boolean(profile.last_error || Number(profile.consecutive_failures || 0) > 0);
    const assigned = Number(profile.assigned_count || 0) > 0;
    if (status === "idle" && assigned) return false;
    if (status === "assigned" && !assigned) return false;
    if (status === "disabled" && profile.is_enabled !== false) return false;
    if (status === "available" && (!available || profile.is_enabled === false)) return false;
    if (status === "abnormal" && !abnormal) return false;
    if (status === "untested" && profile.last_test_at) return false;
    if (status === "rotate" && !profile.has_rotate_api) return false;
    return !query || proxyProfileSearchText(profile).includes(query);
  });
  const tested = state.proxyProfiles.filter((profile) => profile.last_test_at).length;
  if ($("proxy-filter-count")) $("proxy-filter-count").textContent = `${visible.length} / ${state.proxyProfiles.length} 个代理`;
  if ($("proxy-enabled-count")) $("proxy-enabled-count").textContent = `${tested} 个已测试`;
  if (!visible.length) {
    box.innerHTML = `<div class="empty">${state.proxyProfiles.length ? "没有匹配代理" : "暂无代理，请点击右上角“添加代理”"}</div>`;
    return;
  }
  box.innerHTML = `<table class="proxy-account-table proxy-library-table"><thead><tr><th>序号</th><th>代理名称</th><th>协议</th><th>服务器</th><th>出口 IP</th><th>地区 / 延迟</th><th>状态</th><th>操作</th></tr></thead><tbody>${visible.map((profile, index) => {
    const testedOk = profile.last_success_at && !profile.last_error;
    const abnormal = profile.last_error || Number(profile.consecutive_failures || 0) > 0;
    const statusText = profile.is_enabled === false ? "已停用" : abnormal ? `异常 ${profile.consecutive_failures || 1} 次` : testedOk ? "可用" : "未测试";
    const statusClass = profile.is_enabled === false ? "muted" : abnormal ? "bad" : testedOk ? "good" : "muted";
    const rotateCopy = profile.has_rotate_api
      ? `换 IP API · ${profile.rotate_api_method || "GET"} · 等待 ${profile.rotate_wait_seconds ?? 3} 秒`
      : "未配置换 IP API";
    const rotateButton = profile.has_rotate_api
      ? `<button class="button small" data-rotate-profile="${profile.id}" type="button" title="${esc(profile.last_rotate_error || rotateCopy)}">换 IP</button>`
      : "";
    const location = [profile.last_country_name || profile.last_country_code, profile.last_region_name, profile.last_city].filter(Boolean).join(" / ") || "尚未识别";
    const latency = profile.last_latency_ms != null ? `${profile.last_latency_ms} ms` : "—";
    return `<tr>
      <td class="sequence-cell"><span>${index + 1}</span></td>
      <td><strong>${esc(profile.name)}</strong><small>${profile.assigned_account_name ? `已分配 · ${esc(profile.assigned_account_name)}` : "未分配"} · ${profile.has_auth ? "需要认证" : "无认证"}</small></td>
      <td><span class="badge muted">${esc(profile.scheme.toUpperCase())}</span></td>
      <td><strong class="mono">${esc(profile.host)}:${profile.port}</strong><small>${esc(profile.display_url || "凭据已加密")}</small></td>
      <td><code>${esc(profile.last_ip || "尚未测试")}</code><small>${profile.last_success_at ? `成功于 ${fmtDate(profile.last_success_at)}` : profile.last_test_at ? `检测于 ${fmtDate(profile.last_test_at)}` : "从未测试"}</small></td>
      <td><strong>${esc(location)}</strong><small>${esc(latency)}</small></td>
      <td><span class="badge ${statusClass}" title="${esc(profile.last_error || "")}">${statusText}</span></td>
      <td><div class="row-actions compact proxy-row-actions">${rotateButton}<button class="button small" data-test-profile="${profile.id}" type="button">测试</button><button class="button small primary" data-edit-profile="${profile.id}" type="button">编辑</button><button class="button small danger ghost" data-delete-profile="${profile.id}" type="button">删除</button></div></td>
    </tr>`;
  }).join("")}</tbody></table>`;
}

async function openProxyProfileEditor(profileId = null) {
  const summary = profileId ? state.proxyProfiles.find((item) => Number(item.id) === Number(profileId)) : null;
  let profile = summary;
  if (profileId) {
    try {
      profile = await api(`/proxies/${profileId}`);
    } catch (error) {
      toast(error.message, "bad");
      return;
    }
  }
  const rotationFields = [
    {
      name: "rotate_api_url",
      label: profile?.has_rotate_api ? "新更换 IP API（可选）" : "更换 IP API（可选）",
      value: "",
      placeholder: "https://provider.example/change-ip?token=...",
      full: true,
      help: profile?.has_rotate_api ? "留空保留现有加密 API；不会在页面回显密钥。" : "调用后等待指定秒数，再自动测试并记录新的出口 IP。",
    },
    { name: "rotate_api_method", label: "请求方法", type: "select", value: profile?.rotate_api_method || "GET", options: ["GET", "POST"] },
    { name: "rotate_wait_seconds", label: "更换后等待秒数", type: "number", value: profile?.rotate_wait_seconds ?? 3, min: 0, max: 60 },
    { name: "rotate_api_headers", label: "请求头 JSON（可选）", type: "textarea", rows: 3, value: "", placeholder: '{"Authorization":"Bearer ..."}', full: true, help: profile?.has_rotate_api ? "留空保留现有请求头；输入 {} 可清空。" : "请求头和密钥会加密写入 SQLite。" },
    { name: "rotate_api_body", label: "POST 请求体（可选）", type: "textarea", rows: 3, value: "", placeholder: '{"action":"rotate"}', full: true, help: profile?.has_rotate_api ? "留空保留现有请求体。" : "GET 请求不会发送请求体。" },
  ];
  const proxyFields = [
    { name: "name", label: "代理名称", value: profile?.name || "", required: true, full: true },
    { name: "is_enabled", label: "允许分配和使用", type: "checkbox", value: profile?.is_enabled !== false, full: true, help: "停用后不会参与自动分配，也不能手动绑定给新租户。" },
    { name: "scheme", label: "协议", type: "select", value: profile?.scheme || "socks5", options: ["socks5", "socks5h", "http", "https"] },
    { name: "host", label: "服务器", value: profile?.host || "", required: true },
    { name: "port", label: "端口", type: "number", value: profile?.port ?? 1080, min: 1, max: 65535, required: true },
    { name: "username", label: "用户名（可选）", value: profile?.username || "" },
    { name: "password", label: "密码（可选）", type: "password", value: "", help: profile?.has_auth ? "留空保留当前密码；新输入内容会替换旧密码。" : "不需要认证时保持为空。" },
  ];
  if (profile?.has_auth) {
    proxyFields.push({ name: "clear_auth", label: "清除用户名和密码", type: "checkbox", value: false, full: true, help: "仅在不再需要代理认证时勾选。" });
  }
  const fields = [
    ...proxyFields,
    ...rotationFields,
    ...(profile?.has_rotate_api ? [{ name: "clear_rotate_api", label: "清除更换 IP API", type: "checkbox", value: false, full: true, help: "勾选后删除该代理保存的换 IP API、请求头和请求体。" }] : []),
  ];
  const values = await openUtilityDialog({
    title: profile ? "编辑代理" : "添加代理",
    copy: profile ? "可直接修改协议、服务器、端口和用户名；密码留空时保留原密码。" : "代理、账号密码及换 IP API 均加密保存到 SQLite。",
    fields,
    submitText: "保存代理",
    validate: (formValues) => {
      if (!String(formValues.host || "").trim()) return "请填写服务器地址。";
      if (!formValues.clear_auth && formValues.password && !String(formValues.username || "").trim()) return "填写密码时必须同时填写用户名。";
      if (profile?.has_auth && !formValues.clear_auth && !String(formValues.username || "").trim()) return "如需清除认证，请勾选“清除用户名和密码”。";
      const headers = String(formValues.rotate_api_headers || "").trim();
      if (headers) {
        try {
          const parsed = JSON.parse(headers);
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return "请求头必须是 JSON 对象。";
        } catch (error) { return `请求头 JSON 格式错误：${error.message}`; }
      }
      return "";
    },
  });
  if (!values) return;
  try {
    const rotateUrl = String(values.rotate_api_url || "").trim();
    const headersText = String(values.rotate_api_headers || "").trim();
    const bodyText = String(values.rotate_api_body || "");
    const rotatePayload = {};
    if (rotateUrl) rotatePayload.rotate_api_url = rotateUrl;
    if (rotateUrl || profile?.has_rotate_api) {
      rotatePayload.rotate_api_method = values.rotate_api_method || "GET";
      rotatePayload.rotate_wait_seconds = Number(values.rotate_wait_seconds ?? 3);
      if (headersText) rotatePayload.rotate_api_headers = JSON.parse(headersText);
      if (bodyText.trim()) rotatePayload.rotate_api_body = bodyText;
    }
    const hostRaw = String(values.host || "").trim().replace(/^\[|\]$/g, "");
    if (profile) {
      const body = {
        name: String(values.name || "").trim(),
        is_enabled: Boolean(values.is_enabled),
        scheme: values.scheme,
        host: hostRaw,
        port: Number(values.port),
        username: String(values.username || "").trim(),
        password: String(values.password || ""),
      };
      if (values.clear_auth) body.clear_auth = true;
      if (values.clear_rotate_api) body.clear_rotate_api = true;
      else Object.assign(body, rotatePayload);
      await api(`/proxies/${profile.id}`, { method: "PUT", body });
    } else {
      const host = hostRaw.includes(":") ? `[${hostRaw}]` : hostRaw;
      if (values.password && !values.username) throw new Error("填写密码时必须同时填写用户名");
      const auth = values.username ? `${encodeURIComponent(values.username)}${values.password ? `:${encodeURIComponent(values.password)}` : ""}@` : "";
      const proxyUrl = `${values.scheme}://${auth}${host}:${values.port}`;
      await api("/proxies", {
        method: "POST",
        body: {
          name: String(values.name || "").trim(),
          proxy_url: proxyUrl,
          ...rotatePayload,
        },
      });
    }
    await loadProxyProfiles();
    await loadAccounts();
    toast(profile ? "代理已更新" : "代理已添加", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function testProxyProfile(profileId) {
  try {
    const result = await api(`/proxies/${profileId}/test`, { method: "POST" });
    toast(result.message, "good");
    await Promise.all([loadProxyProfiles(), loadAccounts()]);
  } catch (error) {
    toast(error.message, "bad");
    await loadProxyProfiles().catch(() => {});
  }
}

async function rotateProxyProfile(profileId, button = null) {
  const profile = state.proxyProfiles.find((item) => Number(item.id) === Number(profileId));
  if (!profile?.has_rotate_api) {
    toast("该代理尚未配置更换 IP API", "bad");
    return;
  }
  const oldIp = profile.last_ip || "尚未测试";
  if (!confirm(`确定调用“${profile.name}”的更换 IP API 吗？\n当前出口 IP：${oldIp}`)) return;
  setBusy(button, true, "更换中……");
  try {
    const result = await api(`/proxies/${profileId}/rotate`, { method: "POST" });
    toast(result.message, result.changed ? "good" : "");
    await Promise.all([loadProxyProfiles(), loadAccounts()]);
  } catch (error) {
    toast(error.message, "bad");
    await loadProxyProfiles().catch(() => {});
  } finally {
    setBusy(button, false);
  }
}


async function deleteProxyProfile(profileId) {
  const profile = state.proxyProfiles.find((item) => Number(item.id) === Number(profileId));
  const copy = profile?.assigned_count
    ? `该代理当前被 ${profile.assigned_count} 个租户使用，删除后这些租户将自动停用代理。`
    : "删除后无法恢复。";
  if (!confirm(`确定删除代理“${profile?.name || profileId}”吗？\n${copy}`)) return;
  try {
    const result = await api(`/proxies/${profileId}`, { method: "DELETE" });
    await Promise.all([loadProxyProfiles(), loadAccounts()]);
    toast(result.detached_accounts ? `代理已删除，并停用 ${result.detached_accounts} 个租户` : "代理已删除", "good");
  } catch (error) { toast(error.message, "bad"); }
}

function prepareProxyManagementPage() {
  loadProxyProfiles().catch((error) => {
    $("proxy-account-list").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  });
}


async function autoAllocateProxies(button = null) {
  setBusy(button, true, "检查中……");
  try {
    const preview = await api("/proxy-allocation/preview", {
      method: "POST", body: { account_ids: null, reassign: false },
    });
    if (!preview.tenant_count) return toast("所有租户都已经分配代理", "good");
    if (!preview.available_proxy_count) return toast("没有健康且未分配的代理，请先检测代理", "bad");
    const message = `未分配租户 ${preview.tenant_count} 个，可用代理 ${preview.available_proxy_count} 个。\n本次可分配 ${preview.assignable_count} 个，剩余 ${preview.unassigned_count} 个保持未分配。`;
    if (!confirm(`${message}\n\n确定开始自动分配吗？`)) return;
    setBusy(button, true, "分配中……");
    const result = await api("/proxy-allocation/execute", {
      method: "POST", body: { account_ids: null, reassign: false },
    });
    await Promise.all([loadAccounts(), loadProxyProfiles()]);
    toast(`自动分配完成：成功 ${result.assigned} 个，未分配 ${result.unassigned} 个`, result.unassigned ? "" : "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

function openBatchProxyDialog() {
  showPage("proxies");
}


function taskTypeLabel(type) {
  const labels = {
    ACCOUNT_CHECK: "账户检测",
    PROXY_HEALTH: "代理检测",
    INSTANCE_BATCH: "实例批量操作",
  };
  return labels[type] || type || "其他任务";
}

function taskStatusMeta(status) {
  const map = {
    PENDING: ["等待开始", "muted"], RUNNING: ["运行中", "good"], WAITING: ["等待下一项", "good"],
    CANCELLING: ["正在取消", "warn"], COMPLETED: ["已完成", "good"], PARTIAL: ["部分失败", "warn"],
    FAILED: ["失败", "bad"], CANCELLED: ["已取消", "muted"], INTERRUPTED: ["已中断", "bad"],
  };
  return map[status] || [status || "未知", "muted"];
}

function taskSourceMeta(task) {
  const explicit = String(task?.options?.source || "").toLowerCase();
  const requestedBy = String(task?.requested_by || "");
  if (explicit === "scheduled" || requestedBy === "系统定时检测") return ["scheduled", "定时检测", "good"];
  if (explicit === "schedule_now" || requestedBy.includes("定时页立即检测")) return ["schedule_now", "立即检测", "warn"];
  return ["manual", "手动", "muted"];
}

function taskDuration(task) {
  const start = Date.parse(task?.started_at || task?.created_at || "");
  const end = task?.finished_at ? Date.parse(task.finished_at) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  let seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  seconds %= 60;
  if (minutes < 60) return `${minutes}分 ${seconds}秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours}小时 ${minutes % 60}分`;
}

function filteredTasks() {
  const type = $("task-type-filter")?.value || "";
  const status = $("task-status-filter")?.value || "";
  const source = $("task-source-filter")?.value || "";
  return state.tasks.filter((task) => {
    if (type && task.task_type !== type) return false;
    if (status === "active" && !task.is_active) return false;
    if (status && status !== "active" && task.status !== status) return false;
    if (source && taskSourceMeta(task)[0] !== source) return false;
    return true;
  });
}

function renderTaskCenter() {
  const tasks = filteredTasks();
  const total = state.tasks.length;
  const active = state.tasks.filter((task) => task.is_active).length;
  const completed = state.tasks.filter((task) => task.status === "COMPLETED").length;
  const partial = state.tasks.filter((task) => ["PARTIAL", "FAILED", "INTERRUPTED"].includes(task.status)).length;
  if ($("task-summary-active")) $("task-summary-active").textContent = active;
  if ($("task-summary-completed")) $("task-summary-completed").textContent = completed;
  if ($("task-summary-partial")) $("task-summary-partial").textContent = partial;
  if ($("task-summary-total")) $("task-summary-total").textContent = total;
  if ($("task-filter-count")) $("task-filter-count").textContent = `${tasks.length} / ${total} 条任务`;
  const box = $("task-list");
  if (!box) return;
  if (!tasks.length) {
    box.innerHTML = `<div class="empty">${total ? "没有匹配任务" : "暂无任务"}</div>`;
    return;
  }
  box.innerHTML = `<table class="task-table"><thead><tr><th>编号</th><th>任务</th><th>来源</th><th>状态</th><th>进度</th><th>成功 / 失败 / 中断</th><th>当前项目</th><th>耗时</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${tasks.map((task) => {
    const [label, tone] = taskStatusMeta(task.status);
    const [, sourceLabel, sourceTone] = taskSourceMeta(task);
    const current = task.current_item_name || (task.next_item_at ? `等待至 ${fmtDate(task.next_item_at)}` : "—");
    const retry = task.can_retry ? `<button class="button small" data-task-retry="${task.id}" type="button">重试失败/中断项</button>` : "";
    const cancel = task.can_cancel ? `<button class="button small danger ghost" data-task-cancel="${task.id}" type="button">取消</button>` : "";
    return `<tr><td><strong>#${task.id}</strong></td><td><strong>${esc(task.title)}</strong><small>${esc(taskTypeLabel(task.task_type))}</small></td><td><span class="badge ${sourceTone}">${esc(sourceLabel)}</span></td><td><span class="badge ${tone}">${esc(label)}</span></td><td><div class="task-progress-cell"><span>${task.completed}/${task.total}</span><div class="progress-track"><i style="width:${Math.min(100, Number(task.progress_percent || 0))}%"></i></div></div></td><td><strong>${task.succeeded} / ${task.failed} / ${Number(task.interrupted || 0)}</strong><small>${task.skipped ? `跳过 ${task.skipped}` : ""}</small></td><td><span title="${esc(current)}">${esc(current)}</span></td><td><strong>${esc(taskDuration(task))}</strong></td><td><time>${esc(fmtDate(task.created_at).replace(",", ""))}</time></td><td><div class="row-actions compact"><button class="button small primary" data-task-detail="${task.id}" type="button">查看</button>${retry}${cancel}</div></td></tr>`;
  }).join("")}</tbody></table>`;
}

function taskItemResultCopy(item) {
  const result = item?.result || {};
  if (result.ip_address) return `${result.ip_address}${result.latency_ms ? ` · ${result.latency_ms}ms` : ""}`;
  if (result.new_ip) return `新 IP：${result.new_ip}${result.old_ip ? ` · 原 ${result.old_ip}` : ""}`;
  if (Number.isFinite(Number(result.instances))) return `同步 ${result.instances} 台 · ${result.regions_scanned || 0} 个区域`;
  if (result.lifecycle_state) {
    const labels = { STARTING: "正在启动", STOPPING: "正在停止", REBOOTING: "正在重启" };
    return labels[result.lifecycle_state] || result.lifecycle_state;
  }
  if (result.account_status) return result.account_status;
  if (result.operation) return result.operation;
  return "—";
}

function renderTaskDetail(task) {
  state.taskDetail = task;
  const panel = $("task-detail-panel");
  if (!panel) return;
  panel.hidden = false;
  const [label, tone] = taskStatusMeta(task.status);
  $("task-detail-title").textContent = `任务 #${task.id} · ${task.title}`;
  $("task-detail-copy").textContent = `${taskTypeLabel(task.task_type)} · ${label} · 创建于 ${fmtDate(task.created_at)}`;
  if ($("task-safe-resume")) {
    $("task-safe-resume").hidden = !(task.status === "INTERRUPTED" && Number(task.interrupted || 0) > 0);
    $("task-safe-resume").dataset.taskId = task.id;
  }
  const categorySummary = Object.entries(task.error_categories || {})
    .map(([category, count]) => `<span class="badge muted">${esc(taskErrorCategoryLabel(category))} ${Number(count)}</span>`)
    .join("");
  $("task-detail-summary").innerHTML = `<span class="badge ${tone}">${esc(label)}</span><strong>进度 ${task.completed}/${task.total}</strong><span>成功 ${task.succeeded}</span><span>失败 ${task.failed}</span><span>中断 ${Number(task.interrupted || 0)}</span><span>跳过 ${task.skipped}</span>${categorySummary}${task.error ? `<em>${esc(task.error)}</em>` : ""}${(Number(task.failed || 0) + Number(task.interrupted || 0)) ? `<button class="button small" data-task-copy-failures="${task.id}" type="button">复制失败/中断原因</button>` : ""}`;
  const items = task.items || [];
  $("task-detail-items").innerHTML = items.length ? `<table class="task-items-table"><thead><tr><th>序号</th><th>项目</th><th>状态</th><th>错误分类</th><th>结果</th><th>失败原因</th><th>完成时间</th></tr></thead><tbody>${items.map((item, index) => {
    const itemStatus = item.status === "SUCCEEDED" ? ["成功", "good"] : item.status === "FAILED" ? ["失败", "bad"] : item.status === "INTERRUPTED" ? ["已中断", "warn"] : item.status === "RUNNING" ? ["执行中", "good"] : [item.status || "等待", "muted"];
    const result = item.result || {};
    const resultCopy = taskItemResultCopy(item);
    const category = item.error_category ? `<span class="badge muted task-error-category">${esc(taskErrorCategoryLabel(item.error_category))}</span>${item.retryable ? '<small class="task-retry-copy">可重试</small>' : ""}` : "—";
    const retryCopy = item.next_retry_at ? `<small class="task-retry-copy">下次重试：${esc(fmtDate(item.next_retry_at))}</small>` : "";
    return `<tr><td>${index + 1}</td><td><strong>${esc(item.item_name)}</strong><small>${esc(item.item_key)}</small></td><td><span class="badge ${itemStatus[1]}">${esc(itemStatus[0])}</span><small class="task-retry-copy">尝试 ${Number(item.attempt || 0)} 次</small></td><td>${category}</td><td>${esc(resultCopy)}${retryCopy}</td><td class="task-error-cell">${esc(item.error || result.last_error || "—")}</td><td>${esc(item.finished_at ? fmtDate(item.finished_at).replace(",", "") : "—")}</td></tr>`;
  }).join("")}</tbody></table>` : '<div class="empty">该任务没有项目</div>';
}


function taskExportPayload(task) {
  return {
    exported_at: new Date().toISOString(),
    system_version: "N&T 2.0",
    task: {
      id: task.id,
      task_type: task.task_type,
      title: task.title,
      status: task.status,
      requested_by: task.requested_by,
      total: task.total,
      completed: task.completed,
      succeeded: task.succeeded,
      failed: task.failed,
      interrupted: Number(task.interrupted || 0),
      skipped: task.skipped,
      options: task.options || {},
      summary: task.summary || {},
      error: task.error || null,
      created_at: task.created_at,
      started_at: task.started_at,
      finished_at: task.finished_at,
    },
    items: task.items || [],
  };
}

function exportTaskJson() {
  const task = state.taskDetail;
  if (!task) return toast("请先打开任务详情", "bad");
  downloadTextFile(
    `OCI-NT-task-${task.id}.json`,
    JSON.stringify(taskExportPayload(task), null, 2),
    "application/json;charset=utf-8",
  );
  toast(`任务 #${task.id} 已导出 JSON`, "good");
}

function exportTaskCsv() {
  const task = state.taskDetail;
  if (!task) return toast("请先打开任务详情", "bad");
  const rows = task.items || [];
  const lines = [["序号", "任务编号", "任务类型", "项目", "项目键", "状态", "错误分类", "可重试", "尝试次数", "下次重试", "结果", "失败原因", "开始时间", "完成时间"].map(auditCsvCell).join(",")];
  rows.forEach((item, index) => {
    lines.push([
      index + 1,
      task.id,
      taskTypeLabel(task.task_type),
      item.item_name,
      item.item_key,
      item.status,
      taskErrorCategoryLabel(item.error_category || ""),
      item.retryable ? "是" : "否",
      item.attempt,
      item.next_retry_at || "",
      JSON.stringify(item.result || {}),
      item.error || "",
      item.started_at || "",
      item.finished_at || "",
    ].map(auditCsvCell).join(","));
  });
  downloadTextFile(
    `OCI-NT-task-${task.id}.csv`,
    `\ufeff${lines.join("\r\n")}`,
    "text/csv;charset=utf-8",
  );
  toast(`任务 #${task.id} 已导出 CSV`, "good");
}

async function safeResumeCurrentTask(button = null) {
  const task = state.taskDetail;
  if (!task) return toast("请先打开任务详情", "bad");
  if (button) setBusy(button, true, "检查状态……");
  try {
    const preview = await api(`/tasks/${task.id}/safe-resume-preview`);
    const decisions = (preview.items || []).map((item, index) => {
      const labels = {
        RETRY: "需要补执行",
        ALREADY_SATISFIED: "目标状态已满足",
        CONFIRM_RETRY: "需要确认后补执行",
        MANUAL_ONLY: "仅允许人工处理",
        UNRESOLVED: "状态未确认",
      };
      return `${index + 1}. ${item.item_name} · ${labels[item.decision] || item.decision}\n   ${item.reason || ""}`;
    }).join("\n");
    if (preview.operation === "REPLACE_PUBLIC_IP" || preview.safe === false && !preview.requires_confirmation) {
      await window.openMessageDialog({
        title: "无法自动安全恢复",
        copy: "系统不会重复执行结果未知的高风险操作。",
        message: decisions || "请人工检查 OCI 当前状态后重新发起操作。",
        preformatted: true,
      });
      return;
    }
    const confirmationText = preview.requires_confirmation ? `RESUME ${task.id}` : null;
    const values = await window.openUtilityDialog({
      title: "安全恢复中断任务",
      copy: "系统已按实时 OCI 状态判断每个项目，仅补执行确实需要的项目。",
      message: decisions,
      preformatted: true,
      fields: confirmationText ? [{
        name: "confirmation",
        label: `请输入 ${confirmationText} 确认可能重复执行的重启项目`,
        placeholder: confirmationText,
        required: true,
        full: true,
      }] : [],
      submitText: "确认安全恢复",
      danger: Boolean(confirmationText),
      validate: confirmationText
        ? (payload) => payload.confirmation === confirmationText ? "" : `请输入 ${confirmationText}`
        : null,
    });
    if (!values) return;
    const result = await api(`/tasks/${task.id}/safe-resume`, {
      method: "POST",
      body: { confirmation: values.confirmation || null },
    });
    if (result.resumed && result.task) {
      toast(`已创建安全恢复任务 #${result.task.id}`, "good");
    } else {
      toast(result.message || "目标状态已经满足，无需补执行", "good");
    }
    await loadTasks(null, true);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    if (button) setBusy(button, false);
  }
}

async function loadTasks(button = null, silent = false) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    state.tasks = await api("/tasks?limit=100");
    renderTaskCenter();
    const hasActive = state.tasks.some((task) => task.is_active);
    if (state.currentPage === "tasks" && hasActive) startTaskPolling();
    else if (!hasActive) stopTaskPolling();
    if (state.taskDetail?.id) {
      const detail = await api(`/tasks/${state.taskDetail.id}`);
      renderTaskDetail(detail);
    }
  } catch (error) {
    if (!silent) toast(error.message, "bad");
    if ($("task-list")) $("task-list").innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  } finally {
    if (button) setBusy(button, false);
  }
}

function startTaskPolling() {
  if (state.taskTimer) return;
  state.taskTimer = setInterval(() => loadTasks(null, true), 2000);
}

function stopTaskPolling() {
  if (state.taskTimer) clearInterval(state.taskTimer);
  state.taskTimer = null;
}

async function openTaskDetail(taskId) {
  try {
    const task = await api(`/tasks/${taskId}`);
    renderTaskDetail(task);
    $("task-detail-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message, "bad"); }
}

async function cancelManualTask(taskId) {
  if (!confirm(`确定取消任务 #${taskId} 吗？当前项目结束后停止。`)) return;
  try {
    await api(`/tasks/${taskId}/cancel`, { method: "POST" });
    toast("已请求取消任务");
    await loadTasks();
  } catch (error) { toast(error.message, "bad"); }
}

async function retryManualTask(taskId) {
  try {
    const task = await api(`/tasks/${taskId}/retry`, { method: "POST" });
    toast(`失败或中断项目重试任务 #${task.id} 已启动`, "good");
    state.taskDetail = task;
    await loadTasks();
  } catch (error) { toast(error.message, "bad"); }
}

async function copyTaskFailures(taskId) {
  try {
    const task = state.taskDetail?.id === Number(taskId) ? state.taskDetail : await api(`/tasks/${taskId}`);
    const failures = (task.items || []).filter((item) => ["FAILED", "INTERRUPTED"].includes(item.status));
    const copy = failures.map((item, index) => `${index + 1}. [${taskErrorCategoryLabel(item.error_category || "UNKNOWN")}] ${item.item_name}: ${item.error || item.result?.last_error || "未知错误"}`).join("\n");
    if (!copy) return toast("该任务没有失败或中断项目", "bad");
    await navigator.clipboard.writeText(copy);
    toast("失败或中断原因已复制", "good");
  } catch (error) { toast(error.message, "bad"); }
}


async function cleanupTaskHistory(button) {
  if (!confirm("将删除 30 天前已结束的任务，并至少保留最新 100 条。运行中的任务不会被删除，确定继续吗？")) return;
  setBusy(button, true, "清理中……");
  try {
    const result = await api("/tasks/cleanup", {
      method: "POST",
      body: { older_than_days: 30, keep_latest: 100 },
    });
    toast(result.deleted ? `已清理 ${result.deleted} 条任务记录` : "没有需要清理的任务", "good");
    state.taskDetail = null;
    if ($("task-detail-panel")) $("task-detail-panel").hidden = true;
    await loadTasks();
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function startProxyHealthAll(button) {
  if (!state.proxyProfiles.length) return toast("代理库为空", "bad");
  if (!confirm(`将串行检测 ${state.proxyProfiles.length} 个代理并记录出口 IP、地区和延迟，确定继续吗？`)) return;
  setBusy(button, true, "启动中……");
  try {
    const task = await api("/tasks/proxy-health", { method: "POST", body: { profile_ids: null, idempotency_key: createIdempotencyKey("proxy-health") } });
    state.taskDetail = task;
    toast(task.deduplicated ? `相同代理检测任务已存在（#${task.id}）` : `代理检测任务 #${task.id} 已启动`, "good");
    showPage("tasks");
    await loadTasks();
    await openTaskDetail(task.id);
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function loadCloudflare() {
  try {
    const previous = Number(state.cfAccountId || 0);
    state.cfAccounts = await api("/cloudflare/accounts");
    if (previous && state.cfAccounts.some((account) => Number(account.id) === previous)) state.cfAccountId = previous;
    else state.cfAccountId = null;
    renderCloudflare();
    renderDnsRecords();
  } catch (error) { toast(error.message, "bad"); }
}

function updateCloudflareControls() {
  const hasAccount = Boolean(state.cfAccountId);
  const hasZone = Boolean($("cf-zone")?.value);
  if ($("dns-zone-sync")) $("dns-zone-sync").disabled = !hasAccount;
  if ($("dns-refresh")) $("dns-refresh").disabled = !hasAccount || !hasZone;
  if ($("dns-add")) $("dns-add").disabled = !hasAccount || !hasZone;
}

function renderCloudflare() {
  const accounts = state.cfAccounts || [];
  if ($("cf-account-visible-count")) $("cf-account-visible-count").textContent = `${accounts.length} 个账户`;
  updateCloudflareControls();

  if (!accounts.length) {
    $("cf-list").innerHTML = '<div class="empty cloudflare-empty"><strong>暂无 Cloudflare 账户</strong><span>点击右上角“添加 Cloudflare”保存 API Token。</span></div>';
    $("dns-title").textContent = "DNS 记录";
    $("dns-copy").textContent = "请先添加 Cloudflare 账户。";
    $("cf-zone").innerHTML = '<option value="">请先添加账户</option>';
    return;
  }

  $("cf-list").innerHTML = accounts.map((account) => {
    const selected = Number(account.id) === Number(state.cfAccountId);
    return `<article class="cloudflare-account-item ${selected ? "is-selected" : ""}">
      <button class="cloudflare-account-select-button" data-cf-open="${account.id}" type="button">
        <span class="cf-account-avatar">CF</span>
        <span><strong>${esc(account.name || "Cloudflare")}</strong><small>${esc(account.email || "未填写邮箱")}</small></span>
      </button>
      <div class="cloudflare-account-meta"><span class="badge ${account.last_checked_at ? "good" : "muted"}">${account.last_checked_at ? "已检测" : "未检测"}</span><small>${account.last_checked_at ? esc(fmtDate(account.last_checked_at)) : "尚未测试 Token"}</small></div>
      <div class="cloudflare-account-actions"><button class="button small" data-cf-test="${account.id}" type="button">测试</button><button class="button small danger ghost" data-cf-delete="${account.id}" type="button">删除</button></div>
    </article>`;
  }).join("");

  $("dns-title").textContent = "DNS 记录";
  if (!state.cfAccountId) $("dns-copy").textContent = "请在左侧选择 Cloudflare 账户。";
  else if (!state.cfZones.length) $("dns-copy").textContent = "正在等待域名同步。";
}

async function addCloudflare(event) {
  event.preventDefault();
  const errorBox = $("cf-error");
  const button = $("cf-submit");
  errorBox.hidden = true;
  setBusy(button, true, "验证中……");
  try {
    await api("/cloudflare/accounts", { method: "POST", body: {
      name: $("cf-name").value.trim(), api_token: $("cf-token").value.trim(), email: $("cf-email").value.trim() || null,
    }});
    $("cf-dialog").close();
    await loadCloudflare();
    toast("Cloudflare 账户已添加", "good");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

function renderDnsRecords() {
  const records = state.cfRecords || [];
  const type = $("dns-type-filter")?.value || "";
  const query = ($("dns-search")?.value || "").trim().toLowerCase();
  const visible = records.filter((record) => {
    if (type && String(record.type || "").toUpperCase() !== type) return false;
    if (!query) return true;
    return [record.type, record.name, record.content, record.comment, record.ttl, record.priority]
      .map((value) => String(value ?? "").toLowerCase()).join(" ").includes(query);
  });
  if ($("dns-visible-count")) $("dns-visible-count").textContent = `${visible.length} / ${records.length} 条`;
  updateCloudflareControls();

  if (!state.cfAccountId) {
    $("dns-list").innerHTML = '<div class="empty cloudflare-empty"><strong>尚未选择账户</strong><span>请在左侧选择 Cloudflare 账户。</span></div>';
    return;
  }
  if (!state.cfRecordsLoaded) {
    $("dns-list").innerHTML = '<div class="empty cloudflare-empty"><strong>尚未读取 DNS</strong><span>选择域名后会自动读取记录。</span></div>';
    return;
  }
  if (!records.length) {
    $("dns-list").innerHTML = '<div class="empty cloudflare-empty"><strong>暂无 DNS 记录</strong><span>当前域名没有记录，可直接添加。</span></div>';
    return;
  }
  if (!visible.length) {
    $("dns-list").innerHTML = '<div class="empty cloudflare-empty"><strong>没有匹配记录</strong><span>请调整类型或搜索内容。</span></div>';
    return;
  }

  $("dns-list").innerHTML = `<table class="dns-table dense-table">
    <thead><tr><th>类型</th><th>名称</th><th>内容</th><th>TTL</th><th>代理</th><th>操作</th></tr></thead>
    <tbody>${visible.map((record) => `<tr>
      <td><span class="dns-type-badge dns-type-${esc(String(record.type || "").toLowerCase())}">${esc(record.type || "—")}</span></td>
      <td><strong>${esc(record.name || "—")}</strong><small>${esc(record.comment || "无备注")}</small></td>
      <td class="dns-content-cell"><code title="${esc(record.content || "")}">${esc(record.content || "—")}</code>${record.priority !== null && record.priority !== undefined ? `<small>优先级 ${esc(record.priority)}</small>` : ""}</td>
      <td><strong>${record.ttl === 1 ? "自动" : esc(record.ttl)}</strong></td>
      <td><span class="badge ${record.proxied ? "good" : "muted"}">${record.proxied ? "已代理" : "仅 DNS"}</span></td>
      <td><div class="row-actions compact dns-row-actions"><button class="button small" data-dns-edit="${esc(record.id)}" type="button">编辑</button><button class="button small danger ghost" data-dns-delete="${esc(record.id)}" type="button">删除</button></div></td>
    </tr>`).join("")}</tbody>
  </table>`;
}

function selectCloudflareAccount(accountId) {
  const id = Number(accountId || 0);
  if (!id || !state.cfAccounts.some((account) => Number(account.id) === id)) return;
  state.cfAccountId = id;
  state.cfZones = [];
  state.cfRecords = [];
  state.cfRecordsLoaded = false;
  $("cf-zone").innerHTML = '<option value="">正在同步域名…</option>';
  renderCloudflare();
  renderDnsRecords();
}

async function syncCloudflareZones(button = null) {
  if (!state.cfAccountId) return toast("请先选择 Cloudflare 账户", "bad");
  const previousZone = $("cf-zone")?.value || "";
  if (button) setBusy(button, true, "同步中……");
  try {
    state.cfZones = await api(`/cloudflare/accounts/${state.cfAccountId}/zones/sync`, { method: "POST" });
    $("cf-zone").innerHTML = state.cfZones.length
      ? state.cfZones.map((zone) => `<option value="${esc(zone.zone_id)}">${esc(zone.name)}</option>`).join("")
      : '<option value="">未发现可用域名</option>';
    if (previousZone && state.cfZones.some((zone) => String(zone.zone_id) === String(previousZone))) $("cf-zone").value = previousZone;
    state.cfRecords = [];
    state.cfRecordsLoaded = false;
    $("dns-title").textContent = "DNS 记录";
    $("dns-copy").textContent = state.cfZones.length
      ? `已同步 ${state.cfZones.length} 个域名，正在读取当前域名…`
      : "当前账户未发现可管理域名。";
    renderCloudflare();
    renderDnsRecords();
    if (state.cfZones.length && $("cf-zone")?.value) await loadDns();
  } catch (error) { toast(error.message, "bad"); }
  finally { if (button) setBusy(button, false); }
}

async function cfAction(button) {
  const id = Number(button.dataset.cfTest || button.dataset.cfDelete || button.dataset.cfOpen);
  if (button.dataset.cfTest) {
    setBusy(button, true, "测试中……");
    try {
      const result = await api(`/cloudflare/accounts/${id}/test`, { method: "POST" });
      toast(result.message, "good");
      await loadCloudflare();
    } catch (error) { toast(error.message, "bad"); }
    finally { setBusy(button, false); }
  } else if (button.dataset.cfDelete) {
    if (!confirm("确定删除该 Cloudflare 账户吗？")) return;
    setBusy(button, true, "删除中……");
    try {
      await api(`/cloudflare/accounts/${id}`, { method: "DELETE" });
      if (Number(state.cfAccountId) === id) {
        state.cfAccountId = null;
        state.cfZones = [];
        state.cfRecords = [];
        state.cfRecordsLoaded = false;
      }
      await loadCloudflare();
      toast("已删除");
    } catch (error) { toast(error.message, "bad"); }
    finally { setBusy(button, false); }
  } else {
    selectCloudflareAccount(id);
    await syncCloudflareZones(button);
  }
}

async function loadDns(button = null) {
  const zoneId = $("cf-zone").value;
  if (!state.cfAccountId || !zoneId) return toast("请先选择域名", "bad");
  if (button) setBusy(button, true, "读取中……");
  try {
    state.cfRecords = await api(`/cloudflare/accounts/${state.cfAccountId}/zones/${encodeURIComponent(zoneId)}/dns-records`);
    state.cfRecordsLoaded = true;
    renderDnsRecords();
    const zoneName = state.cfZones.find((zone) => String(zone.zone_id) === String(zoneId))?.name || $("cf-zone").selectedOptions?.[0]?.textContent || "当前域名";
    $("dns-copy").textContent = `${zoneName} · 已读取 ${state.cfRecords.length} 条 DNS 记录`;
  } catch (error) { toast(error.message, "bad"); }
  finally { if (button) setBusy(button, false); }
}

async function dnsPayloadFromDialog(record = null) {
  const values = await openUtilityDialog({
    title: record ? "编辑 DNS 记录" : "新增 DNS 记录",
    copy: "记录会提交到当前选择的 Cloudflare 域名。",
    fields: [
      { name: "type", label: "记录类型", type: "select", value: record?.type || "A", options: ["A", "AAAA", "CNAME", "TXT", "MX", "SRV", "CAA", "NS", "PTR", "URI"] },
      { name: "name", label: "记录名称", value: record?.name || "", placeholder: "例如：www", required: true },
      { name: "content", label: "记录内容", value: record?.content || "", placeholder: "目标 IP 或内容", required: true, full: true },
      { name: "ttl", label: "TTL", type: "number", value: record?.ttl || 1, min: 1, step: 1, help: "1 表示自动" },
      { name: "priority", label: "优先级", type: "number", value: record?.priority ?? "", min: 0, step: 1, help: "MX / SRV / URI 可填写" },
      { name: "proxied", label: "开启 Cloudflare 代理", type: "checkbox", value: Boolean(record?.proxied), help: "仅 A、AAAA、CNAME 生效", full: true },
      { name: "comment", label: "备注", value: record?.comment || "", placeholder: "可选", full: true },
    ],
    submitText: record ? "保存记录" : "创建记录",
    validate: (data) => {
      const type = String(data.type || "").toUpperCase();
      if (!data.name || !data.content) return "记录名称和内容不能为空";
      if (data.proxied && !["A", "AAAA", "CNAME"].includes(type)) return `${type} 记录不支持 Cloudflare 代理`;
      return "";
    },
  });
  if (!values) return null;
  const type = String(values.type || "A").toUpperCase();
  return {
    type,
    name: String(values.name || "").trim(),
    content: String(values.content || "").trim(),
    ttl: Number.isFinite(values.ttl) && values.ttl > 0 ? values.ttl : 1,
    proxied: ["A", "AAAA", "CNAME"].includes(type) ? Boolean(values.proxied) : false,
    priority: values.priority,
    comment: String(values.comment || "").trim() || null,
  };
}

async function createDnsRecord() {
  if (!state.cfAccountId || !$("cf-zone").value) return toast("请先选择域名", "bad");
  const payload = await dnsPayloadFromDialog(); if (!payload) return;
  try { await api(`/cloudflare/accounts/${state.cfAccountId}/zones/${encodeURIComponent($("cf-zone").value)}/dns-records`, { method: "POST", body: payload }); toast("DNS 记录已创建", "good"); await loadDns(); }
  catch (error) { toast(error.message, "bad"); }
}

async function editDnsRecord(recordId) {
  const record = state.cfRecords.find((item) => String(item.id) === String(recordId));
  const payload = await dnsPayloadFromDialog(record); if (!payload) return;
  try { await api(`/cloudflare/accounts/${state.cfAccountId}/zones/${encodeURIComponent($("cf-zone").value)}/dns-records/${encodeURIComponent(recordId)}`, { method: "PUT", body: payload }); toast("DNS 记录已更新", "good"); await loadDns(); }
  catch (error) { toast(error.message, "bad"); }
}

async function deleteDnsRecord(recordId) {
  if (!await openConfirmDialog({ title: "删除 DNS 记录", message: "删除后无法恢复。", submitText: "确认删除", danger: true })) return;
  try { await api(`/cloudflare/accounts/${state.cfAccountId}/zones/${encodeURIComponent($("cf-zone").value)}/dns-records/${encodeURIComponent(recordId)}`, { method: "DELETE" }); toast("DNS 记录已删除"); await loadDns(); }
  catch (error) { toast(error.message, "bad"); }
}


function formatSystemBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes;
  let unit = "B";
  for (const next of units) {
    amount /= 1024;
    unit = next;
    if (amount < 1024) break;
  }
  return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${unit}`;
}

function formatSystemDuration(value) {
  let seconds = Math.max(0, Math.floor(Number(value || 0)));
  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  if (days) return `${days} 天 ${hours} 小时`;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${seconds} 秒`;
}

function systemStatusText(status) {
  if (status === "ok") return "正常";
  if (status === "warning") return "注意";
  return "异常";
}

function setSystemDetailsExpanded(expanded) {
  const details = $("dashboard-system-details");
  const button = $("system-status-toggle");
  if (!details || !button) return;
  details.hidden = !expanded;
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  button.textContent = expanded ? "收起详情" : "查看详情";
}

function renderSystemDiagnostics(data) {
  state.systemDiagnostics = data;
  const components = Array.isArray(data?.components) ? data.components : [];
  const counts = data?.counts || {};
  const storage = data?.storage || {};
  const backups = data?.backups || {};
  const database = components.find((item) => item.key === "database") || {};
  const overall = data?.overall_status || "error";

  const overallBox = $("system-status-overall");
  overallBox.textContent = overall === "ok" ? "系统运行正常" : (overall === "warning" ? "系统存在提醒" : "系统存在异常");
  overallBox.className = `system-overall system-overall-${overall}`;
  $("system-status-checked").textContent = `检查于 ${fmtDate(data?.checked_at).replace(",", "")}`;
  $("system-status-uptime").textContent = formatSystemDuration(data?.uptime_seconds);
  $("system-status-version").textContent = `${data?.display_version || "N&T 2.0"}`;
  $("system-status-database").textContent = systemStatusText(database.status || "error");
  $("system-status-database").className = `system-stat-value system-stat-${database.status || "error"}`;
  $("system-status-database-copy").textContent = database.summary || "数据库未完成检查";
  $("system-status-data").textContent = `${Number(counts.accounts || 0)} / ${Number(counts.instances || 0)}`;
  $("system-status-data-copy").textContent = "账户 / 缓存实例";
  $("system-status-storage").textContent = formatSystemBytes(storage.free_bytes);
  $("system-status-storage-copy").textContent = `已使用 ${Number(storage.used_percent || 0).toFixed(1)}%`;

  $("system-count-accounts").textContent = Number(counts.accounts || 0);
  $("system-count-instances").textContent = Number(counts.instances || 0);
  $("system-count-proxies").textContent = Number(counts.proxies || 0);
  $("system-count-cloudflare").textContent = Number(counts.cloudflare_accounts || 0);
  $("system-count-launch").textContent = Number(counts.launch_profiles || 0);
  $("system-count-audit").textContent = Number(counts.audit_logs || 0);
  $("system-count-backups").textContent = Number(backups.count || 0);
  $("system-database-size").textContent = formatSystemBytes(storage.database_bytes);
  $("system-component-count").textContent = `${components.length} 项`;
  $("system-status-copy").disabled = false;
  if (overall === "error") setSystemDetailsExpanded(true);

  const list = $("system-component-list");
  list.innerHTML = components.length ? components.map((item) => `
    <article class="system-component-row system-component-${esc(item.status || "error")}">
      <span class="system-component-indicator" aria-hidden="true"></span>
      <div class="system-component-name"><strong>${esc(item.name || "检查项")}</strong><small>${esc(item.detail || "")}</small></div>
      <div class="system-component-result"><span>${esc(systemStatusText(item.status))}</span><strong>${esc(item.summary || "—")}</strong></div>
    </article>`).join("") : '<div class="empty">暂无自检结果</div>';
}

async function loadSystemDiagnostics(button = null) {
  const refreshButton = button || $("system-status-refresh");
  setBusy(refreshButton, true, "检查中……");
  try {
    const result = await api("/system/diagnostics");
    renderSystemDiagnostics(result);
    if (button) toast(result.overall_status === "error" ? "自检发现异常" : "系统自检已完成", result.overall_status === "error" ? "bad" : "good");
  } catch (error) {
    $("system-status-overall").textContent = "系统自检失败";
    $("system-status-overall").className = "system-overall system-overall-error";
    $("system-status-checked").textContent = error.message;
    $("system-component-list").innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    $("system-status-copy").disabled = true;
    if (button) toast(error.message, "bad");
  } finally {
    setBusy(refreshButton, false);
  }
}

function systemDiagnosticSummary(data) {
  const components = Array.isArray(data?.components) ? data.components : [];
  const counts = data?.counts || {};
  const storage = data?.storage || {};
  const lines = [
    `OCI-N&T V${data?.version || "1.0.1"} 系统诊断`,
    `检查时间：${fmtDate(data?.checked_at).replace(",", "")}`,
    `总体状态：${systemStatusText(data?.overall_status)}`,
    `运行时间：${formatSystemDuration(data?.uptime_seconds)}`,
    `账户 / 实例 / 代理：${counts.accounts || 0} / ${counts.instances || 0} / ${counts.proxies || 0}`,
    `数据库大小：${formatSystemBytes(storage.database_bytes)}`,
    `磁盘剩余：${formatSystemBytes(storage.free_bytes)}（已使用 ${Number(storage.used_percent || 0).toFixed(1)}%）`,
    "",
    ...components.map((item) => `[${systemStatusText(item.status)}] ${item.name}：${item.summary}${item.detail ? ` · ${item.detail}` : ""}`),
  ];
  return lines.join("\n");
}


async function exportSystemDiagnostics(button = null) {
  setBusy(button, true, "导出中……");
  try {
    const bundle = await api("/system/diagnostics/export");
    const stamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
    downloadTextFile(
      `OCI-NT-diagnostics-${stamp}.json`,
      JSON.stringify(bundle, null, 2),
      "application/json;charset=utf-8",
    );
    toast("脱敏诊断文件已导出", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function copySystemDiagnostics() {
  if (!state.systemDiagnostics) return;
  const text = systemDiagnosticSummary(state.systemDiagnostics);
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast("诊断摘要已复制", "good");
  } catch (error) {
    toast(`复制失败：${error.message}`, "bad");
  }
}


function monitorPercent(used, total) {
  const denominator = Number(total || 0);
  return denominator > 0 ? Math.max(0, Math.min(100, Number(used || 0) / denominator * 100)) : 0;
}

function formatRate(bytesPerSecond) {
  return `${formatSystemBytes(Number(bytesPerSecond || 0))}/s`;
}

function formatTrafficPair(value = {}) {
  return `↓ ${formatSystemBytes(value.rx_bytes || 0)} · ↑ ${formatSystemBytes(value.tx_bytes || 0)}`;
}

function setMonitorCardState(elementId, percent, warning = 75, error = 90) {
  const element = $(elementId)?.closest(".monitor-resource-card");
  if (!element) return;
  element.classList.toggle("monitor-warning", percent >= warning && percent < error);
  element.classList.toggle("monitor-error", percent >= error);
}

function monitorChartSvg(points, series, options = {}) {
  if (!Array.isArray(points) || points.length < 2) return '<div class="empty">历史样本不足，采集两次后显示趋势。</div>';
  const width = 600;
  const height = 150;
  const pad = { left: 30, right: 8, top: 8, bottom: 20 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const allValues = series.flatMap((entry) => points.map((point) => Number(point[entry.key] || 0)));
  const configuredMax = Number(options.max || 0);
  const maximum = configuredMax || Math.max(1, ...allValues) * 1.08;
  const minimum = Number(options.min || 0);
  const scaleX = (index) => pad.left + (points.length === 1 ? 0 : index / (points.length - 1) * innerWidth);
  const scaleY = (value) => pad.top + innerHeight - ((Math.max(minimum, Math.min(maximum, Number(value || 0))) - minimum) / Math.max(1, maximum - minimum)) * innerHeight;
  const grid = [0, .25, .5, .75, 1].map((part) => {
    const y = pad.top + innerHeight * part;
    const value = maximum - (maximum - minimum) * part;
    return `<line class="monitor-chart-grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"></line><text class="monitor-chart-label" x="2" y="${y + 3}">${esc(options.label ? options.label(value) : Math.round(value))}</text>`;
  }).join("");
  const paths = series.map((entry, seriesIndex) => {
    const path = points.map((point, index) => `${index ? "L" : "M"}${scaleX(index).toFixed(1)},${scaleY(point[entry.key]).toFixed(1)}`).join(" ");
    return `<path class="monitor-chart-line${seriesIndex ? ` ${entry.className || "secondary"}` : ""}" d="${path}"></path>`;
  }).join("");
  const first = new Date(points[0].time);
  const last = new Date(points.at(-1).time);
  const timeLabel = (date) => Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="系统趋势图">${grid}${paths}<text class="monitor-chart-label" x="${pad.left}" y="${height - 4}">${esc(timeLabel(first))}</text><text class="monitor-chart-label" text-anchor="end" x="${width - pad.right}" y="${height - 4}">${esc(timeLabel(last))}</text></svg>`;
}

function renderMonitorHistory(payload) {
  state.monitorHistory = payload;
  const points = Array.isArray(payload?.points) ? payload.points : [];
  $("monitor-history-copy").textContent = points.length ? `${points.length} 个聚合点 · ${state.monitorRange}` : "等待历史样本";
  $("monitor-chart-cpu").innerHTML = monitorChartSvg(points, [{ key: "cpu_percent" }, { key: "load1", className: "secondary" }], { max: 100, label: (value) => `${Math.round(value)}%` });
  $("monitor-chart-memory").innerHTML = monitorChartSvg(points, [{ key: "memory_percent" }, { key: "swap_percent", className: "tertiary" }], { max: 100, label: (value) => `${Math.round(value)}%` });
  const maxRate = Math.max(1, ...points.flatMap((point) => [Number(point.rx_rate_bps || 0), Number(point.tx_rate_bps || 0)]));
  $("monitor-chart-network").innerHTML = monitorChartSvg(points, [{ key: "rx_rate_bps" }, { key: "tx_rate_bps", className: "secondary" }], { max: maxRate, label: (value) => formatSystemBytes(value) });
  const last = points.at(-1) || {};
  $("monitor-chart-cpu-value").textContent = `${Number(last.cpu_percent || 0).toFixed(1)}% · Load ${Number(last.load1 || 0).toFixed(2)}`;
  $("monitor-chart-memory-value").textContent = `内存 ${Number(last.memory_percent || 0).toFixed(1)}% · Swap ${Number(last.swap_percent || 0).toFixed(1)}%`;
  $("monitor-chart-network-value").textContent = `↓ ${formatRate(last.rx_rate_bps)} · ↑ ${formatRate(last.tx_rate_bps)}`;
}

function renderMonitorSnapshot(payload) {
  state.monitorSnapshot = payload;
  const metric = payload?.metric || {};
  const system = payload?.system || {};
  const traffic = payload?.traffic || {};
  const containers = payload?.containers || {};
  const settings = payload?.settings || {};
  const memoryPercent = monitorPercent(metric.memory_used_bytes, metric.memory_total_bytes);
  const diskPercent = monitorPercent(metric.disk_used_bytes, metric.disk_total_bytes);
  const swapPercent = monitorPercent(metric.swap_used_bytes, metric.swap_total_bytes);
  const cpuPercent = Number(metric.cpu_percent || 0);

  $("monitor-collected-at").textContent = `更新于 ${fmtDate(metric.collected_at).replace(",", "")}`;
  $("monitor-cpu").textContent = `${cpuPercent.toFixed(1)}%`;
  $("monitor-cpu-copy").textContent = `Load ${Number(metric.load1 || 0).toFixed(2)} / ${Number(metric.load5 || 0).toFixed(2)}`;
  $("monitor-cpu-bar").style.width = `${cpuPercent}%`;
  setMonitorCardState("monitor-cpu", cpuPercent, settings.cpu_alert_percent * .8, settings.cpu_alert_percent);

  $("monitor-memory").textContent = `${memoryPercent.toFixed(1)}%`;
  $("monitor-memory-copy").textContent = `${formatSystemBytes(metric.memory_used_bytes)} / ${formatSystemBytes(metric.memory_total_bytes)} · Swap ${swapPercent.toFixed(1)}%`;
  $("monitor-memory-bar").style.width = `${memoryPercent}%`;
  setMonitorCardState("monitor-memory", memoryPercent, 80, 92);

  $("monitor-disk").textContent = `${diskPercent.toFixed(1)}%`;
  $("monitor-disk-copy").textContent = `剩余 ${formatSystemBytes(metric.disk_free_bytes)}`;
  $("monitor-disk-bar").style.width = `${diskPercent}%`;
  setMonitorCardState("monitor-disk", diskPercent, 80, 92);

  $("monitor-network").textContent = `↓ ${formatRate(metric.rx_rate_bps)}`;
  $("monitor-network-copy").textContent = `↑ ${formatRate(metric.tx_rate_bps)}`;
  $("monitor-interface").textContent = metric.interface || "未识别";
  $("monitor-month-traffic").textContent = formatSystemBytes(Number(traffic.month?.rx_bytes || 0) + Number(traffic.month?.tx_bytes || 0));
  $("monitor-traffic-copy").textContent = `今日 ${formatTrafficPair(traffic.today)}`;
  $("monitor-uptime").textContent = formatSystemDuration(system.uptime_seconds);
  $("monitor-hostname").textContent = system.hostname || "—";

  $("monitor-system-info").innerHTML = [
    ["主机", system.hostname], ["系统", system.operating_system], ["内核", system.kernel], ["架构", system.architecture],
    ["CPU", system.cpu_model], ["核心", `${system.cpu_cores || 0} 核`], ["时区", system.timezone], ["启动", fmtDate(system.boot_time).replace(",", "")],
  ].map(([label, value]) => `<div><dt>${esc(label)}</dt><dd title="${esc(value || "—")}">${esc(value || "—")}</dd></div>`).join("");

  $("monitor-docker-version").textContent = containers.docker_version ? `Docker ${containers.docker_version}` : "Docker 详情不可用";
  const containerRows = Array.isArray(containers.containers) ? containers.containers : [];
  $("monitor-container-list").innerHTML = containerRows.length ? containerRows.map((item) => {
    const healthy = item.state === "running" && ["healthy", "none"].includes(item.health);
    return `<article class="monitor-container-row"><div><strong><span class="status-dot ${healthy ? "good" : "bad"}"></span>${esc(item.name || "容器")}</strong><small>${esc(item.state || "unknown")} · health ${esc(item.health || "none")} · 重启 ${item.restart_count ?? "—"}</small></div><div class="monitor-container-metrics"><span>${item.cpu_percent == null ? "CPU —" : `CPU ${Number(item.cpu_percent).toFixed(1)}%`}</span><small>${item.memory_used_bytes == null ? "内存 —" : formatSystemBytes(item.memory_used_bytes)}</small></div></article>`;
  }).join("") : '<div class="empty">没有可显示的容器状态</div>';

  const alertStates = Array.isArray(payload?.alerts?.states) ? payload.alerts.states : [];
  const activeAlerts = alertStates.filter((item) => item.status !== "NORMAL");
  $("monitor-alert-count").textContent = `${activeAlerts.length} 项`;
  const alertLabels = { cpu: "CPU 使用率", memory: "可用内存", disk: "磁盘空间", swap: "Swap 使用率", traffic: "月流量额度", web: "Web 服务" };
  $("monitor-alert-list").innerHTML = activeAlerts.length ? activeAlerts.map((item) => `<article class="monitor-alert-row monitor-alert-${String(item.status || "").toLowerCase()}"><div><strong>${esc(alertLabels[item.alert_key] || item.alert_key)}</strong><small>${esc(item.detail?.summary || "等待状态恢复")}</small></div><span class="status-pill ${item.status === "ALERT" ? "bad" : "warning"}">${item.status === "ALERT" ? "告警" : "观察"}</span></article>`).join("") : '<div class="empty">当前没有系统资源告警</div>';
}

async function loadMonitorSnapshot(button = null) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    const payload = await api("/system/monitor/current");
    renderMonitorSnapshot(payload);
  } catch (error) {
    if (button) toast(error.message, "bad");
    if ($("monitor-collected-at")) $("monitor-collected-at").textContent = `读取失败：${error.message}`;
  } finally {
    if (button) setBusy(button, false);
  }
}

async function loadMonitorHistory() {
  try {
    renderMonitorHistory(await api(`/system/monitor/history?range=${encodeURIComponent(state.monitorRange)}`));
  } catch (error) {
    if ($("monitor-history-copy")) $("monitor-history-copy").textContent = `历史读取失败：${error.message}`;
  }
}

function stopDashboardMonitor() {
  clearInterval(state.monitorTimer);
  clearInterval(state.monitorHistoryTimer);
  state.monitorTimer = null;
  state.monitorHistoryTimer = null;
}

function startDashboardMonitor() {
  stopDashboardMonitor();
  loadMonitorSnapshot();
  loadMonitorHistory();
  state.monitorTimer = setInterval(() => {
    if (state.currentPage === "dashboard" && !document.hidden) loadMonitorSnapshot();
  }, 10000);
  state.monitorHistoryTimer = setInterval(() => {
    if (state.currentPage === "dashboard" && !document.hidden) loadMonitorHistory();
  }, 60000);
}

async function exportMonitorCsv(button = null) {
  if (button) setBusy(button, true, "导出中……");
  try {
    const content = await api(`/system/monitor/history.csv?range=${encodeURIComponent(state.monitorRange)}`);
    const stamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
    downloadTextFile(`OCI-NT-metrics-${state.monitorRange}-${stamp}.csv`, content, "text/csv;charset=utf-8");
    toast("系统资源历史已导出", "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { if (button) setBusy(button, false); }
}

function renderMonitorSettings(payload) {
  state.monitorSettings = payload;
  $("monitor-enabled").checked = payload.enabled !== false;
  const select = $("monitor-interface-select");
  select.innerHTML = '<option value="auto">自动选择默认出口</option>' + (payload.interfaces || []).map((item) => `<option value="${esc(item.name)}">${esc(item.name)}${item.virtual ? " · 虚拟" : ""}${item.state === "up" ? " · 在线" : ""}</option>`).join("");
  select.value = payload.interface || "auto";
  $("monitor-sample-interval").value = payload.sample_interval_seconds || 60;
  $("monitor-retention-days").value = payload.retention_days || 30;
  $("monitor-alert-duration").value = payload.alert_duration_minutes || 5;
  $("monitor-cpu-alert").value = payload.cpu_alert_percent || 90;
  $("monitor-swap-alert").value = payload.swap_alert_percent || 70;
  $("monitor-traffic-quota").value = payload.monthly_traffic_quota_gb || 0;
  $("monitor-all-containers").checked = Boolean(payload.include_all_containers);
  syncMonitorActionButtons(false);
  $("monitor-settings-summary").textContent = `每 ${payload.sample_interval_seconds || 60} 秒采样 · 保留 ${payload.retention_days || 30} 天 · 网卡 ${payload.interface || "auto"}`;
}

async function loadMonitorSettings(button = null) {
  if (button) setBusy(button, true, "刷新中……");
  try { renderMonitorSettings(await api("/system/monitor/settings")); }
  catch (error) { toast(error.message, "bad"); }
  finally { if (button) setBusy(button, false); }
}

async function saveMonitorSettings(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, "保存中……");
  try {
    const payload = await api("/system/monitor/settings", {
      method: "PUT",
      body: {
        enabled: $("monitor-enabled").checked,
        interface: $("monitor-interface-select").value,
        sample_interval_seconds: Number($("monitor-sample-interval").value),
        retention_days: Number($("monitor-retention-days").value),
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
        include_all_containers: $("monitor-all-containers").checked,
        cpu_alert_percent: Number($("monitor-cpu-alert").value),
        swap_alert_percent: Number($("monitor-swap-alert").value),
        monthly_traffic_quota_gb: Number($("monitor-traffic-quota").value),
        alert_duration_minutes: Number($("monitor-alert-duration").value),
      },
    });
    renderMonitorSettings(payload);
    toast("监控运行设置已保存", "good");
    if (state.currentPage === "dashboard") startDashboardMonitor();
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function resetMonitorTraffic(button) {
  const confirmed = await openConfirmDialog({ title: "重置流量统计起点", message: "不会删除原始历史指标，只会从当前时间重新计算“累计流量”。", submitText: "确认重置" });
  if (!confirmed) return;
  setBusy(button, true, "重置中……");
  try { await api("/system/monitor/traffic/reset", { method: "POST" }); toast("流量统计起点已重置", "good"); await loadMonitorSnapshot(); }
  catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function cleanupMonitorHistory(button) {
  const confirmed = await openConfirmDialog({ title: "清理指标历史", message: "将按照当前保留天数删除过期指标，不影响流量统计起点之后的有效记录。", submitText: "清理历史" });
  if (!confirmed) return;
  setBusy(button, true, "清理中……");
  try { const result = await api("/system/monitor/history/cleanup", { method: "POST" }); toast(`已清理 ${result.metrics_deleted || 0} 条指标`, "good"); }
  catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

function auditActionCategory(action) {
  const value = String(action || "").toUpperCase();
  if (/^(LOGIN_|PASSWORD_|CREDENTIAL_|GOOGLE_AUTH_|OAUTH_)/.test(value)) return "auth";
  if (value.startsWith("CLOUDFLARE_")) return "cloudflare";
  if (value.includes("PROXY")) return "proxy";
  if (/^OCI_(INSTANCE|PUBLIC_IP|PRIVATE_IP|VNIC|IPV6|NETWORK|SECURITY|BOOT_VOLUME|IMAGE|LAUNCH|VNC|BUCKET|OBJECT|PREAUTH|MULTIPART)/.test(value)) return "instance";
  if (/^OCI_(ACCOUNT|ACCOUNTS|REGION|IAM|LIMIT)/.test(value)) return "oci";
  if (/^(SYSTEM_|TELEGRAM_|MONITOR_|BACKUP_|SETTINGS_)/.test(value)) return "system";
  return "other";
}

function auditCategoryLabel(category) {
  return ({
    auth: "账户与登录",
    oci: "OCI 账户",
    instance: "实例与网络",
    proxy: "代理",
    cloudflare: "Cloudflare",
    system: "系统",
    other: "其他",
  })[category] || "其他";
}

function auditMatchesTime(row, filter) {
  if (!filter) return true;
  const created = new Date(row.created_at);
  if (Number.isNaN(created.getTime())) return false;
  const now = new Date();
  if (filter === "today") {
    return created.getFullYear() === now.getFullYear()
      && created.getMonth() === now.getMonth()
      && created.getDate() === now.getDate();
  }
  const days = filter === "7d" ? 7 : filter === "30d" ? 30 : 0;
  if (!days) return true;
  return created.getTime() >= now.getTime() - days * 86400000;
}

function getFilteredAuditRows() {
  const query = ($("audit-search")?.value || "").trim().toLowerCase();
  const category = $("audit-category-filter")?.value || "";
  const username = $("audit-user-filter")?.value || "";
  const time = $("audit-time-filter")?.value || "";
  return state.auditRows.filter((row) => {
    if (category && auditActionCategory(row.action) !== category) return false;
    if (username && String(row.username || "系统") !== username) return false;
    if (!auditMatchesTime(row, time)) return false;
    if (!query) return true;
    return [row.username, row.action_label, row.action, row.resource_label, row.resource_type,
      row.detail_label, row.detail, row.ip_label, row.ip_address]
      .filter(Boolean).join(" ").toLowerCase().includes(query);
  });
}

function renderAuditUserOptions() {
  const select = $("audit-user-filter");
  if (!select) return;
  const current = select.value;
  const users = [...new Set(state.auditRows.map((row) => String(row.username || "系统")))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  select.innerHTML = `<option value="">全部用户</option>${users.map((user) => `<option value="${esc(user)}">${esc(user)}</option>`).join("")}`;
  if (users.includes(current)) select.value = current;
}

function renderAudit() {
  const box = $("audit-list");
  if (!box) return;
  const rows = getFilteredAuditRows();
  if ($("audit-count")) $("audit-count").textContent = `${rows.length} / ${state.auditRows.length} 条`;
  if ($("audit-total-summary")) $("audit-total-summary").textContent = `${state.auditRows.length} 条记录`;
  if (!rows.length) {
    box.innerHTML = `<div class="empty">${state.auditRows.length ? "没有匹配记录" : "暂无记录"}</div>`;
    return;
  }
  box.innerHTML = `<table class="audit-table"><colgroup><col class="audit-col-sequence"><col class="audit-col-time"><col class="audit-col-user"><col class="audit-col-category"><col class="audit-col-action"><col class="audit-col-resource"><col class="audit-col-detail"><col class="audit-col-source"></colgroup><thead><tr><th>序号</th><th>时间</th><th>用户</th><th>类型</th><th>操作</th><th>资源</th><th>详情</th><th>来源</th></tr></thead><tbody>${rows.map((row, index) => {
    const action = row.action_label || row.action || "—";
    const resource = row.resource_label || row.resource_type || "—";
    const detail = row.detail_label || row.detail || "—";
    const source = row.ip_label || row.ip_address || "本机";
    const category = auditActionCategory(row.action);
    return `<tr><td class="sequence-cell"><span>${index + 1}</span></td><td><time class="audit-time audit-time-inline">${esc(fmtDate(row.created_at).replace(",", ""))}</time></td><td><span class="audit-user">${esc(row.username || "系统")}</span></td><td><span class="audit-category audit-category-${category}">${esc(auditCategoryLabel(category))}</span></td><td><span class="audit-action" title="${esc(action)}">${esc(action)}</span></td><td><span class="audit-resource" title="${esc(resource)}">${esc(resource)}</span></td><td><span class="audit-detail" title="${esc(detail)}">${esc(detail)}</span></td><td><span class="audit-source" title="${esc(source)}">${esc(source)}</span></td></tr>`;
  }).join("")}</tbody></table>`;
}

function resetAuditFilters() {
  if ($("audit-category-filter")) $("audit-category-filter").value = "";
  if ($("audit-user-filter")) $("audit-user-filter").value = "";
  if ($("audit-time-filter")) $("audit-time-filter").value = "";
  if ($("audit-search")) $("audit-search").value = "";
  renderAudit();
}

function auditCsvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function downloadTextFile(filename, content, type = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function exportAuditCsv() {
  const rows = getFilteredAuditRows();
  if (!rows.length) return toast("当前没有可导出的记录", "bad");
  const lines = [["序号", "时间", "用户", "类型", "操作", "资源", "详情", "来源"].map(auditCsvCell).join(",")];
  rows.forEach((row, index) => {
    const category = auditActionCategory(row.action);
    lines.push([
      index + 1,
      fmtDate(row.created_at).replace(",", ""),
      row.username || "系统",
      auditCategoryLabel(category),
      row.action_label || row.action || "—",
      row.resource_label || row.resource_type || "—",
      row.detail_label || row.detail || "—",
      row.ip_label || row.ip_address || "本机",
    ].map(auditCsvCell).join(","));
  });
  downloadTextFile(
    `OCI-NT-operation-records-${new Date().toISOString().slice(0, 10)}.csv`,
    `\ufeff${lines.join("\r\n")}`,
    "text/csv;charset=utf-8",
  );
  toast(`已导出 ${rows.length} 条记录`, "good");
}

async function loadAudit(button = null) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    state.auditRows = await api("/audit-logs?limit=500");
    renderAuditUserOptions();
    renderAudit();
  } catch (error) {
    if ($("audit-list")) $("audit-list").innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    toast(error.message, "bad");
  } finally {
    if (button) setBusy(button, false);
  }
}


function backupWeekdayLabel(value) {
  return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][Number(value)] || "星期一";
}

function browserTimezoneLabel(offsetMinutes) {
  const value = Number(offsetMinutes || 0);
  const sign = value >= 0 ? "+" : "-";
  const absolute = Math.abs(value);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = String(absolute % 60).padStart(2, "0");
  return `UTC${sign}${hours}:${minutes}`;
}

function syncBackupScheduleControls() {
  const enabled = Boolean($("backup-schedule-enabled")?.checked);
  const weekly = $("backup-schedule-frequency")?.value === "WEEKLY";
  $("backup-schedule-form")?.classList.toggle("is-disabled", !enabled);
  if ($("backup-schedule-frequency")) $("backup-schedule-frequency").disabled = !enabled;
  if ($("backup-schedule-time")) $("backup-schedule-time").disabled = !enabled;
  if ($("backup-schedule-weekday")) $("backup-schedule-weekday").disabled = !enabled || !weekly;
  syncActionButton("backup-schedule-enabled", "backup-schedule-toggle", "启用自动备份", "停用自动备份");
  const badge = $("backup-schedule-badge");
  if (badge) {
    badge.className = `status-pill ${enabled ? "good" : "muted"}`;
    badge.textContent = enabled ? "已启用" : "已关闭";
  }
}

function renderBackupSchedule(schedule) {
  state.backupSchedule = schedule || null;
  if (!schedule) return;
  if ($("backup-schedule-enabled")) $("backup-schedule-enabled").checked = Boolean(schedule.enabled);
  if ($("backup-schedule-frequency")) $("backup-schedule-frequency").value = schedule.frequency || "DAILY";
  if ($("backup-schedule-time")) $("backup-schedule-time").value = schedule.local_time || "03:00";
  if ($("backup-schedule-weekday")) $("backup-schedule-weekday").value = String(Number(schedule.weekday || 0));
  syncBackupScheduleControls();

  const summary = $("backup-schedule-summary");
  if (!summary) return;
  if (!schedule.enabled) {
    summary.textContent = "自动备份已关闭";
    return;
  }
  const frequency = schedule.frequency === "WEEKLY"
    ? `每周${backupWeekdayLabel(schedule.weekday)}`
    : "每天";
  const zone = browserTimezoneLabel(schedule.timezone_offset_minutes);
  const next = schedule.next_run_at ? ` · 下次 ${fmtDate(schedule.next_run_at)}` : "";
  const last = schedule.last_run_at
    ? ` · 上次 ${schedule.last_status === "SUCCESS" ? "成功" : schedule.last_status === "FAILED" ? "失败" : "已执行"} ${fmtDate(schedule.last_run_at)}`
    : " · 尚未执行";
  summary.textContent = `${frequency} ${schedule.local_time}（${zone}）${next}${last}`;
  summary.title = schedule.last_error || "";
}

async function loadBackupSchedule() {
  const schedule = await api("/system/backups/schedule");
  renderBackupSchedule(schedule);
  return schedule;
}

async function saveBackupSchedule(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, "保存中……");
  try {
    const schedule = await api("/system/backups/schedule", {
      method: "PUT",
      body: {
        enabled: $("backup-schedule-enabled").checked,
        frequency: $("backup-schedule-frequency").value,
        local_time: $("backup-schedule-time").value,
        weekday: Number($("backup-schedule-weekday").value),
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
      },
    });
    renderBackupSchedule(schedule);
    toast(schedule.enabled ? "自动本地备份已启用" : "自动本地备份已关闭", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

function releaseStatusLabel(status) {
  const key = String(status || "").toUpperCase();
  if (key === "SUCCESS") return "成功";
  if (key === "FAILED") return "失败";
  if (key === "RUNNING") return "进行中";
  return key || "—";
}

function releaseEventLabel(type) {
  const labels = {
    UPGRADE_STARTED: "开始升级",
    UPGRADE_COMPLETED: "升级完成",
    UPGRADE_FAILED: "升级失败",
    APP_STARTED: "应用启动",
  };
  return labels[String(type || "").toUpperCase()] || String(type || "—");
}

function renderReleaseInfo(info, history = []) {
  state.releaseInfo = info || null;
  state.releaseHistory = Array.isArray(history) ? history : [];
  const current = $("release-current");
  const list = $("release-history");
  if (current) {
    if (!info) current.innerHTML = '<div class="empty">未读取到版本信息</div>';
    else {
      const fingerprint = String(info.source_fingerprint || "unknown");
      const event = info.latest_event || null;
      current.innerHTML = `
        <article class="release-metric release-product"><small>产品版本</small><strong>${esc(info.display_version || "N&T 2.0")}</strong><span>正式稳定版</span></article>
        <article class="release-metric"><small>后端构建</small><strong title="${esc(info.build_id || "—")}">${esc(info.build_id || "—")}</strong><span>API 运行版本</span></article>
        <article class="release-metric"><small>前端界面</small><strong>release-center18</strong><span>实例与 UI 整合版</span></article>
        <article class="release-metric"><small>数据库结构</small><strong>schema ${Number(info.schema_version || 0)}</strong><span>SQLite 迁移版本</span></article>
        <article class="release-metric release-fingerprint"><small>源码校验</small><strong title="${esc(fingerprint)}">${esc(fingerprint === "unknown" ? "unknown" : `${fingerprint.slice(0, 16)}…`)}</strong><span>SHA-256</span></article>
        <article class="release-metric"><small>运行状态</small><strong>${esc(event ? `${releaseEventLabel(event.event_type)} · ${releaseStatusLabel(event.status)}` : "暂无记录")}</strong><span>${event?.created_at ? esc(fmtDate(event.created_at)) : "等待启动记录"}</span></article>`;
    }
  }
  if (list) {
    list.innerHTML = state.releaseHistory.length ? `
      <table class="release-history-table"><thead><tr><th>时间</th><th>事件</th><th>版本</th><th>Schema</th><th>状态</th><th>详情</th></tr></thead><tbody>
      ${state.releaseHistory.map((row) => {
        const detail = row.detail?.message || row.detail?.reason || row.detail?.error || row.log_path || "—";
        return `<tr><td data-label="时间">${fmtDate(row.created_at)}</td><td data-label="事件">${esc(releaseEventLabel(row.event_type))}</td><td data-label="版本">${esc(row.version || "—")}</td><td data-label="Schema">${Number(row.schema_version || 0)}</td><td data-label="状态"><span class="badge ${String(row.status).toUpperCase() === "SUCCESS" ? "good" : String(row.status).toUpperCase() === "FAILED" ? "bad" : "muted"}">${esc(releaseStatusLabel(row.status))}</span></td><td data-label="详情" class="release-detail"><span title="${esc(String(detail))}">${esc(String(detail))}</span></td></tr>`;
      }).join("")}
      </tbody></table>` : '<div class="empty">暂无升级记录</div>';
  }
}

async function loadReleaseInfo(button = null) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    const [info, history] = await Promise.all([
      api("/system/release"),
      api("/system/release/history?limit=30"),
    ]);
    renderReleaseInfo(info, history);
  } catch (error) {
    if ($("release-current")) $("release-current").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
    if ($("release-history")) $("release-history").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
    toast(error.message, "bad");
  } finally {
    if (button) setBusy(button, false);
  }
}

function renderAuthSessions(rows = []) {
  state.authSessions = Array.isArray(rows) ? rows : [];
  const box = $("session-list");
  if (!box) return;
  box.innerHTML = state.authSessions.length ? `<table class="session-table"><thead><tr><th>设备</th><th>登录方式</th><th>来源 IP</th><th>最近活动</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.authSessions.map((row) => {
    const current = row.is_current;
    const status = current ? "当前设备" : row.active ? "已登录" : row.revoked_at ? "已注销" : "已过期";
    const tone = current || row.active ? "good" : "muted";
    const action = row.active && !current ? `<button class="button small danger ghost" data-session-revoke="${esc(row.session_id)}" type="button">注销</button>` : "—";
    return `<tr><td><strong title="${esc(row.user_agent || "未知设备")}">${esc((row.user_agent || "未知设备").slice(0, 45))}</strong><small>${esc(row.session_id.slice(0, 12))}…</small></td><td>${esc(row.login_method || "LOCAL")}</td><td>${esc(row.ip_address || "LOCAL")}</td><td>${esc(fmtDate(row.last_seen_at || row.created_at))}</td><td><span class="badge ${tone}">${esc(status)}</span></td><td>${action}</td></tr>`;
  }).join("")}</tbody></table>` : '<div class="empty">暂无登录会话</div>';
}

function renderLoginAttempts(rows = []) {
  state.loginAttempts = Array.isArray(rows) ? rows : [];
  const box = $("login-attempt-list");
  if (!box) return;
  box.innerHTML = state.loginAttempts.length ? `<table><thead><tr><th>时间</th><th>用户名</th><th>方式 / 原因</th><th>来源</th><th>结果</th></tr></thead><tbody>${state.loginAttempts.map((row) => `<tr><td>${esc(fmtDate(row.created_at))}</td><td>${esc(row.username || "—")}</td><td>${esc(row.reason || "—")}</td><td>${esc(row.ip_address || "LOCAL")}</td><td><span class="badge ${row.success ? "good" : "bad"}">${row.success ? "成功" : "失败"}</span></td></tr>`).join("")}</tbody></table>` : '<div class="empty">暂无登录记录</div>';
}

async function loadAuthSessions(button = null, includeAttempts = false) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    const requests = [api("/auth/sessions")];
    if (includeAttempts) requests.push(api("/auth/login-attempts?limit=50"));
    const [sessions, attempts] = await Promise.all(requests);
    renderAuthSessions(sessions);
    if (includeAttempts) renderLoginAttempts(attempts);
  } catch (error) {
    if ($("session-list")) $("session-list").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  } finally {
    if (button) setBusy(button, false);
  }
}

async function revokeAuthSession(sessionId, button) {
  const confirmed = await window.openConfirmDialog({
    title: "注销登录设备",
    copy: "该设备的 Token 将立即失效。",
    submitText: "确认注销",
    danger: true,
  });
  if (!confirmed) return;
  setBusy(button, true, "注销中……");
  try {
    await api(`/auth/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    toast("会话已注销", "good");
    await loadAuthSessions();
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function logoutOtherSessions(button) {
  const confirmed = await window.openConfirmDialog({
    title: "注销其他设备",
    copy: "保留当前浏览器登录，其他设备的会话会立即失效。",
    submitText: "注销其他设备",
    danger: true,
  });
  if (!confirmed) return;
  setBusy(button, true, "处理中……");
  try {
    const result = await api("/auth/sessions/logout-others", { method: "POST" });
    toast(result.message, "good");
    await loadAuthSessions();
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

function renderResourceStatus(status, limits = null) {
  state.resourceStatus = status || null;
  state.resourceLimits = limits || status?.limits || null;
  const box = $("resource-status");
  if (box && status) {
    const warnings = (status.warnings || []).map((item) => `<li>${esc(item)}</li>`).join("");
    box.innerHTML = `
      <article><small>可用内存</small><strong>${formatSystemBytes(status.memory_available_bytes || 0)}</strong><span>Swap ${formatSystemBytes(status.swap_free_bytes || 0)}</span></article>
      <article><small>数据盘可用</small><strong>${formatSystemBytes(status.disk_free_bytes || 0)}</strong><span>inode ${Number(status.inode_free || 0).toLocaleString()}</span></article>
      <article><small>SQLite</small><strong>${formatSystemBytes(status.database_bytes || 0)}</strong><span>WAL ${formatSystemBytes(status.wal_bytes || 0)}</span></article>
      <article><small>运行任务</small><strong>${Number(status.active_tasks || 0)}</strong><span>上限 ${Number((limits || status.limits || {}).max_active_tasks || 0)}</span></article>
      ${warnings ? `<ul class="resource-warning-list">${warnings}</ul>` : '<div class="policy-box compact-policy resource-ok"><strong>资源状态正常</strong><span>当前可以启动新的批量任务。</span></div>'}`;
  }
  const resolved = limits || status?.limits;
  if (resolved) {
    if ($("resource-min-memory")) $("resource-min-memory").value = resolved.min_free_memory_mb;
    if ($("resource-min-disk")) $("resource-min-disk").value = resolved.min_free_disk_mb;
    if ($("resource-max-active")) $("resource-max-active").value = resolved.max_active_tasks;
    if ($("resource-max-items")) $("resource-max-items").value = resolved.max_task_items;
  }
}

async function loadResourceStatus(button = null) {
  if (button) setBusy(button, true, "刷新中……");
  try {
    const [status, limits] = await Promise.all([
      api("/system/resources"),
      api("/system/resources/limits"),
    ]);
    renderResourceStatus(status, limits);
  } catch (error) {
    if ($("resource-status")) $("resource-status").innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  } finally {
    if (button) setBusy(button, false);
  }
}

async function saveResourceLimits(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, "保存中……");
  try {
    const limits = await api("/system/resources/limits", {
      method: "PUT",
      body: {
        min_free_memory_mb: Number($("resource-min-memory").value),
        min_free_disk_mb: Number($("resource-min-disk").value),
        max_active_tasks: Number($("resource-max-active").value),
        max_task_items: Number($("resource-max-items").value),
      },
    });
    renderResourceStatus(await api("/system/resources"), limits);
    toast("系统资源保护设置已保存", "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function loadSettings() {
  try {
    const [telegram, google, backupPolicy, backupSchedule, releaseInfo, releaseHistory, resourceStatus, resourceLimits, sessions, monitorSettings] = await Promise.all([
      api("/settings/telegram"),
      api("/settings/google-auth"),
      api("/system/backups/policy"),
      api("/system/backups/schedule"),
      api("/system/release"),
      api("/system/release/history?limit=30"),
      api("/system/resources"),
      api("/system/resources/limits"),
      api("/auth/sessions"),
      api("/system/monitor/settings"),
    ]);
    $("telegram-enabled").checked = telegram.enabled;
    $("telegram-chat").value = telegram.chat_id || "";
    $("telegram-token").placeholder = telegram.has_bot_token ? "已保存；留空表示不修改" : "请输入 Bot Token";
    $("telegram-account-check").checked = telegram.account_check !== false;
    $("telegram-instance-operation").checked = telegram.instance_operation !== false;
    $("telegram-launch-task").checked = telegram.launch_task !== false;
    $("telegram-proxy-alert").checked = telegram.proxy_alert !== false;
    $("telegram-system-backup").checked = telegram.system_backup !== false;
    $("telegram-system-resource").checked = telegram.system_resource !== false;
    syncTelegramAction();
    renderGoogleAuthSettings(google);
    renderBackupPolicy(backupPolicy);
    renderBackupSchedule(backupSchedule);
    renderReleaseInfo(releaseInfo, releaseHistory);
    renderResourceStatus(resourceStatus, resourceLimits);
    renderMonitorSettings(monitorSettings);
    renderAuthSessions(sessions);
    await loadBackups();
  } catch (error) { toast(error.message, "bad"); }
}

async function updateCredentials(event) {
  event.preventDefault();
  const username = $("credential-username").value.trim();
  const password = $("credential-password").value;
  if (!username && password === "") { toast("请填写新用户名或新密码", "bad"); return; }
  try {
    const result = await api("/settings/credentials", { method: "PUT", body: {
      current_password: $("credential-current").value,
      username: username || null,
      new_password: password === "" ? null : password,
    }});
    saveToken(result.access_token);
    state.user.username = result.username;
    $("current-username").textContent = result.username;
    event.currentTarget.reset();
    $("credential-username").placeholder = `当前：${result.username}；留空不修改`;
    toast("登录用户名或密码已更新", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function updateTelegram(event) {
  event.preventDefault();
  try {
    const result = await api("/settings/telegram", { method: "PUT", body: {
      enabled: $("telegram-enabled").checked,
      chat_id: $("telegram-chat").value.trim(),
      bot_token: $("telegram-token").value.trim() || null,
      account_check: $("telegram-account-check").checked,
      instance_operation: $("telegram-instance-operation").checked,
      launch_task: $("telegram-launch-task").checked,
      proxy_alert: $("telegram-proxy-alert").checked,
      system_backup: $("telegram-system-backup").checked,
      system_resource: $("telegram-system-resource").checked,
    }});
    $("telegram-token").value = "";
    $("telegram-token").placeholder = result.has_bot_token ? "已保存；留空表示不修改" : "请输入 Bot Token";
    $("telegram-enabled").checked = Boolean(result.enabled);
    syncTelegramAction();
    toast("Telegram 设置已保存", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function testTelegram() {
  try { const result = await api("/settings/telegram/test", { method: "POST" }); toast(result.message, "good"); }
  catch (error) { toast(error.message, "bad"); }
}


function renderBackupPolicy(policy) {
  state.backupPolicy = policy || null;
  if (!policy) return;
  if ($("backup-retention-days")) $("backup-retention-days").value = policy.retention_days;
  if ($("backup-keep-latest")) $("backup-keep-latest").value = policy.keep_latest;
  if ($("backup-max-count")) $("backup-max-count").value = policy.max_count;
  if ($("backup-policy-summary")) {
    $("backup-policy-summary").textContent = `当前 ${Number(policy.current_count || 0)} 份 · ${formatSystemBytes(policy.current_bytes || 0)} · 保留 ${policy.retention_days} 天，至少 ${policy.keep_latest} 份，最多 ${policy.max_count} 份`;
  }
}

async function saveBackupPolicy(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, "保存中……");
  try {
    await api("/system/backups/policy", {
      method: "PUT",
      body: {
        retention_days: Number($("backup-retention-days").value),
        keep_latest: Number($("backup-keep-latest").value),
        max_count: Number($("backup-max-count").value),
      },
    });
    renderBackupPolicy(await api("/system/backups/policy"));
    toast("备份保留策略已保存", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function cleanupBackups(button) {
  setBusy(button, true, "检查中……");
  try {
    const preview = await api("/system/backups/cleanup", { method: "POST", body: { dry_run: true } });
    if (!preview.candidate_count) {
      toast("当前没有需要清理的备份", "good");
      return;
    }
    const names = (preview.candidates || []).slice(0, 5).map((item) => item.name).join("\n");
    const confirmed = await window.openConfirmDialog({
      title: "清理本地备份",
      copy: `将按当前策略删除 ${preview.candidate_count} 份备份。`,
      message: `${names}${preview.candidate_count > 5 ? `\n以及其他 ${preview.candidate_count - 5} 份` : ""}`,
      submitText: "确认清理",
      danger: true,
    });
    if (!confirmed) return;
    const result = await api("/system/backups/cleanup", { method: "POST", body: { dry_run: false } });
    toast(`已清理 ${result.deleted_count} 份备份`, "good");
    renderBackupPolicy(await api("/system/backups/policy"));
    await Promise.all([loadBackups(), loadSystemDiagnostics()]);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function loadBackups() {
  const box = $("backup-list");
  if (!box) return;
  try {
    const [rows, drills, policy] = await Promise.all([
      api("/system/backups"),
      api("/system/backups/restore-drills?limit=100"),
      api("/system/backups/policy"),
    ]);
    const latestDrill = new Map();
    (drills || []).forEach((row) => {
      if (row.backup_name && !latestDrill.has(row.backup_name)) latestDrill.set(row.backup_name, row);
    });
    box.innerHTML = rows.length ? rows.map((row) => {
      const drill = latestDrill.get(row.name);
      const drillCopy = drill
        ? ` · 恢复演练 ${drill.status === "SUCCESS" ? "通过" : "失败"} ${fmtDate(drill.created_at)}`
        : " · 未执行恢复演练";
      return `<div class="simple-row backup-row"><div class="backup-row-copy"><strong>${esc(row.name || "备份")}</strong><small>${fmtDate(row.created_at)} · ${esc(row.size_human || row.size || "")}${esc(drillCopy)}</small></div><div class="row-actions compact"><button class="button small" data-backup-verify="${esc(row.name || "")}" type="button">验证</button><button class="button small" data-backup-drill="${esc(row.name || "")}" type="button">恢复演练</button><button class="button small danger ghost" data-backup-restore="${esc(row.name || "")}" type="button"${drill?.status === "SUCCESS" ? "" : " disabled"}>正式恢复</button><button class="button small danger backup-delete-button" data-backup-delete="${esc(row.name || "")}" type="button">删除备份</button></div></div>`;
    }).join("") : '<div class="empty">暂无备份</div>';
    box.querySelectorAll("[data-backup-verify]").forEach((button) => button.addEventListener("click", () => verifyBackup(button)));
    box.querySelectorAll("[data-backup-drill]").forEach((button) => button.addEventListener("click", () => restoreDrill(button)));
    box.querySelectorAll("[data-backup-restore]").forEach((button) => button.addEventListener("click", () => formalRestoreBackup(button)));
    box.querySelectorAll("[data-backup-delete]").forEach((button) => button.addEventListener("click", () => deleteBackup(button)));
    renderBackupPolicy(policy);
  } catch (error) { box.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; }
}


async function formalRestoreBackup(button) {
  const name = button?.dataset.backupRestore || "";
  if (!name) return;
  const confirmation = `RESTORE ${name}`;
  const values = await window.openUtilityDialog({
    title: "正式恢复数据库",
    copy: "高风险操作：恢复前会自动创建保护备份，验证失败会自动切回；当前本地管理员账号和密码会保留，所有登录会话将失效。",
    message: `待恢复备份：${name}`,
    fields: [
      { name: "current_password", label: "当前登录密码", type: "password", required: true, full: true },
      { name: "confirmation", label: `请输入 ${confirmation}`, placeholder: confirmation, required: true, full: true },
    ],
    submitText: "确认正式恢复",
    danger: true,
    validate: (payload) => payload.confirmation === confirmation ? "" : `请输入 ${confirmation}`,
  });
  if (!values) return;
  setBusy(button, true, "恢复中……");
  try {
    const result = await api(`/system/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      body: values,
    });
    await window.openMessageDialog({
      title: "数据库恢复成功",
      copy: `保护备份：${result.protection_backup_name || "已创建"}`,
      message: "当前及其他设备的登录会话已经失效；请使用当前本地用户名和密码重新登录。",
    });
    logout(false);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function restoreDrill(button) {
  const name = button?.dataset.backupDrill || "";
  if (!name) return;
  const confirmed = await window.openConfirmDialog({
    title: "验证备份可恢复性",
    copy: "系统会复制该备份到临时目录，在隔离数据库上执行迁移和 API 启动检查。不会替换当前数据库。",
    message: `备份：${name}`,
    submitText: "开始演练",
  });
  if (!confirmed) return;
  setBusy(button, true, "演练中……");
  try {
    const result = await api(`/system/backups/${encodeURIComponent(name)}/restore-drill`, { method: "POST" });
    const schema = `${Number(result.schema_before || 0)} → ${Number(result.schema_after || 0)}`;
    if (result.status === "SUCCESS") {
      toast(`恢复演练通过：schema ${schema} · OpenAPI ${Number(result.openapi_paths || 0)} 条 · ${Number(result.duration_ms || 0)}ms`, "good");
    } else {
      toast(`恢复演练失败：${result.error || "未知错误"}`, "bad");
    }
    await Promise.all([loadBackups(), loadSystemDiagnostics(), loadReleaseInfo()]);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function verifyBackup(button) {
  const name = button?.dataset.backupVerify || "";
  if (!name) return;
  setBusy(button, true, "验证中……");
  try {
    const result = await api(`/system/backups/${encodeURIComponent(name)}/verify`, { method: "POST" });
    const shortHash = String(result.sha256 || "").slice(0, 16);
    toast(`备份完整：quick_check=${result.quick_check} · SHA256 ${shortHash}…`, "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function maintainDatabase(button) {
  setBusy(button, true, "检查中……");
  try {
    const result = await api("/system/database/maintain", {
      method: "POST",
      body: { vacuum: false },
    });
    toast(`数据库正常：${result.quick_check_after} · WAL 已整理`, "good");
    await Promise.all([loadSystemDiagnostics(), loadBackups()]);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function deleteBackup(button) {
  const name = button?.dataset.backupDelete || "";
  if (!name) return;
  const confirmed = await window.openConfirmDialog({
    title: "删除本地备份",
    copy: "删除后无法恢复。",
    message: `确定删除备份“${name}”吗？`,
    submitText: "删除备份",
    danger: true,
  });
  if (!confirmed) return;
  setBusy(button, true, "删除中……");
  try {
    const result = await api(`/system/backups/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast(result.message || `备份已删除：${name}`, "good");
    await loadBackups();
  } catch (error) {
    toast(error.message, "bad");
    setBusy(button, false);
  }
}

async function createBackup() {
  const button = $("backup-create");
  setBusy(button, true, "备份中……");
  try { const result = await api("/system/backups", { method: "POST" }); const cleaned = Number(result.cleanup?.deleted_count || 0); toast(`备份已创建：${result.name}${cleaned ? ` · 自动清理 ${cleaned} 份旧备份` : ""}`, "good"); await loadBackups(); }
  catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

function bindEvents() {
  restoreLocalControl("proxy-status-filter", "oci_nt_filter_proxy_status");
  restoreLocalControl("proxy-search", "oci_nt_filter_proxy_search");
  restoreLocalControl("task-type-filter", "oci_nt_filter_task_type");
  restoreLocalControl("task-status-filter", "oci_nt_filter_task_status");
  restoreLocalControl("dns-type-filter", "oci_nt_filter_dns_type");
  restoreLocalControl("dns-search", "oci_nt_filter_dns_search");

  $("login-form").addEventListener("submit", login);
  $("google-login").addEventListener("click", () => { location.href = `${API}/auth/google/start`; });
  $("logout").addEventListener("click", () => logout());
  $("account-search").addEventListener("input", () => {
    if (state.ui2) state.ui2.accountPage = 1;
    saveLocalControl("account-search", "oci_nt_filter_account_search");
    renderAccounts();
  });
  $("account-add").addEventListener("click", openAccountDialog);
  $("account-form").addEventListener("submit", importAccount);
  $("api-config-file").addEventListener("change", handleOciConfigFileSelection);
  $("api-config-file-clear").addEventListener("click", () => resetOciConfigFileImport());
  $("api-config-profile").addEventListener("change", (event) => applyImportedOciConfigProfile(event.currentTarget.value));
  $("api-private-key-file").addEventListener("change", (event) => {
    const file = event.currentTarget.files?.[0];
    if (file) {
      importedPrivateKeyFile = null;
      $("api-private-key-copy").textContent = `已选择私钥：${file.name}`;
    }
  });
  $("api-config").addEventListener("input", () => {
    updateImportRegionDetection();
    if (!applyingImportedOciConfig && importedOciConfigState) {
      importedOciConfigState = null;
      $("api-config-profile-row").hidden = true;
      $("api-config-profile").innerHTML = "";
      setOciConfigFileStatus("Config 内容已手动修改，将按当前文本导入。", "muted");
    }
  });
  $("import-proxy-enabled").addEventListener("change", setImportProxyState);
  $("import-proxy-mode-existing").addEventListener("change", setImportProxyState);
  $("import-proxy-mode-new").addEventListener("change", setImportProxyState);
  $("import-proxy-profile-select").addEventListener("change", setImportProxyState);
  $("import-proxy-test").addEventListener("click", testImportProxy);
  $("proxy-form").addEventListener("submit", saveProxyForm);
  $("proxy-enabled").addEventListener("change", setProxyEditorState);
  $("proxy-mode-existing").addEventListener("change", setProxyEditorState);
  $("proxy-mode-new").addEventListener("change", setProxyEditorState);
  $("proxy-clear").addEventListener("click", clearProxyForm);
  $("proxy-test-unsaved").addEventListener("click", testProxyDialogAddress);
  $("bulk-check").addEventListener("click", (event) => startBulkCheck(event.currentTarget));
  $("batch-proxy-open")?.addEventListener("click", openBatchProxyDialog);
  $("proxy-import-text")?.addEventListener("click", openProxyTextImportDialog);
  $("proxy-add-single")?.addEventListener("click", () => openProxyProfileEditor());
  $("proxy-test-all")?.addEventListener("click", (event) => startProxyHealthAll(event.currentTarget));
  $("proxy-auto-allocate")?.addEventListener("click", (event) => autoAllocateProxies(event.currentTarget));
  $("proxy-status-filter")?.addEventListener("change", () => { saveLocalControl("proxy-status-filter", "oci_nt_filter_proxy_status"); renderProxyManagement(); });
  $("proxy-search")?.addEventListener("input", () => { saveLocalControl("proxy-search", "oci_nt_filter_proxy_search"); renderProxyManagement(); });
  $("proxy-filter-reset")?.addEventListener("click", () => {
    if ($("proxy-status-filter")) $("proxy-status-filter").value = "";
    if ($("proxy-search")) $("proxy-search").value = "";
    clearLocalControls(["oci_nt_filter_proxy_status", "oci_nt_filter_proxy_search"]);
    renderProxyManagement();
  });
  $("bulk-cancel").addEventListener("click", cancelBulk);
  $("task-cleanup")?.addEventListener("click", (event) => cleanupTaskHistory(event.currentTarget));
  $("task-refresh")?.addEventListener("click", (event) => loadTasks(event.currentTarget));
  $("task-type-filter")?.addEventListener("change", () => { saveLocalControl("task-type-filter", "oci_nt_filter_task_type"); renderTaskCenter(); });
  $("task-status-filter")?.addEventListener("change", () => { saveLocalControl("task-status-filter", "oci_nt_filter_task_status"); renderTaskCenter(); });
  const ntSavedTaskSource = localStorage.getItem("oci_nt_filter_task_source") || "";
  if ($("task-source-filter") && ["manual", "scheduled", "schedule_now"].includes(ntSavedTaskSource)) $("task-source-filter").value = ntSavedTaskSource;
  $("task-source-filter")?.addEventListener("change", () => { saveLocalControl("task-source-filter", "oci_nt_filter_task_source"); renderTaskCenter(); });
  $("task-filter-reset")?.addEventListener("click", () => { if ($("task-type-filter")) $("task-type-filter").value = ""; if ($("task-status-filter")) $("task-status-filter").value = ""; if ($("task-source-filter")) $("task-source-filter").value = ""; clearLocalControls(["oci_nt_filter_task_type", "oci_nt_filter_task_status", "oci_nt_filter_task_source"]); renderTaskCenter(); });
  $("task-detail-close")?.addEventListener("click", () => { state.taskDetail = null; $("task-detail-panel").hidden = true; });
  $("task-export-csv")?.addEventListener("click", exportTaskCsv);
  $("task-export-json")?.addEventListener("click", exportTaskJson);
  $("task-safe-resume")?.addEventListener("click", (event) => safeResumeCurrentTask(event.currentTarget));
  $("tenant-back").addEventListener("click", () => showPage(window.ui2TenantBackPage || "accounts"));
  $("cf-add").addEventListener("click", () => { $("cf-form").reset(); $("cf-error").hidden = true; $("cf-dialog").showModal(); });
  $("cf-refresh")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "刷新中……");
    try { await loadCloudflare(); } finally { setBusy(button, false); }
  });
  $("cf-account-status")?.addEventListener("change", renderCloudflare);
  $("cf-account-search")?.addEventListener("input", renderCloudflare);
  $("cf-account-reset")?.addEventListener("click", () => {
    if ($("cf-account-status")) $("cf-account-status").value = "";
    if ($("cf-account-search")) $("cf-account-search").value = "";
    renderCloudflare();
  });
  $("cf-form").addEventListener("submit", addCloudflare);
  $("dns-zone-sync")?.addEventListener("click", (event) => syncCloudflareZones(event.currentTarget));
  $("cf-zone")?.addEventListener("change", async () => {
    state.cfRecords = [];
    state.cfRecordsLoaded = false;
    renderDnsRecords();
    const zoneName = $("cf-zone").selectedOptions?.[0]?.textContent || "当前域名";
    if (!$("cf-zone").value) {
      $("dns-copy").textContent = "请选择域名。";
      return;
    }
    $("dns-copy").textContent = `${zoneName} · 正在读取 DNS 记录…`;
    await loadDns();
  });
  $("dns-refresh").addEventListener("click", (event) => loadDns(event.currentTarget));
  $("dns-type-filter")?.addEventListener("change", () => { saveLocalControl("dns-type-filter", "oci_nt_filter_dns_type"); renderDnsRecords(); });
  $("dns-search")?.addEventListener("input", () => { saveLocalControl("dns-search", "oci_nt_filter_dns_search"); renderDnsRecords(); });
  $("dns-filter-reset")?.addEventListener("click", () => {
    if ($("dns-type-filter")) $("dns-type-filter").value = "";
    if ($("dns-search")) $("dns-search").value = "";
    clearLocalControls(["oci_nt_filter_dns_type", "oci_nt_filter_dns_search"]);
    renderDnsRecords();
  });
  $("dns-add").addEventListener("click", createDnsRecord);
  $("system-status-refresh")?.addEventListener("click", (event) => loadSystemDiagnostics(event.currentTarget));
  $("system-status-copy")?.addEventListener("click", copySystemDiagnostics);
  $("system-status-export")?.addEventListener("click", (event) => exportSystemDiagnostics(event.currentTarget));
  $("system-status-toggle")?.addEventListener("click", () => {
    const details = $("dashboard-system-details");
    setSystemDetailsExpanded(Boolean(details?.hidden));
  });
  $("monitor-refresh")?.addEventListener("click", (event) => loadMonitorSnapshot(event.currentTarget));
  $("monitor-export")?.addEventListener("click", (event) => exportMonitorCsv(event.currentTarget));
  document.querySelectorAll("[data-monitor-range]").forEach((button) => button.addEventListener("click", () => {
    state.monitorRange = button.dataset.monitorRange || "1h";
    document.querySelectorAll("[data-monitor-range]").forEach((item) => item.classList.toggle("active", item === button));
    loadMonitorHistory();
  }));
  $("audit-refresh")?.addEventListener("click", (event) => loadAudit(event.currentTarget));
  $("audit-export")?.addEventListener("click", exportAuditCsv);
  $("audit-category-filter")?.addEventListener("change", renderAudit);
  $("audit-user-filter")?.addEventListener("change", renderAudit);
  $("audit-time-filter")?.addEventListener("change", renderAudit);
  $("audit-search")?.addEventListener("input", renderAudit);
  $("audit-filter-reset")?.addEventListener("click", resetAuditFilters);
  $("credential-form").addEventListener("submit", updateCredentials);
  $("session-refresh")?.addEventListener("click", (event) => loadAuthSessions(event.currentTarget, true));
  $("session-logout-others")?.addEventListener("click", (event) => logoutOtherSessions(event.currentTarget));
  $("resource-refresh")?.addEventListener("click", (event) => loadResourceStatus(event.currentTarget));
  $("resource-limit-form")?.addEventListener("submit", saveResourceLimits);
  $("monitor-settings-refresh")?.addEventListener("click", (event) => loadMonitorSettings(event.currentTarget));
  $("monitor-enabled-toggle")?.addEventListener("click", () => {
    $("monitor-enabled").checked = !$("monitor-enabled").checked;
    syncMonitorActionButtons(true);
  });
  $("monitor-all-containers-toggle")?.addEventListener("click", () => {
    $("monitor-all-containers").checked = !$("monitor-all-containers").checked;
    syncMonitorActionButtons(true);
  });
  $("monitor-settings-form")?.addEventListener("submit", saveMonitorSettings);
  $("monitor-traffic-reset")?.addEventListener("click", (event) => resetMonitorTraffic(event.currentTarget));
  $("monitor-history-cleanup")?.addEventListener("click", (event) => cleanupMonitorHistory(event.currentTarget));
  document.querySelector(".login-attempts-details")?.addEventListener("toggle", (event) => { if (event.currentTarget.open) loadAuthSessions(null, true); });
  $("telegram-form").addEventListener("submit", updateTelegram);
  $("telegram-test").addEventListener("click", testTelegram);
  $("telegram-toggle")?.addEventListener("click", () => {
    $("telegram-enabled").checked = !$("telegram-enabled").checked;
    syncTelegramAction();
  });
  $("google-auth-form").addEventListener("submit", saveGoogleAuth);
  $("google-auth-toggle")?.addEventListener("click", () => {
    $("google-auth-enabled").checked = !$("google-auth-enabled").checked;
    syncGoogleAuthAction();
  });
  $("google-auth-test").addEventListener("click", testGoogleAuth);
  $("google-copy-callback").addEventListener("click", async () => {
    const value = $("google-redirect-uri").value.trim();
    if (!value) { toast("请先填写 Google 回调地址", "bad"); return; }
    await navigator.clipboard.writeText(value);
    toast("回调地址已复制", "good");
  });
  $("database-maintain")?.addEventListener("click", (event) => maintainDatabase(event.currentTarget));
  $("backup-schedule-enabled")?.addEventListener("change", syncBackupScheduleControls);
  $("backup-schedule-toggle")?.addEventListener("click", () => {
    $("backup-schedule-enabled").checked = !$("backup-schedule-enabled").checked;
    syncBackupScheduleControls();
  });
  $("backup-schedule-frequency")?.addEventListener("change", syncBackupScheduleControls);
  $("backup-schedule-form")?.addEventListener("submit", saveBackupSchedule);
  $("release-refresh")?.addEventListener("click", (event) => loadReleaseInfo(event.currentTarget));
  $("backup-policy-form")?.addEventListener("submit", saveBackupPolicy);
  $("backup-cleanup")?.addEventListener("click", (event) => cleanupBackups(event.currentTarget));
  $("backup-create").addEventListener("click", createBackup);

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.page) showPage(button.dataset.page);
    else if (button.dataset.closeDialog) $(button.dataset.closeDialog).close();
    else if (button.dataset.manageAccount) openTenant(button.dataset.manageAccount);
    else if (button.dataset.checkAccount) checkAccount(Number(button.dataset.checkAccount), button);
    else if (button.dataset.tenantTab) selectTenantTab(button.dataset.tenantTab);
    else if (button.hasAttribute("data-sync-tenant-instances")) loadTenantInstances(true);
    else if (button.dataset.instanceAction) instanceAction(button);
    else if (button.dataset.replaceIp) replaceIp(button);
    else if (button.dataset.editInstance) editInstance(button);
    else if (button.dataset.terminateInstance) terminateInstance(button);
    else if (button.dataset.configProxy) configureProxy(Number(button.dataset.configProxy));
    else if (button.dataset.testProxy) testProxy(Number(button.dataset.testProxy));
    else if (button.dataset.rotateProfile) rotateProxyProfile(Number(button.dataset.rotateProfile), button);
    else if (button.dataset.testProfile) testProxyProfile(Number(button.dataset.testProfile));
    else if (button.dataset.taskDetail) openTaskDetail(Number(button.dataset.taskDetail));
    else if (button.dataset.taskCancel) cancelManualTask(Number(button.dataset.taskCancel));
    else if (button.dataset.taskRetry) retryManualTask(Number(button.dataset.taskRetry));
    else if (button.dataset.taskCopyFailures) copyTaskFailures(Number(button.dataset.taskCopyFailures));
    else if (button.dataset.sessionRevoke) revokeAuthSession(button.dataset.sessionRevoke, button);
    else if (button.dataset.editProfile) openProxyProfileEditor(Number(button.dataset.editProfile));
    else if (button.dataset.deleteProfile) deleteProxyProfile(Number(button.dataset.deleteProfile));
    else if (button.dataset.deleteAccount) deleteAccount(Number(button.dataset.deleteAccount));
    else if (button.hasAttribute("data-refresh-launch-jobs")) loadLaunchJobs();
    else if (button.dataset.launchDetail) showLaunchJobDetail(Number(button.dataset.launchDetail));
    else if (button.dataset.launchCancel) cancelLaunchJob(Number(button.dataset.launchCancel));
    else if (button.dataset.launchRetryFailed) retryFailedLaunchJob(Number(button.dataset.launchRetryFailed));
    else if (button.dataset.launchClone) cloneLaunchJob(Number(button.dataset.launchClone));
    else if (button.dataset.launchReset) resetLaunchJob(Number(button.dataset.launchReset));
    else if (button.dataset.launchDelete) deleteLaunchJob(Number(button.dataset.launchDelete));
    else if (button.dataset.cfTest || button.dataset.cfDelete || button.dataset.cfOpen) cfAction(button);
    else if (button.dataset.dnsEdit) editDnsRecord(button.dataset.dnsEdit);
    else if (button.dataset.dnsDelete) deleteDnsRecord(button.dataset.dnsDelete);
  });
}

async function bootstrap() {
  bindEvents();
  await loadGoogleLoginStatus();
  await exchangeGoogleCode();
  if (state.token) await startApp();
}

document.addEventListener("DOMContentLoaded", bootstrap);

/* BEGIN OCI-N&T V1.0 1.0-ux-freeze1
   Frontend-only final UX pass. No extra OCI/API polling. */
function ntUxAuditResultMeta(row) {
  const action = String(row?.action || "").trim().toUpperCase();
  const actionLabel = String(row?.action_label || "").trim();
  const detail = String(row?.detail || "");
  const detailLabel = String(row?.detail_label || "");
  const text = [action, actionLabel, detail, detailLabel].filter(Boolean).join(" ");

  const numericFailureKeys = [
    "failed", "failure", "failures", "error", "errors",
    "interrupted", "abnormal", "invalid"
  ];

  let explicitFailureCount = null;
  let hasPositiveCounter = false;

  for (const key of numericFailureKeys) {
    const match = text.match(new RegExp("(?:^|[,;\\s])" + key + "\\s*[=:]\\s*(\\d+)", "i"));
    if (!match) continue;
    const value = Number(match[1]);
    explicitFailureCount = Math.max(explicitFailureCount ?? 0, value);
  }

  for (const key of ["success", "succeeded", "completed", "total"]) {
    if (new RegExp("(?:^|[,;\\s])" + key + "\\s*[=:]\\s*\\d+", "i").test(text)) {
      hasPositiveCounter = true;
      break;
    }
  }

  // success=2,failed=0：明确视为正常，避免仅因字段名 failed 被误判。
  if (explicitFailureCount === 0 && hasPositiveCounter) {
    return { key: "normal", label: "正常", tone: "muted" };
  }

  if (Number(explicitFailureCount || 0) > 0) {
    return { key: "failure", label: "失败 / 异常", tone: "bad" };
  }

  // 只识别明确的失败动作，不再对任意包含 FAIL 字样的文本做宽泛判断。
  if (
    /(?:^|_)(FAILED|FAILURE|ERROR|EXCEPTION|DENIED|REJECTED|INVALID|TIMEOUT|UNAVAILABLE|INTERRUPTED)(?:_|$)/.test(action)
    || /(失败|错误|异常|拒绝|超时|中断)/.test(actionLabel + " " + detailLabel)
  ) {
    return { key: "failure", label: "失败 / 异常", tone: "bad" };
  }

  if (
    /(?:^|_)(CREATED|UPDATED|DELETED|SAVED|TESTED|CHECKED|READ|SYNCED|ENABLED|DISABLED|STARTED|COMPLETED|CANCELLED|RESET|LOGIN_SUCCESS)(?:_|$)/.test(action)
    || /(成功|已完成|正常|已保存|已创建|已删除|已更新|已同步|检测通过)/.test(actionLabel + " " + detailLabel)
  ) {
    return { key: "normal", label: "正常", tone: "muted" };
  }

  return { key: "normal", label: "正常", tone: "muted" };
}
function ntUxAuditObjectKey(row) { return String(row?.resource_type || "OTHER").trim().toUpperCase() || "OTHER"; }
function ntUxAuditObjectLabel(value) {
  const labels={OCI_ACCOUNT:"OCI 账户",ACCOUNT:"账户",OCI_INSTANCE:"OCI 实例",INSTANCE:"实例",PUBLIC_IP:"公网 IP",PRIVATE_IP:"私网 IP",VNIC:"VNIC",BOOT_VOLUME:"启动卷",PROXY_PROFILE:"代理",PROXY:"代理",CLOUDFLARE_ACCOUNT:"Cloudflare",DNS_RECORD:"DNS 记录",TASK:"任务",MANUAL_TASK:"任务",BACKUP:"备份",SYSTEM:"系统",SETTINGS:"系统设置",SESSION:"登录会话",USER:"用户",OTHER:"其他"};
  return labels[value] || String(value || "OTHER").replaceAll("_"," ");
}
function ntUxEnsureAuditFilters() {
  const grid=document.querySelector("#audit-page .audit-filter-grid"); const searchLabel=$("audit-search")?.closest("label");
  if(!grid||!searchLabel)return;
  if(!$("audit-result-filter")){const label=document.createElement("label");label.className="audit-result-filter-field";label.title="当前日志表没有独立结果字段，此筛选仅根据操作名称和详情文本进行本地归类。";label.innerHTML='<span>结果</span><select id="audit-result-filter"><option value="">全部结果</option><option value="failure">失败 / 异常</option><option value="normal">正常</option></select>';grid.insertBefore(label,searchLabel);}
  if(!$("audit-object-filter")){const label=document.createElement("label");label.className="audit-object-filter-field";label.innerHTML='<span>对象</span><select id="audit-object-filter"><option value="">全部对象</option></select>';grid.insertBefore(label,searchLabel);}
}
function ntUxRefreshAuditObjectOptions() {
  ntUxEnsureAuditFilters(); const select=$("audit-object-filter"); if(!select)return; const previous=select.value;
  const values=[...new Set((state.auditRows||[]).map(ntUxAuditObjectKey).filter(Boolean))].sort((a,b)=>ntUxAuditObjectLabel(a).localeCompare(ntUxAuditObjectLabel(b),"zh-CN"));
  const frag=document.createDocumentFragment(); const all=document.createElement("option");all.value="";all.textContent="全部对象";frag.appendChild(all);
  for(const value of values){const option=document.createElement("option");option.value=value;option.textContent=ntUxAuditObjectLabel(value);frag.appendChild(option);} select.replaceChildren(frag); if(values.includes(previous))select.value=previous;
}
const ntUxOriginalGetFilteredAuditRows=getFilteredAuditRows;
getFilteredAuditRows=function ntUxFilteredAuditRows(){const base=ntUxOriginalGetFilteredAuditRows();const result=$("audit-result-filter")?.value||"";const object=$("audit-object-filter")?.value||"";return base.filter(row=>(!result||ntUxAuditResultMeta(row).key===result)&&(!object||ntUxAuditObjectKey(row)===object));};
function ntUxAuditFailureReason(row){if(ntUxAuditResultMeta(row).key!=="failure")return "";return String(row?.detail_label||row?.detail||row?.action_label||row?.action||"未记录详细原因");}
async function ntUxCopyText(value){const text=String(value||"");if(!text)return false;try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return true;}}catch(_){}const box=document.createElement("textarea");box.value=text;box.setAttribute("readonly","");box.style.position="fixed";box.style.opacity="0";document.body.appendChild(box);box.select();let ok=false;try{ok=document.execCommand("copy");}catch(_){}box.remove();return ok;}
function ntUxEnhanceAuditRows(){
  ntUxRefreshAuditObjectOptions();const table=$("audit-list")?.querySelector(".audit-table");if(!table)return;const data=getFilteredAuditRows();const rows=[...(table.tBodies[0]?.rows||[])];
  rows.forEach((tr,index)=>{if(tr.dataset.ntUxAudit==="1")return;const row=data[index];if(!row)return;tr.dataset.ntUxAudit="1";const result=ntUxAuditResultMeta(row);tr.classList.add(`audit-result-${result.key}`);
    const actionCell=tr.cells[4];if(actionCell&&!actionCell.querySelector(".audit-inferred-result")){const badge=document.createElement("span");badge.className=`badge ${result.tone} audit-inferred-result`;badge.textContent=result.label;badge.title="由当前本地审计记录的操作名称和详情推断";actionCell.prepend(badge);}
    const detailCell=tr.cells[6];if(!detailCell)return;const detail=String(row.detail_label||row.detail||"—");const existing=detailCell.querySelector(".audit-detail");if(existing&&detail.length>88){existing.remove();const details=document.createElement("details");details.className="audit-detail-fold";const summary=document.createElement("summary");summary.textContent=detail.slice(0,74)+"…";const body=document.createElement("div");body.className="audit-detail-full";body.textContent=detail;details.append(summary,body);detailCell.prepend(details);}const reason=ntUxAuditFailureReason(row);if(reason&&!detailCell.querySelector("[data-nt-audit-copy]")){const button=document.createElement("button");button.type="button";button.className="button small ghost audit-copy-reason";button.dataset.ntAuditCopy=String(index);button.textContent="复制原因";button.title="复制当前失败/异常记录的详情";detailCell.appendChild(button);}
  });
}
function ntUxTaskErrorSummary(task){const chunks=[];const categories=task?.error_categories;if(categories&&typeof categories==="object"&&!Array.isArray(categories)){for(const [category,count] of Object.entries(categories)){const n=Number(count||0);if(!n)continue;let label=category;try{label=taskErrorCategoryLabel(category);}catch(_){}chunks.push(`${label} ${n}`);}}if(!chunks.length&&task?.error_category){let label=String(task.error_category);try{label=taskErrorCategoryLabel(task.error_category);}catch(_){}chunks.push(label);}if(!chunks.length&&Number(task?.failed||0)>0)chunks.push(`失败项 ${Number(task.failed)}`);if(Number(task?.interrupted||0)>0)chunks.push(`中断 ${Number(task.interrupted)}`);return chunks.slice(0,2).join(" · ");}
const ntUxOriginalFilteredTasks=filteredTasks;
filteredTasks=function ntUxPriorityFilteredTasks(){const rows=ntUxOriginalFilteredTasks().slice();const score=task=>task?.is_active?30:["PARTIAL","FAILED","INTERRUPTED"].includes(String(task?.status||"").toUpperCase())?20:String(task?.status||"").toUpperCase()==="COMPLETED"?0:10;return rows.sort((a,b)=>{const p=score(b)-score(a);if(p)return p;const at=Date.parse(a?.updated_at||a?.created_at||"")||0;const bt=Date.parse(b?.updated_at||b?.created_at||"")||0;return bt!==at?bt-at:Number(b?.id||0)-Number(a?.id||0);});};
function ntUxEnhanceTaskRows(){const table=$("task-list")?.querySelector(".task-table");if(!table)return;const data=filteredTasks();const rows=[...(table.tBodies[0]?.rows||[])];rows.forEach((tr,index)=>{if(tr.dataset.ntUxTask==="1")return;const task=data[index];if(!task)return;tr.dataset.ntUxTask="1";const status=String(task.status||"").toUpperCase();if(task.is_active)tr.classList.add("task-row-active");else if(["PARTIAL","FAILED","INTERRUPTED"].includes(status))tr.classList.add("task-row-problem");else if(status==="COMPLETED")tr.classList.add("task-row-completed");const summary=ntUxTaskErrorSummary(task);const titleCell=tr.cells[1];if(summary&&titleCell&&!titleCell.querySelector(".task-inline-error")){const line=document.createElement("span");line.className="task-inline-error";line.textContent=summary;line.title="任务错误分类摘要";titleCell.appendChild(line);}const retry=tr.querySelector("[data-task-retry]");if(retry){retry.classList.add("primary","task-retry-action");retry.title="仅重新执行失败或中断的项目";}});}
function ntUxProfileIdFromRow(tr){for(const button of tr.querySelectorAll("button")){for(const attr of button.attributes){if(attr.name.startsWith("data-")&&attr.name.includes("profile")&&/^\d+$/.test(String(attr.value||"")))return Number(attr.value);}}return 0;}

function ntUxShortLocalDate(value) {
  if (!value) return "";
  const full = String(fmtDate(value).replace(",", "") || "");
  let match = full.match(/^\d{4}[\/-](\d{2}[\/-]\d{2})\s+(\d{2}:\d{2})/);
  if (match) return `${match[1]} ${match[2]}`;
  return full.length > 16 ? full.slice(-16) : full;
}

function ntUxEnhanceProxyRows(){const table=$("proxy-account-list")?.querySelector(".proxy-library-table");if(!table||!table.tHead?.rows?.length)return;const headers=[...table.tHead.rows[0].cells].map(cell=>cell.textContent.trim());const nameIndex=headers.findIndex(text=>text.includes("代理名称"));if(nameIndex<0)return;for(const tr of [...(table.tBodies[0]?.rows||[])]){if(tr.dataset.ntUxProxy==="1")continue;const profileId=ntUxProfileIdFromRow(tr);if(!profileId)continue;const profile=(state.proxyProfiles||[]).find(item=>Number(item.id)===profileId);if(!profile)continue;tr.dataset.ntUxProxy="1";const bound=(state.accounts||[]).filter(account=>Number(account.proxy_profile_id||0)===profileId).length;const cell=tr.cells[nameIndex];if(!cell||cell.querySelector(".proxy-cache-meta"))continue;const meta=document.createElement("div");meta.className="proxy-cache-meta";const binding=document.createElement("span");binding.className=`proxy-binding-chip ${bound?"is-used":"is-idle"}`;binding.textContent=`绑定 ${bound}`;binding.title=bound?`当前有 ${bound} 个 OCI 租户引用此代理`:"当前没有 OCI 租户引用此代理";const checked=document.createElement("span");checked.className="proxy-last-check";checked.textContent=profile.last_test_at?`检测 ${ntUxShortLocalDate(profile.last_test_at)}`:"从未检测";checked.title=profile.last_test_at?`最后检测时间：${fmtDate(profile.last_test_at).replace(",","")}`:"尚无本地检测记录";meta.append(binding,checked);cell.appendChild(meta);}}
function ntUxScheduleEnhance(fn){let pending=false;return()=>{if(pending)return;pending=true;queueMicrotask(()=>{pending=false;fn();});};}
function ntUxInstallObserver(target,enhancer){if(!target||target.dataset.ntUxObserver==="1")return;target.dataset.ntUxObserver="1";const scheduled=ntUxScheduleEnhance(enhancer);new MutationObserver(scheduled).observe(target,{childList:true,subtree:true});scheduled();}
function ntUxFreezeInit(){
  ntUxEnsureAuditFilters();
  $("audit-result-filter")?.addEventListener("change",()=>renderAudit());$("audit-object-filter")?.addEventListener("change",()=>renderAudit());
  $("audit-filter-reset")?.addEventListener("click",()=>{if($("audit-result-filter"))$("audit-result-filter").value="";if($("audit-object-filter"))$("audit-object-filter").value="";renderAudit();});
  $("audit-list")?.addEventListener("click",async event=>{const button=event.target.closest("[data-nt-audit-copy]");if(!button)return;const row=getFilteredAuditRows()[Number(button.dataset.ntAuditCopy||-1)];const reason=ntUxAuditFailureReason(row);if(!reason)return;const ok=await ntUxCopyText(reason);toast(ok?"失败原因已复制":"复制失败，请手动复制",ok?"good":"bad");});
  ntUxInstallObserver($("audit-list"),ntUxEnhanceAuditRows);ntUxInstallObserver($("task-list"),ntUxEnhanceTaskRows);ntUxInstallObserver($("proxy-account-list"),ntUxEnhanceProxyRows);
  ntUxEnhanceAuditRows();ntUxEnhanceTaskRows();ntUxEnhanceProxyRows();
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",ntUxFreezeInit,{once:true});else queueMicrotask(ntUxFreezeInit);
/* END OCI-N&T V1.0 1.0-ux-freeze1 */

/* OCI-N&T V1.0 1.0-ux-freeze1-fix1: audit result inference + compact proxy check time */


/* BEGIN OCI-N&T V1.0.4 1.0.4-launch-key-list1 */
const ntLaunchGeneratedKeys = new Map();
let ntLaunchEnhanceQueued = false;

function ntLaunchBytesFromB64Url(value) {
  let text = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  while (text.length % 4) text += "=";
  const raw = atob(text);
  return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
}

function ntLaunchU32(value) {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, value >>> 0, false);
  return out;
}

function ntLaunchConcat(...parts) {
  const length = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(length);
  let offset = 0;
  parts.forEach((part) => {
    out.set(part, offset);
    offset += part.length;
  });
  return out;
}

function ntLaunchSshString(bytes) {
  return ntLaunchConcat(ntLaunchU32(bytes.length), bytes);
}

function ntLaunchMpint(bytes) {
  let value = bytes;
  while (value.length > 1 && value[0] === 0 && !(value[1] & 0x80)) {
    value = value.slice(1);
  }
  if (value.length && (value[0] & 0x80)) {
    value = ntLaunchConcat(new Uint8Array([0]), value);
  }
  return ntLaunchSshString(value);
}

function ntLaunchB64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function ntLaunchPem(bytes, label = "PRIVATE KEY") {
  const base64 = ntLaunchB64(bytes);
  const lines = base64.match(/.{1,64}/g) || [];
  return `-----BEGIN ${label}-----\n${lines.join("\n")}\n-----END ${label}-----\n`;
}

function ntLaunchSafeName(value) {
  return String(value || "OCI-NT")
    .trim()
    .replace(/[\\/:*?"<>|\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48) || "OCI-NT";
}

function ntLaunchDownloadPrivateKey(text, filename) {
  const blob = new Blob([text], { type: "application/x-pem-file" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

async function ntLaunchGenerateSshKey(textarea, button, status) {
  if (!window.isSecureContext || !window.crypto?.subtle) {
    throw new Error("当前浏览器环境不支持安全密钥生成，请通过 HTTPS 打开 OCI-N&T");
  }

  button.disabled = true;
  const original = button.textContent;
  button.textContent = "正在生成 RSA-3072…";
  status.textContent = "密钥正在浏览器本地生成，私钥不会上传服务器。";

  try {
    const keyPair = await crypto.subtle.generateKey(
      {
        name: "RSASSA-PKCS1-v1_5",
        modulusLength: 3072,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
      },
      true,
      ["sign", "verify"],
    );

    const jwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
    const exponent = ntLaunchBytesFromB64Url(jwk.e);
    const modulus = ntLaunchBytesFromB64Url(jwk.n);
    const type = new TextEncoder().encode("ssh-rsa");
    const publicBlob = ntLaunchConcat(
      ntLaunchSshString(type),
      ntLaunchMpint(exponent),
      ntLaunchMpint(modulus),
    );
    const publicKey = `ssh-rsa ${ntLaunchB64(publicBlob)} OCI-N&T`;

    const digest = new Uint8Array(
      await crypto.subtle.digest("SHA-256", publicBlob),
    );
    const fingerprint = `SHA256:${ntLaunchB64(digest).replace(/=+$/g, "")}`;

    const privatePkcs8 = new Uint8Array(
      await crypto.subtle.exportKey("pkcs8", keyPair.privateKey),
    );
    let privatePem = ntLaunchPem(privatePkcs8);

    const now = new Date();
    const stamp = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
      "-",
      String(now.getHours()).padStart(2, "0"),
      String(now.getMinutes()).padStart(2, "0"),
      String(now.getSeconds()).padStart(2, "0"),
    ].join("");
    const tenantName = ntLaunchSafeName(
      document.querySelector("#tenant-name")?.textContent || `tenant-${state.tenantId || "unknown"}`,
    );
    const filename = `OCI-N&T-${tenantName}-${stamp}.pem`;

    textarea.value = publicKey;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));

    const tenantKey = String(state.tenantId || "current");
    ntLaunchGeneratedKeys.set(tenantKey, {
      publicKey,
      fingerprint,
      filename,
    });

    ntLaunchDownloadPrivateKey(privatePem, filename);
    privatePem = "";

    status.innerHTML =
      `<strong>已自动写入公钥</strong>` +
      `<span>指纹 ${esc(fingerprint)}</span>` +
      `<span>私钥已一次性下载：${esc(filename)}</span>`;
    button.textContent = "重新生成并下载新私钥";

    try {
      if (typeof updateLaunchSubmitState === "function") updateLaunchSubmitState();
    } catch (_) {}
  } finally {
    button.disabled = false;
    if (button.textContent === "正在生成 RSA-3072…") {
      button.textContent = original;
    }
  }
}

function ntLaunchEnhanceSshControl() {
  const textarea = document.querySelector("#tenant-launch-ssh");
  if (!textarea || textarea.dataset.ntSshEnhanced === "1") return;
  textarea.dataset.ntSshEnhanced = "1";
  textarea.setAttribute("autocomplete", "off");

  const label = textarea.closest("label");
  const title = label?.querySelector(":scope > span");
  if (title) title.textContent = "SSH 公钥";

  const box = document.createElement("div");
  box.className = "nt-ssh-auto-card";
  box.innerHTML = `
    <div class="nt-ssh-auto-copy">
      <strong>自动 SSH 密钥</strong>
      <span>浏览器生成 RSA-3072。公钥自动写入 OCI；私钥只下载一次，不上传、不保存、不通过 Telegram 发送。</span>
    </div>
    <div class="nt-ssh-auto-actions">
      <button class="button primary nt-ssh-generate" type="button">自动生成并下载私钥</button>
      <span class="nt-ssh-status">尚未生成；也可以继续粘贴你自己的 SSH 公钥。</span>
    </div>
  `;
  if (label?.parentElement) {
    label.insertAdjacentElement("afterend", box);
  } else {
    textarea.insertAdjacentElement("afterend", box);
  }

  const button = box.querySelector(".nt-ssh-generate");
  const status = box.querySelector(".nt-ssh-status");
  const tenantKey = String(state.tenantId || "current");
  const cached = ntLaunchGeneratedKeys.get(tenantKey);

  if (cached && !textarea.value.trim()) {
    textarea.value = cached.publicKey;
    status.innerHTML =
      `<strong>已恢复本次页面生成的公钥</strong>` +
      `<span>指纹 ${esc(cached.fingerprint)}</span>` +
      `<span>请使用此前已下载的私钥：${esc(cached.filename)}</span>`;
    button.textContent = "重新生成并下载新私钥";
  }

  textarea.addEventListener("input", () => {
    const current = ntLaunchGeneratedKeys.get(String(state.tenantId || "current"));
    if (current && textarea.value.trim() !== current.publicKey) {
      ntLaunchGeneratedKeys.delete(String(state.tenantId || "current"));
      status.textContent = "当前为手动公钥；系统不会保存对应私钥。";
      button.textContent = "生成新密钥并下载私钥";
    }
  });

  button.addEventListener("click", async () => {
    try {
      await ntLaunchGenerateSshKey(textarea, button, status);
      if (typeof toast === "function") {
        toast("SSH 公钥已自动写入，私钥已开始下载", "good");
      }
    } catch (error) {
      status.textContent = error?.message || "SSH 密钥生成失败";
      if (typeof toast === "function") {
        toast(error?.message || "SSH 密钥生成失败", "bad");
      }
    }
  });
}

function ntLaunchEnhanceJobLists() {
  document.querySelectorAll(".launch-job-list").forEach((list) => {
    if (list.querySelector(":scope > .nt-launch-job-head")) return;
    const head = document.createElement("div");
    head.className = "nt-launch-job-head";
    head.innerHTML = "<span>任务 / 租户</span><span>进度 / 重试</span><span>操作</span>";
    list.prepend(head);
  });
}

function ntLaunchRunEnhancers() {
  ntLaunchEnhanceSshControl();
  ntLaunchEnhanceJobLists();
}

function ntLaunchQueueEnhancers() {
  if (ntLaunchEnhanceQueued) return;
  ntLaunchEnhanceQueued = true;
  queueMicrotask(() => {
    ntLaunchEnhanceQueued = false;
    ntLaunchRunEnhancers();
  });
}

ntLaunchRunEnhancers();
new MutationObserver(ntLaunchQueueEnhancers).observe(document.body, {
  childList: true,
  subtree: true,
});
/* END OCI-N&T V1.0.4 1.0.4-launch-key-list1 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-analytics-split-a2-import-fix1 */
const ntAnalyticsState = {
  mode: "",
  costOnlyPositive: true,
  lastCost: null,
  lastMetrics: null,
  metricsLoading: false,
  metricsRequestKey: "",
  metricsLastStartedAt: 0,
};

const ntAnalyticsNativeFetch = window.fetch.bind(window);

function ntAnalyticsEsc(value) {
  if (typeof esc === "function") return esc(String(value ?? ""));
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function ntAnalyticsNumber(value, digits = 3) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function ntAnalyticsMoney(value, currency = "USD") {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "—";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }).format(n);
  } catch (_) {
    return `$${n.toFixed(4)}`;
  }
}

function ntAnalyticsDate(value) {
  const text = String(value || "");
  const match = text.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : (text || "—");
}

function ntAnalyticsLocalDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function ntAnalyticsServiceCategory(service) {
  const value = String(service || "").toLowerCase();
  if (value.includes("compute")) return "compute";
  if (value.includes("storage")) return "storage";
  if (value.includes("network")) return "network";
  return "other";
}

function ntAnalyticsServiceLabel(key) {
  return ({ compute: "计算", storage: "存储", network: "网络", other: "其他" })[key] || "其他";
}

function ntAnalyticsMetricValue(item) {
  if (typeof item === "number") return item;
  if (Array.isArray(item) && item.length > 1) return Number(item[1]);
  return Number(item?.value ?? item?.average ?? item?.sum ?? item?.maximum ?? item?.minimum);
}

function ntAnalyticsMetricTime(item, index) {
  if (Array.isArray(item)) return String(item[0] ?? index);
  return String(item?.timestamp ?? item?.time ?? item?.datetime ?? item?.date ?? index);
}

function ntAnalyticsMetricPoints(series) {
  if (!Array.isArray(series)) return [];
  const out=[];
  for (const item of series) {
    if (Array.isArray(item?.aggregated_datapoints)) {
      for (const point of item.aggregated_datapoints) {
        const value=ntAnalyticsMetricValue(point);
        if (Number.isFinite(value)) out.push({ time: ntAnalyticsMetricTime(point,out.length), value });
      }
      continue;
    }
    const value=ntAnalyticsMetricValue(item);
    if (Number.isFinite(value)) out.push({ time: ntAnalyticsMetricTime(item,out.length), value });
  }
  return out;
}

function ntAnalyticsSparkline(points) {
  if (!points.length) return '<div class="nt-analytics-empty compact">暂无趋势数据</div>';
  const values=points.map((p)=>p.value);
  const min=Math.min(...values); const max=Math.max(...values); const span=max-min || 1;
  const width=460, height=132, px=14, py=16;
  const x=(i)=>points.length===1 ? width/2 : px + (width-2*px)*i/(points.length-1);
  const y=(v)=>py + (height-2*py)*(1-(v-min)/span);
  const coords=points.map((p,i)=>`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  return `<svg class="nt-analytics-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><line x1="${px}" y1="${height-py}" x2="${width-px}" y2="${height-py}" class="nt-analytics-axis"/><polyline points="${coords}" class="nt-analytics-spark-line"/></svg>`;
}

function ntAnalyticsHideNativeOutput(card) {
  if (!card) return;
  card.querySelectorAll("pre, .json-output, .query-output, .rc-json, .insight-output").forEach((el) => {
    el.classList.add("nt-analytics-native-output-hidden");
  });
  for (const el of card.querySelectorAll("div,code")) {
    if (el.closest(".nt-analytics-shell")) continue;
    const text=(el.textContent || "").trim();
    if (el.children.length <= 1 && (text === "尚未查询" || (text.startsWith("{") && text.length > 30))) {
      el.classList.add("nt-analytics-native-output-hidden");
    }
  }
}

function ntAnalyticsNativeCard(label) {
  const content=document.querySelector("#tenant-content");
  if (!content) return null;
  const button=[...content.querySelectorAll("button")].find((el)=>(el.textContent || "").trim().includes(label));
  return button?.closest("section,article,.panel") || button?.parentElement || null;
}

function ntAnalyticsSetActiveTab(mode) {
  document.querySelectorAll("#tenant-tabs [data-tenant-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tenantTab === mode);
  });
}

function ntAnalyticsSetHeading(mode) {
  const content=document.querySelector("#tenant-content");
  if (!content) return;
  const wanted=mode === "costs" ? "账户费用" : "实例指标";
  const copy=mode === "costs"
    ? "查询当前 OCI 租户/账户整体费用；只有点击查询时才访问 Usage API。"
    : "进入页面后自动查询当前实例的 OCI Monitoring 指标；切换实例或时间范围会自动刷新。";
  for (const el of content.querySelectorAll("h1,h2,h3")) {
    if ((el.textContent || "").trim() === "流量与费用") { el.textContent=wanted; break; }
  }
  const firstP=content.querySelector(":scope > p");
  if (firstP && /OCI Monitoring|Usage API|后台|采集|查询/.test(firstP.textContent || "")) firstP.textContent=copy;
}

function ntAnalyticsEnsureShell(mode, card) {
  const content=document.querySelector("#tenant-content");
  if (!content || !card) return null;
  content.querySelectorAll(".nt-analytics-shell").forEach((el)=>el.remove());
  const shell=document.createElement("section");
  shell.id=mode === "costs" ? "nt-account-cost-shell" : "nt-instance-metrics-shell";
  shell.className=`nt-analytics-shell nt-analytics-${mode}`;
  shell.innerHTML=mode === "costs"
    ? `<div class="nt-analytics-placeholder"><strong>账户费用</strong><span>选择时间范围并点击“查询费用”后显示费用卡片、每日趋势和区域明细。</span></div>`
    : `<div class="nt-analytics-placeholder"><strong>实例指标</strong><span>进入页面后自动显示指标卡片、趋势和数据点；切换实例或时间范围会自动刷新。</span></div>`;
  card.insertAdjacentElement("afterend", shell);
  return shell;
}

function ntAnalyticsAddRangeBar(mode, card) {
  if (!card || card.querySelector(".nt-analytics-rangebar")) return;
  const queryLabel=mode === "costs" ? "查询费用" : "查询指标";
  const queryButton=[...card.querySelectorAll("button")].find((el)=>(el.textContent || "").trim().includes(queryLabel));
  if (!queryButton) return;
  const bar=document.createElement("div");
  bar.className="nt-analytics-rangebar";
  if (mode === "costs") {
    bar.innerHTML=`<span>时间范围</span><button class="button small" type="button" data-nt-cost-range="today">今日</button><button class="button small active" type="button" data-nt-cost-range="month">本月</button><button class="button small" type="button" data-nt-cost-range="custom">自定义</button><em>费用范围：整个当前 OCI 账户</em>`;
  } else {
    bar.innerHTML=`<span>时间范围</span><button class="button small" type="button" data-nt-metric-hours="1">1 小时</button><button class="button small active" type="button" data-nt-metric-hours="24">24 小时</button><button class="button small" type="button" data-nt-metric-hours="168">7 天</button><em>指标范围：当前所选实例</em>`;
  }
  const anchor=queryButton.closest("form") || queryButton;
  anchor.parentElement?.insertBefore(bar, anchor);

  bar.addEventListener("click", (event) => {
    const costButton=event.target.closest("[data-nt-cost-range]");
    if (costButton) {
      bar.querySelectorAll("[data-nt-cost-range]").forEach((b)=>b.classList.toggle("active", b===costButton));
      const dates=[...card.querySelectorAll('input[type="date"]')];
      if (costButton.dataset.ntCostRange === "custom") { dates[0]?.focus(); return; }
      const now=new Date(); const tomorrow=new Date(now); tomorrow.setDate(tomorrow.getDate()+1);
      const start=new Date(now);
      if (costButton.dataset.ntCostRange === "month") start.setDate(1);
      if (dates[0]) dates[0].value=ntAnalyticsLocalDate(start);
      if (dates[1]) dates[1].value=ntAnalyticsLocalDate(tomorrow);
      return;
    }
    const metricButton=event.target.closest("[data-nt-metric-hours]");
    if (metricButton) {
      bar.querySelectorAll("[data-nt-metric-hours]").forEach((b)=>b.classList.toggle("active", b===metricButton));
      const hoursInput=[...card.querySelectorAll('input[type="number"]')].find((input)=>Number(input.min || 0) <= 24);
      if (hoursInput) {
        hoursInput.value=metricButton.dataset.ntMetricHours;
        hoursInput.dispatchEvent(new Event("change", { bubbles:true }));
        ntAnalyticsScheduleAutoMetrics(220);
      }
    }
  });
}


let ntMetricsAutoTimer = null;
let ntMetricsAutoSequence = 0;
const ntMetricsCacheRequests = new Map();

function ntAnalyticsLoadCachedMetrics(card) {
  const account = selectedTenant();
  const instanceSelect = card?.querySelector("#rc-metric-instance");
  const hoursInput = card?.querySelector("#rc-metric-hours");

  if (!account || !instanceSelect?.value || !hoursInput?.value) return;

  const instance = state.instances.find(
    (item) => String(item.id) === String(instanceSelect.value)
  );

  if (!instance) return;

  const key = [
    account.id,
    instance.id,
    hoursInput.value,
  ].join(":");

  const lastCacheRead = Number(ntMetricsCacheRequests.get(key) || 0);
  if (Date.now() - lastCacheRead < 15000) return;
  ntMetricsCacheRequests.set(key, Date.now());

  const query = new URLSearchParams({
    region: instance.region || account.region || account.home_region_key || "",
    compartment_id: instance.compartment_id || account.tenancy_ocid || "",
    hours: hoursInput.value,
    cached: "true",
    direct: "true",
  });

  api(
    `/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/metrics?${query}`
  )
    .then((data) => {
      if (
        ntAnalyticsState.mode === "metrics" &&
        Number(state.tenantId) === Number(account.id)
      ) {
        ntAnalyticsState.lastMetrics = data;
        ntAnalyticsRenderMetrics(data);
      }
    })
    .catch(() => {
      ntMetricsCacheRequests.delete(key);
    });
}

function ntAnalyticsScheduleAutoMetrics(delay = 180, startedAt = Date.now()) {
  clearTimeout(ntMetricsAutoTimer);

  const sequence = ++ntMetricsAutoSequence;
  const tenantId = Number(state.tenantId);
  const maxWaitMs = 20000;

  ntMetricsAutoTimer = setTimeout(() => {
    if (sequence !== ntMetricsAutoSequence) return;
    if (Number(state.tenantId) !== tenantId) return;
    if (ntAnalyticsState.mode !== "metrics") return;

    const card =
      ntAnalyticsNativeCard("查询指标") ||
      ntAnalyticsNativeCard("刷新指标");

    if (!card || !card.isConnected) return;

    const button = [...card.querySelectorAll("button")].find((element) => {
      const label = (element.textContent || "").trim();
      return label.includes("查询指标") || label.includes("刷新指标");
    });

    const instanceSelect = card.querySelector("select");

    const hoursInput = [...card.querySelectorAll('input[type="number"]')]
      .find((input) => Number(input.min || 0) <= 24);

    const ready = Boolean(
      button &&
      !button.disabled &&
      instanceSelect?.value &&
      hoursInput?.value
    );

    if (!ready) {
      if (Date.now() - startedAt < maxWaitMs) {
        ntAnalyticsScheduleAutoMetrics(350, startedAt);
      } else if (typeof toast === "function") {
        toast("实例指标暂未就绪，可稍后点击“刷新指标”", "bad");
      }
      return;
    }

    const requestKey = `${tenantId}:${instanceSelect.value}:${hoursInput.value}`;
    if (
      ntAnalyticsState.metricsLoading &&
      ntAnalyticsState.metricsRequestKey === requestKey &&
      Date.now() - ntAnalyticsState.metricsLastStartedAt < 30000
    ) return;
    if (
      ntAnalyticsState.metricsRequestKey === requestKey &&
      Date.now() - ntAnalyticsState.metricsLastStartedAt < 3000
    ) return;
    ntAnalyticsState.metricsLoading = true;
    ntAnalyticsState.metricsRequestKey = requestKey;
    ntAnalyticsState.metricsLastStartedAt = Date.now();

    const shell = document.querySelector("#nt-instance-metrics-shell");
    if (shell) {
      shell.innerHTML = `
        <div class="nt-metrics-loading-head">
          <div>
            <strong>正在读取实例指标</strong>
            <span>CPU、内存和网络流量正在并行查询……</span>
          </div>
          <span class="nt-metrics-loading-badge">OCI Monitoring</span>
        </div>
        <div class="nt-metrics-skeleton-grid">
          ${["CPU 使用率", "内存使用率", "网络接收流量", "网络发送流量"]
            .map((label) => `
              <article>
                <span>${label}</span>
                <i></i>
                <small>正在读取</small>
              </article>
            `).join("")}
        </div>
      `;
    }

    button.click();
  }, delay);
}

function ntAnalyticsPrepare(mode) {
  const content=document.querySelector("#tenant-content");
  if (!content) return false;
  const metricsCard=ntAnalyticsNativeCard("查询指标");
  const costsCard=ntAnalyticsNativeCard("查询费用");
  const chosen=mode === "costs" ? costsCard : metricsCard;
  const other=mode === "costs" ? metricsCard : costsCard;
  if (!chosen) {
    return false;
  }
  if (other) other.hidden=true;
  chosen.hidden=false;
  chosen.classList.add("nt-analytics-native-card");
  ntAnalyticsHideNativeOutput(chosen);
  ntAnalyticsSetHeading(mode);
  ntAnalyticsSetActiveTab(mode);
  ntAnalyticsCompactControlsA3(mode, chosen);
  ntAnalyticsEnsureShell(mode, chosen);
  if (mode === "costs" && ntAnalyticsState.lastCost) ntAnalyticsRenderCost(ntAnalyticsState.lastCost);
  if (mode === "metrics" && ntAnalyticsState.lastMetrics) ntAnalyticsRenderMetrics(ntAnalyticsState.lastMetrics);
  if (mode === "metrics") ntAnalyticsLoadCachedMetrics(chosen);

  if (mode === "metrics") {
    const nativeQueryButton = [...chosen.querySelectorAll("button")].find((element) =>
      (element.textContent || "").trim().includes("查询指标")
    );

    if (nativeQueryButton) {
      nativeQueryButton.textContent = "刷新指标";
      nativeQueryButton.title = "页面会自动查询，也可点击手动刷新";
    }

    ntAnalyticsScheduleAutoMetrics(220);
  }
  return true;
}


document.querySelector("#tenant-content")?.addEventListener("change", (event) => {
  if (ntAnalyticsState.mode !== "metrics") return;

  const control = event.target.closest(
    "#rc-metric-instance, #rc-metric-hours, " +
    ".nt-analytics-instance-select-a3, .nt-analytics-hours-a3"
  );

  if (!control) return;
  ntAnalyticsScheduleAutoMetrics(260);
});

function ntAnalyticsOpen(mode) {
  ntAnalyticsState.mode=mode;
  // Reuse the current V1.0.4 native Insights click path instead of depending on
  // a particular internal function name. The hidden native tab remains in DOM.
  const nativeTab=document.querySelector('#tenant-tabs [data-tenant-tab="insights"]');
  if (!nativeTab) {
    toast?.("未找到原生流量与费用入口", "bad");
    return;
  }
  nativeTab.click();
  // First entry may still be waiting for the tenant instance cache. Wait for the
  // native panel to exist instead of requiring the user to leave and re-enter.
  const tenantId = Number(state.tenantId);
  const startedAt = Date.now();
  const prepareWhenReady = () => {
    if (ntAnalyticsState.mode !== mode || Number(state.tenantId) !== tenantId) return;
    if (ntAnalyticsPrepare(mode)) return;
    if (Date.now() - startedAt < 20000) setTimeout(prepareWhenReady, 180);
    else if (typeof toast === "function") toast("实例指标加载超时，请点击“刷新指标”重试", "bad");
  };
  requestAnimationFrame(prepareWhenReady);
}

// Capture the two new tabs before the current delegated tenant-tab handler.
// The hidden native Insights button is then clicked so all existing request
// builders and tenant-scoped logic remain unchanged.
document.querySelector("#tenant-tabs")?.addEventListener("click", (event) => {
  const button=event.target.closest('[data-tenant-tab="metrics"],[data-tenant-tab="costs"]');
  if (!button) {
    if (event.target.closest('[data-tenant-tab]:not([data-tenant-tab="insights"])')) {
      ntAnalyticsState.mode="";
      ntAnalyticsState.metricsLoading=false;
      ntAnalyticsState.metricsRequestKey="";
      clearTimeout(ntMetricsAutoTimer);
      ntMetricsAutoSequence += 1;
    }
    return;
  }
  event.preventDefault();
  event.stopImmediatePropagation();
  ntAnalyticsOpen(button.dataset.tenantTab);
}, true);

function ntAnalyticsCostRows(data) {
  return Array.isArray(data?.items) ? data.items : [];
}

function ntAnalyticsCostAmount(item) {
  return Number(item?.computed_amount ?? item?.attributed_cost ?? 0) || 0;
}

function ntAnalyticsCostSummary(items) {
  const totals={ total:0, compute:0, storage:0, network:0, other:0 };
  for (const item of items) {
    const amount=ntAnalyticsCostAmount(item);
    const category=ntAnalyticsServiceCategory(item?.service);
    totals.total += amount;
    totals[category] += amount;
  }
  return totals;
}

function ntAnalyticsCostDaily(items) {
  const map=new Map();
  for (const item of items) {
    const date=ntAnalyticsDate(item?.time_usage_started);
    if (!map.has(date)) map.set(date,{ date, compute:0, storage:0, network:0, other:0 });
    const row=map.get(date);
    row[ntAnalyticsServiceCategory(item?.service)] += ntAnalyticsCostAmount(item);
  }
  return [...map.values()].sort((a,b)=>a.date.localeCompare(b.date));
}

function ntAnalyticsCostChart(daily) {
  if (!daily.length) return '<div class="nt-analytics-empty">当前查询范围没有费用趋势数据。</div>';
  const keys=["compute","storage","network","other"];
  const width=1000, height=300, left=58, right=22, top=24, bottom=42;
  const innerW=width-left-right, innerH=height-top-bottom;
  let max=0;
  for (const row of daily) for (const key of keys) max=Math.max(max, Number(row[key] || 0));
  max=max || 1;
  const x=(i)=>daily.length===1 ? left+innerW/2 : left+innerW*i/(daily.length-1);
  const y=(v)=>top+innerH-innerH*Number(v || 0)/max;
  const grid=[0,1,2,3,4].map((n)=>{
    const value=max*n/4, yy=y(value);
    return `<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" class="nt-analytics-grid-line"/><text x="${left-9}" y="${yy+4}" text-anchor="end" class="nt-analytics-axis-text">${ntAnalyticsNumber(value,2)}</text>`;
  }).join("");
  const paths=keys.map((key)=>{
    const points=daily.map((row,i)=>`${x(i).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
    return `<polyline points="${points}" class="nt-analytics-cost-line nt-line-${key}"/>`;
  }).join("");
  const step=Math.max(1,Math.ceil(daily.length/7));
  const labels=daily.map((row,i)=> (i%step===0 || i===daily.length-1) ? `<text x="${x(i)}" y="${height-15}" text-anchor="middle" class="nt-analytics-axis-text">${ntAnalyticsEsc(row.date.slice(5))}</text>` : "").join("");
  return `<div class="nt-analytics-chart-legend">${keys.map((key)=>`<span class="nt-legend-${key}">${ntAnalyticsServiceLabel(key)}</span>`).join("")}</div><svg class="nt-analytics-cost-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="每日费用趋势">${grid}${paths}${labels}</svg>`;
}

function ntAnalyticsCostTable(items, currency) {
  const rows=items
    .filter((item)=>!ntAnalyticsState.costOnlyPositive || ntAnalyticsCostAmount(item) > 0)
    .sort((a,b)=> ntAnalyticsDate(b?.time_usage_started).localeCompare(ntAnalyticsDate(a?.time_usage_started)) || ntAnalyticsCostAmount(b)-ntAnalyticsCostAmount(a));
  if (!rows.length) return '<div class="nt-analytics-empty">当前筛选条件下没有费用明细。</div>';
  return `<div class="nt-analytics-table-wrap"><table class="nt-analytics-table"><thead><tr><th>日期</th><th>服务</th><th>区域</th><th>用量</th><th>费用</th></tr></thead><tbody>${rows.slice(0,500).map((item)=>`<tr><td>${ntAnalyticsEsc(ntAnalyticsDate(item?.time_usage_started))}</td><td>${ntAnalyticsEsc(item?.service || "—")}</td><td><code>${ntAnalyticsEsc(item?.region || "—")}</code></td><td>${ntAnalyticsEsc(ntAnalyticsNumber(item?.computed_quantity ?? item?.attributed_usage,4))}</td><td><strong>${ntAnalyticsEsc(ntAnalyticsMoney(ntAnalyticsCostAmount(item), item?.currency || currency))}</strong></td></tr>`).join("")}</tbody></table></div>`;
}

function ntAnalyticsRenderCost(data) {
  const shell=document.querySelector("#nt-account-cost-shell");
  if (!shell || ntAnalyticsState.mode !== "costs") return;
  const items=ntAnalyticsCostRows(data);
  const totals=ntAnalyticsCostSummary(items);
  const daily=ntAnalyticsCostDaily(items);
  const currency=items.find((item)=>item?.currency)?.currency || "USD";
  const started=items.length ? ntAnalyticsDate(items.map((x)=>x.time_usage_started).sort()[0]) : "—";
  const ended=items.length ? ntAnalyticsDate(items.map((x)=>x.time_usage_ended).sort().slice(-1)[0]) : "—";
  shell.innerHTML=`
    <div class="nt-analytics-result-head"><div><h3>账户费用</h3><p>${ntAnalyticsEsc(started)} → ${ntAnalyticsEsc(ended)} · 当前 OCI 账户整体 · OCI Usage API</p></div><details><summary>原始 JSON</summary><pre>${ntAnalyticsEsc(JSON.stringify(data,null,2))}</pre></details></div>
    <div class="nt-analytics-cost-cards">
      <article class="total"><span>总费用</span><strong>${ntAnalyticsMoney(totals.total,currency)}</strong><small>当前查询范围</small></article>
      <article class="compute"><span>计算</span><strong>${ntAnalyticsMoney(totals.compute,currency)}</strong><small>Compute</small></article>
      <article class="storage"><span>存储</span><strong>${ntAnalyticsMoney(totals.storage,currency)}</strong><small>Storage</small></article>
      <article class="network"><span>网络</span><strong>${ntAnalyticsMoney(totals.network,currency)}</strong><small>Network</small></article>
      <article class="other"><span>其他</span><strong>${ntAnalyticsMoney(totals.other,currency)}</strong><small>Other</small></article>
    </div>
    <section class="nt-analytics-chart-card"><div class="nt-analytics-section-head"><div><strong>每日费用趋势</strong><small>${ntAnalyticsEsc(currency)}</small></div></div>${ntAnalyticsCostChart(daily)}</section>
    <section class="nt-analytics-detail-card"><div class="nt-analytics-section-head"><div><strong>费用明细</strong><small>账户级 service + region 数据，不把费用误归属到单个实例</small></div><button id="nt-account-cost-positive" class="button small ${ntAnalyticsState.costOnlyPositive ? "active" : ""}" type="button">仅显示费用 &gt; 0</button></div><div id="nt-account-cost-table">${ntAnalyticsCostTable(items,currency)}</div></section>`;
  shell.querySelector("#nt-account-cost-positive")?.addEventListener("click",()=>{
    ntAnalyticsState.costOnlyPositive=!ntAnalyticsState.costOnlyPositive;
    ntAnalyticsRenderCost(data);
  },{once:true});
  ntAnalyticsHideNativeOutput(ntAnalyticsNativeCard("查询费用"));
}

function ntAnalyticsMetricRows(metrics) {
  const defs=[
    ["CPU 使用率", "cpu"], ["内存使用率", "memory"], ["网络接收流量", "network_in"], ["网络发送流量", "network_out"],
  ];
  const rows=[];
  for (const [label,key] of defs) {
    for (const point of ntAnalyticsMetricPoints(metrics?.[key] || [])) rows.push({ label,key,...point });
  }
  return rows.sort((a,b)=>String(b.time).localeCompare(String(a.time)));
}

function ntAnalyticsMetricUnit(key) {
  if (key === "cpu" || key === "memory") return "%";
  if (key === "network_in" || key === "network_out") return "字节";
  return "";
}

function ntAnalyticsMetricDisplay(key, value) {
  if (value === null || value === undefined || value === "") return "—";

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";

  if (key === "cpu" || key === "memory") {
    return `${ntAnalyticsNumber(numeric, 2)}%`;
  }

  if (key === "network_in" || key === "network_out") {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let converted = Math.abs(numeric);
    let unitIndex = 0;

    while (converted >= 1024 && unitIndex < units.length - 1) {
      converted /= 1024;
      unitIndex += 1;
    }

    if (numeric < 0) converted = -converted;

    const digits = unitIndex === 0 ? 0 : 2;
    return `${ntAnalyticsNumber(converted, digits)} ${units[unitIndex]}`;
  }

  return ntAnalyticsNumber(numeric, 2);
}

function ntAnalyticsRenderMetrics(data) {
  const shell=document.querySelector("#nt-instance-metrics-shell");
  if (!shell || ntAnalyticsState.mode !== "metrics") return;
  const metrics=data?.metrics && typeof data.metrics === "object" ? data.metrics : {};
  const defs=[
    ["CPU 使用率","cpu"],["内存使用率","memory"],["网络接收流量","network_in"],["网络发送流量","network_out"],
  ];
  const groups=defs.map(([label,key])=>({label,key,points:ntAnalyticsMetricPoints(metrics[key] || [])}));
  const allEmpty=groups.every((g)=>!g.points.length);
  const rows=ntAnalyticsMetricRows(metrics);
  shell.innerHTML=`
    <div class="nt-analytics-result-head"><div><h3>实例指标 ${data?.from_cache ? '<span class="nt-metrics-cache-tag">缓存</span>' : '<span class="nt-metrics-live-tag">实时</span>'}</h3><p>${ntAnalyticsEsc(data?.region || "—")} · ${ntAnalyticsDate(data?.start_time)} → ${ntAnalyticsDate(data?.end_time)}${data?.from_cache && data?.cached_at ? ` · 缓存于 ${ntAnalyticsEsc(fmtDate(data.cached_at))}` : ""}</p></div><details><summary>原始 JSON</summary><pre>${ntAnalyticsEsc(JSON.stringify(data,null,2))}</pre></details></div>
    <div class="nt-analytics-metric-cards">${groups.map((g)=>{const latest=g.points.length ? g.points[g.points.length-1].value : null; const avg=g.points.length ? g.points.reduce((s,p)=>s+p.value,0)/g.points.length : null; return `<article class="metric-${g.key}"><span>${g.label}</span><strong>${ntAnalyticsMetricDisplay(g.key, latest)}</strong><small>${g.points.length ? `平均 ${ntAnalyticsMetricDisplay(g.key, avg)} · ${g.points.length} 个数据点` : "OCI 尚未生成数据"}</small></article>`;}).join("")}</div>
    <section class="nt-analytics-chart-card"><div class="nt-analytics-section-head"><div><strong>指标趋势</strong><small>每项指标独立缩放，避免不同单位互相误导</small></div></div>${allEmpty ? '<div class="nt-analytics-empty">OCI Monitoring 暂未返回指标数据。新实例通常需要一段时间才会产生监控数据；请等待约 15–30 分钟后重新查询。没有网络流量时，网络指标也可能暂时为空。</div>' : `<div class="nt-analytics-metric-trends">${groups.map((g)=>`<article><div><strong>${g.label}</strong><span>${g.points.length ? `${g.points.length} 点` : "无数据"}</span></div>${ntAnalyticsSparkline(g.points)}</article>`).join("")}</div>`}</section>
    <section class="nt-analytics-detail-card"><div class="nt-analytics-section-head"><div><strong>指标数据点</strong><small>最多显示最近 200 个数据点</small></div></div>${rows.length ? `<div class="nt-analytics-table-wrap"><table class="nt-analytics-table"><thead><tr><th>指标</th><th>时间</th><th>数值</th></tr></thead><tbody>${rows.slice(0,200).map((row)=>`<tr><td>${ntAnalyticsEsc(row.label)}</td><td><code>${ntAnalyticsEsc(row.time)}</code></td><td><strong>${ntAnalyticsEsc(ntAnalyticsMetricDisplay(row.key, row.value))}</strong></td></tr>`).join("")}</tbody></table></div>` : '<div class="nt-analytics-empty">暂无指标数据点。</div>'}</section>`;
  ntAnalyticsHideNativeOutput(ntAnalyticsNativeCard("查询指标"));
}

// Capture successful native API responses directly. We never inspect the page
// repeatedly: no observer loop, no interval loop, no 250ms poller.
window.fetch = async function ntAnalyticsFetch(input, init) {
  const response=await ntAnalyticsNativeFetch(input,init);
  try {
    const url=typeof input === "string" ? input : String(input?.url || "");
    const mode=ntAnalyticsState.mode;
    const isCost=mode === "costs" && /\/accounts\/\d+\/costs(?:\?|$)/.test(url);
    const isMetrics=mode === "metrics" && /\/accounts\/\d+\/instances\/[^/]+\/metrics(?:\?|$)/.test(url);
    const isCachedMetrics=isMetrics && /[?&]cached=true(?:&|$)/.test(url);
    if (isMetrics && !isCachedMetrics) {
      ntAnalyticsState.metricsLoading=false;
      if (!response.ok && ntAnalyticsState.mode === "metrics") {
        const shell=document.querySelector("#nt-instance-metrics-shell");
        if (shell) shell.innerHTML=`<div class="nt-analytics-empty nt-metrics-error"><strong>指标读取失败</strong><span>OCI Monitoring 响应异常（${response.status}），缓存数据不受影响。可稍后点击“刷新指标”重试。</span></div>`;
      }
    }
    if (response.ok && (isCost || isMetrics)) {
      response.clone().json().then((data)=>{
        if (isCost) ntAnalyticsState.lastCost=data;
        if (isMetrics) ntAnalyticsState.lastMetrics=data;
        // One one-shot callback after the native handler has painted its result.
        setTimeout(()=>{
          if (isCost && ntAnalyticsState.mode === "costs") ntAnalyticsRenderCost(data);
          if (isMetrics && ntAnalyticsState.mode === "metrics") ntAnalyticsRenderMetrics(data);
        },0);
      }).catch(()=>{});
    }
  } catch (_) {}
  return response;
};
/* END OCI-N&T V1.0.4 1.0.4-analytics-split-a2-import-fix1 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-analytics-layout-a3 */
function ntAnalyticsCloneControlA3(source, className = "") {
  if (!source) return null;
  const clone = source.cloneNode(true);
  clone.removeAttribute("id");
  clone.removeAttribute("name");
  clone.classList.add("nt-analytics-compact-input");
  if (className) clone.classList.add(className);
  clone.value = source.value;
  clone.disabled = source.disabled;

  clone.addEventListener("input", () => {
    source.value = clone.value;
    source.dispatchEvent(new Event("input", { bubbles: true }));
  });
  clone.addEventListener("change", () => {
    source.value = clone.value;
    source.dispatchEvent(new Event("change", { bubbles: true }));
  });
  return clone;
}

function ntAnalyticsSetSourceValueA3(source, clone, value) {
  if (!source) return;
  source.value = String(value);
  source.dispatchEvent(new Event("input", { bubbles: true }));
  source.dispatchEvent(new Event("change", { bubbles: true }));
  if (clone) clone.value = String(value);
}

function ntAnalyticsCompactControlsA3(mode, card) {
  if (!card) return;

  const content = document.querySelector("#tenant-content");
  const host = card.parentElement;
  host?.classList.add("nt-analytics-onecol-host");
  card.classList.add("nt-analytics-compact-card");

  const pageCopy = content?.querySelector(":scope > p");
  if (
    pageCopy &&
    /OCI Monitoring|Usage API|后台|采集|查询/.test(pageCopy.textContent || "")
  ) {
    pageCopy.classList.add("nt-analytics-page-copy-hidden");
  }

  // A2 inserted a secondary range bar. A3 replaces the entire input area
  // with one compact analysis toolbar modeled after the reference layout.
  card.querySelector(".nt-analytics-rangebar")?.remove();

  const oldBar = card.querySelector(".nt-analytics-controlbar-a3");
  if (oldBar) return;

  const queryLabel = mode === "costs" ? "查询费用" : "查询指标";
  const nativeQueryButton = [...card.querySelectorAll("button")].find(
    (el) => (el.textContent || "").trim().includes(queryLabel)
  );
  if (!nativeQueryButton) return;

  const nativeSelects = [...card.querySelectorAll("select")];
  const nativeDates = [...card.querySelectorAll('input[type="date"]')];
  const nativeNumbers = [...card.querySelectorAll('input[type="number"]')];

  const bar = document.createElement("div");
  bar.className = `nt-analytics-controlbar-a3 nt-analytics-controlbar-${mode}`;

  const quick = document.createElement("div");
  quick.className = "nt-analytics-quick-a3";

  const custom = document.createElement("div");
  custom.className = "nt-analytics-custom-a3";
  custom.hidden = true;

  const scope = document.createElement("span");
  scope.className = "nt-analytics-scope-a3";
  scope.textContent =
    mode === "costs" ? "范围：整个当前 OCI 账户" : "范围：当前所选实例";

  const compactQuery = document.createElement("button");
  compactQuery.type = "button";
  compactQuery.className = "button primary nt-analytics-query-a3";
  compactQuery.textContent = queryLabel;
  compactQuery.addEventListener("click", () => {
    if (nativeQueryButton.disabled) {
      if (typeof toast === "function") toast("当前查询条件尚未就绪", "bad");
      return;
    }
    nativeQueryButton.click();
  });

  if (mode === "metrics") {
    const instanceSource = nativeSelects[0] || null;
    const hoursSource =
      nativeNumbers.find((input) => Number(input.min || 0) <= 24) ||
      nativeNumbers[0] ||
      null;

    if (instanceSource) {
      const field = document.createElement("label");
      field.className = "nt-analytics-inline-field-a3 nt-analytics-instance-field-a3";
      const title = document.createElement("span");
      title.textContent = "实例";
      const instanceClone = ntAnalyticsCloneControlA3(
        instanceSource,
        "nt-analytics-instance-select-a3"
      );
      field.append(title, instanceClone);
      bar.append(field);
    }

    const rangeLabel = document.createElement("span");
    rangeLabel.className = "nt-analytics-control-label-a3";
    rangeLabel.textContent = "时间范围：";
    bar.append(rangeLabel);

    const hoursClone = ntAnalyticsCloneControlA3(
      hoursSource,
      "nt-analytics-hours-a3"
    );
    if (hoursClone) {
      hoursClone.min = hoursSource?.min || "1";
      custom.append(hoursClone);
      const suffix = document.createElement("span");
      suffix.textContent = "小时";
      custom.append(suffix);
    }

    [
      ["1 小时", "1"],
      ["24 小时", "24"],
      ["7 天", "168"],
      ["自定义", "custom"],
    ].forEach(([label, value]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button small${value === "24" ? " active" : ""}`;
      button.dataset.ntA3Metric = value;
      button.textContent = label;
      quick.append(button);
    });

    quick.addEventListener("click", (event) => {
      const button = event.target.closest("[data-nt-a3-metric]");
      if (!button) return;
      quick
        .querySelectorAll("[data-nt-a3-metric]")
        .forEach((el) => el.classList.toggle("active", el === button));

      if (button.dataset.ntA3Metric === "custom") {
        custom.hidden = false;
        hoursClone?.focus();
        return;
      }

      custom.hidden = true;
      ntAnalyticsSetSourceValueA3(
        hoursSource,
        hoursClone,
        button.dataset.ntA3Metric
      );
    });
  } else {
    const rangeLabel = document.createElement("span");
    rangeLabel.className = "nt-analytics-control-label-a3";
    rangeLabel.textContent = "时间范围：";
    bar.append(rangeLabel);

    const startSource = nativeDates[0] || null;
    const endSource = nativeDates[1] || null;
    const startClone = ntAnalyticsCloneControlA3(
      startSource,
      "nt-analytics-date-a3"
    );
    const endClone = ntAnalyticsCloneControlA3(
      endSource,
      "nt-analytics-date-a3"
    );

    if (startClone && endClone) {
      custom.append(startClone);
      const arrow = document.createElement("span");
      arrow.className = "nt-analytics-date-arrow-a3";
      arrow.textContent = "→";
      custom.append(arrow, endClone);
    }

    [
      ["今日", "today"],
      ["本月", "month"],
      ["自定义", "custom"],
    ].forEach(([label, value]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button small${value === "month" ? " active" : ""}`;
      button.dataset.ntA3Cost = value;
      button.textContent = label;
      quick.append(button);
    });

    quick.addEventListener("click", (event) => {
      const button = event.target.closest("[data-nt-a3-cost]");
      if (!button) return;

      quick
        .querySelectorAll("[data-nt-a3-cost]")
        .forEach((el) => el.classList.toggle("active", el === button));

      if (button.dataset.ntA3Cost === "custom") {
        custom.hidden = false;
        startClone?.focus();
        return;
      }

      custom.hidden = true;
      const now = new Date();
      const tomorrow = new Date(now);
      tomorrow.setDate(tomorrow.getDate() + 1);
      const start = new Date(now);

      if (button.dataset.ntA3Cost === "month") {
        start.setDate(1);
      }

      const startValue = ntAnalyticsLocalDate(start);
      const endValue = ntAnalyticsLocalDate(tomorrow);
      ntAnalyticsSetSourceValueA3(startSource, startClone, startValue);
      ntAnalyticsSetSourceValueA3(endSource, endClone, endValue);
    });
  }

  bar.append(quick, custom);

  const spacer = document.createElement("div");
  spacer.className = "nt-analytics-control-spacer-a3";
  bar.append(spacer, scope, compactQuery);

  card.prepend(bar);

  // Keep the native form and native controls alive for their existing event
  // handlers, but hide their visual wrappers. The compact controls above are
  // synchronized clones, so query semantics are unchanged.
  [...card.children].forEach((child) => {
    if (child !== bar) child.classList.add("nt-analytics-native-control-hidden-a3");
  });
}
/* END OCI-N&T V1.0.4 1.0.4-analytics-layout-a3 */

/* BEGIN OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 */
(() => {
  const $s = (id) => document.getElementById(id);
  const ID = "account-check-schedule-";

  function fmt(value) {
    if (!value) return "—";
    try { return typeof fmtDate === "function" ? fmtDate(value) : new Date(value).toLocaleString(); }
    catch { return String(value); }
  }

  function toggleUi() {
    const input = $s(ID + "enabled");
    const button = $s(ID + "toggle");
    if (!input || !button) return;
    button.textContent = input.checked ? "已启用" : "启用定时检测";
    button.setAttribute("aria-pressed", input.checked ? "true" : "false");
    button.classList.toggle("active", input.checked);
  }

  function modeUi() {
    const daily = $s(ID + "mode")?.value === "daily";
    if ($s(ID + "hours-field")) $s(ID + "hours-field").hidden = daily;
    if ($s(ID + "daily-field")) $s(ID + "daily-field").hidden = !daily;
  }

  function resultText(data) {
    const status = data.effective_last_status || data.last_status || "NEVER";
    const job = data.last_job;
    const names = {
      NEVER:"尚未执行", STARTED:"已启动", PENDING:"等待开始", RUNNING:"运行中", WAITING:"等待下一项",
      COMPLETED:"已完成", PARTIAL:"部分失败", FAILED:"失败", CANCELLED:"已取消", INTERRUPTED:"已中断",
      SKIPPED_BUSY:"已有检测任务，已跳过", ERROR:"启动失败"
    };
    let text = names[status] || status;
    if (job && Number(job.total || 0) > 0) {
      text += ` · ${Number(job.alive || 0)} 正常 / ${Number(job.abnormal || 0)} 异常 / ${Number(job.unknown || 0)} 未知`;
    }
    return text;
  }

  function notifySummary(policy) {
    const mode = policy.notify_mode || "abnormal_only";
    if (mode === "silent") return "不发送通知";
    if (mode === "all") return "每次检测通知";
    return `连续 ${Number(policy.consecutive_failures || 2)} 次异常后告警`;
  }

  async function loadSchedule() {
    if (!$s(ID + "card")) return;
    const data = await api("/settings/account-check-schedule");
    const s = data.settings || {};
    const policy = data.alert_policy || {};
    $s(ID + "enabled").checked = Boolean(s.enabled);
    $s(ID + "mode").value = s.mode || "interval";
    $s(ID + "hours").value = Number(s.interval_hours || 24);
    $s(ID + "daily-time").value = s.daily_time || "02:00";
    $s(ID + "account-interval").value = Number.isFinite(Number(s.account_interval_seconds)) ? Number(s.account_interval_seconds) : 0;
    $s(ID + "notify-mode").value = policy.notify_mode || "abnormal_only";
    $s(ID + "consecutive-failures").value = Number(policy.consecutive_failures || 2);
    $s(ID + "recovery-notify").value = policy.recovery_notify === false ? "false" : "true";
    $s(ID + "last").textContent = fmt(data.last_attempt_at);
    $s(ID + "next").textContent = s.enabled ? fmt(data.next_run_at) : "未启用";
    $s(ID + "result").textContent = resultText(data);
    $s(ID + "result").title = data.last_message || "";
    if (!s.enabled) {
      $s(ID + "summary").textContent = `当前关闭 · ${notifySummary(policy)}`;
    } else if (s.mode === "daily") {
      const offset = Number(s.timezone_offset_minutes || 0);
      const sign = offset >= 0 ? "+" : "-";
      const hh = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
      const mm = String(Math.abs(offset) % 60).padStart(2, "0");
      $s(ID + "summary").textContent = `每天 ${s.daily_time}（UTC${sign}${hh}:${mm}） · 账户间隔 ${s.account_interval_seconds} 秒 · ${notifySummary(policy)}`;
    } else {
      $s(ID + "summary").textContent = `每 ${s.interval_hours} 小时 · 账户间隔 ${s.account_interval_seconds} 秒 · ${notifySummary(policy)}`;
    }
    toggleUi(); modeUi();
  }

  function payload() {
    const interval = Number($s(ID + "account-interval").value);
    const hours = Number($s(ID + "hours").value);
    const threshold = Number($s(ID + "consecutive-failures").value);
    if (!Number.isInteger(interval) || interval < 0 || interval > 86400) throw new Error("账户间隔必须是 0–86400 的整数秒。");
    if (!Number.isInteger(hours) || hours < 1 || hours > 720) throw new Error("检测周期必须是 1–720 的整数小时。");
    if (!Number.isInteger(threshold) || threshold < 1 || threshold > 10) throw new Error("连续异常阈值必须是 1–10 的整数。");
    return {
      enabled: Boolean($s(ID + "enabled").checked),
      mode: $s(ID + "mode").value,
      interval_hours: hours,
      daily_time: $s(ID + "daily-time").value || "02:00",
      account_interval_seconds: interval,
      timezone_offset_minutes: -new Date().getTimezoneOffset(),
      alert_policy: {
        notify_mode: $s(ID + "notify-mode").value,
        consecutive_failures: threshold,
        recovery_notify: $s(ID + "recovery-notify").value === "true",
      },
    };
  }

  async function saveSchedule(button) {
    if (button) setBusy(button, true, "保存中……");
    try {
      await api("/settings/account-check-schedule", { method:"PUT", body:payload() });
      await loadSchedule();
      toast("API 定时检测与告警设置已保存", "good");
    } finally { if (button) setBusy(button, false); }
  }

  async function runNow(button) {
    setBusy(button, true, "启动中……");
    try {
      await api("/settings/account-check-schedule", { method:"PUT", body:payload() });
      const result = await api("/settings/account-check-schedule/run-now", { method:"POST" });
      if (typeof renderBulkJob === "function" && result?.job) renderBulkJob(result.job);
      toast(`检测任务 #${result?.job?.id || "—"} 已进入任务中心`, "good");
      await loadSchedule();
      if (state?.currentPage === "tasks" && typeof loadTasks === "function") loadTasks(null, true).catch(() => {});
    } catch (error) { toast(error.message, "bad"); }
    finally { setBusy(button, false); }
  }

  function init() {
    const form = $s(ID + "form");
    if (!form || form.dataset.ready === "1") return;
    form.dataset.ready = "1";
    $s(ID + "toggle")?.addEventListener("click", () => { $s(ID + "enabled").checked = !$s(ID + "enabled").checked; toggleUi(); });
    $s(ID + "mode")?.addEventListener("change", modeUi);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try { await saveSchedule(event.submitter); } catch (error) { toast(error.message, "bad"); }
    });
    $s(ID + "run-now")?.addEventListener("click", (event) => runNow(event.currentTarget));
    if (typeof loadSettings === "function" && !window.__ntApiScheduleLoadWrapped) {
      window.__ntApiScheduleLoadWrapped = true;
      const original = loadSettings;
      loadSettings = async function(...args) {
        const result = await original(...args);
        try { await loadSchedule(); } catch (error) { toast(error.message, "bad"); }
        return result;
      };
    }
    loadSchedule().catch(() => {});
  }

  window.ntLoadAccountCheckSchedule = loadSchedule;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else queueMicrotask(init);
})();
/* END OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 */

/* BEGIN OCI-N&T V1.0.4 API_SCHEDULE_UI_ALIGN3 */
(() => {
  const FEATURE = "1.0.4-api-schedule-ui-align3";

  function initScheduleHeaderAlign3() {
    const checkbox = document.getElementById("account-check-schedule-enabled");
    const button = document.getElementById("account-check-schedule-toggle");
    if (!checkbox || !button) return;

    const sync = () => {
      const on = Boolean(checkbox.checked);
      button.textContent = on ? "停用定时检测" : "启用定时检测";
      button.setAttribute("aria-pressed", on ? "true" : "false");
    };

    if (button.dataset.ntScheduleAlign3 !== FEATURE) {
      button.dataset.ntScheduleAlign3 = FEATURE;

      checkbox.addEventListener("change", () => queueMicrotask(sync));
      button.addEventListener("click", () => queueMicrotask(sync));


    }

    sync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initScheduleHeaderAlign3, { once: true });
  } else {
    initScheduleHeaderAlign3();
  }
})();
/* END OCI-N&T V1.0.4 API_SCHEDULE_UI_ALIGN3 */

/* OCI-N&T V1.0.5 1.0.5-proxy-exclusive-allocation1 */


/* BEGIN OCI-N&T V1.0.5 1.0.5-instance-detail10 */
const ntInstanceDetailState = {
  account: null,
  instance: null,
  tab: "overview",
  metricHours: 1,
  metricRequest: 0,
  consoleRequest: 0,
  historyRequest: 0,
};

function ntInstanceDetailFind(accountId, instanceId) {
  const account = (state.accounts || []).find((item) => Number(item.id) === Number(accountId));
  const candidates = [
    ...(state.ui2?.instanceResult?.instances || []),
    ...(state.instances || []),
  ];
  const instance = candidates.find((item) =>
    String(item.id) === String(instanceId) &&
    (!item.account_id || Number(item.account_id) === Number(accountId))
  );
  return { account, instance };
}

function ntInstanceDetailDialog() {
  let dialog = document.getElementById("nt-instance-detail-dialog");
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = "nt-instance-detail-dialog";
  dialog.className = "nt-instance-detail-dialog";
  dialog.innerHTML = '<div id="nt-instance-detail-root"></div>';
  document.body.appendChild(dialog);
  dialog.addEventListener("cancel", () => { ntInstanceDetailState.metricRequest += 1; });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  return dialog;
}

function ntInstanceDetailCopy(value, label) {
  if (!value) return "";
  return `<button class="nt-detail-copy" data-copy-text="${esc(value)}" data-copy-label="${esc(label)}" type="button" aria-label="复制${esc(label)}">复制</button>`;
}

function ntInstanceDetailStatus(instance) {
  const status = String(instance.lifecycle_state || "UNKNOWN").toUpperCase();
  const klass = status === "RUNNING" ? "good" : status === "STOPPED" ? "muted" : "warn";
  return `<span class="badge ${klass}">${esc(status)}</span>`;
}

function ntInstanceDetailMetricToolbar() {
  const hours = Number(ntInstanceDetailState.metricHours || 1);
  return `<div class="nt-detail-metric-toolbar">
    <div class="nt-detail-range" aria-label="指标时间范围">
      ${[[1,"1 小时"],[24,"24 小时"],[168,"7 天"]].map(([value,label]) => `<button class="${hours === value ? "active" : ""}" data-detail-metric-hours="${value}" type="button">${label}</button>`).join("")}
    </div>
    <div><button class="button small" data-detail-metrics-cache type="button">读取缓存</button><button class="button small primary" data-detail-metrics-live type="button">实时读取</button></div>
  </div>`;
}

function ntInstanceDetailMetricEmpty(copy = "优先读取本地缓存，不会自动访问 OCI。") {
  return `${ntInstanceDetailMetricToolbar()}<div class="nt-detail-metric-empty"><strong>指标尚未载入</strong><span>${esc(copy)}</span><button class="button primary" data-detail-metrics-live type="button">实时读取指标</button></div>`;
}

function ntInstanceDetailConsole(instance) {
  const primary = primaryVnic(instance);
  return `<div class="nt-detail-console-grid">
    <section class="nt-detail-section">
      <div class="nt-detail-section-head"><div><h3>控制台</h3><p>VNC/Console Connection 由 OCI 官方实例控制台连接提供。</p></div></div>
      <div class="nt-detail-console-actions">
        <button class="button primary" data-detail-open-tab="vnc" type="button">打开 VNC 工作区</button>
        <button class="button" data-detail-console-refresh type="button">刷新会话</button>
      </div>
      <div class="nt-detail-console-hint">
        <strong>${primary?.public_ip ? `公网 IP：${esc(primary.public_ip)}` : "当前没有缓存公网 IP"}</strong>
        <span>VNC 不依赖实例 SSH 服务，适合系统启动异常、SSH 无法连接或救援操作。</span>
      </div>
    </section>
    <section class="nt-detail-section nt-detail-wide">
      <div class="nt-detail-section-head"><div><h3>最近 VNC 会话</h3><p>仅读取 OCI-N&T 本地会话记录。</p></div></div>
      <div data-detail-console-sessions class="nt-detail-history-list"><div class="nt-detail-loading compact"><span></span><strong>正在读取本地会话</strong></div></div>
    </section>
  </div>`;
}

function ntInstanceDetailHistory() {
  return `<section class="nt-detail-section nt-detail-wide">
    <div class="nt-detail-section-head"><div><h3>操作记录</h3><p>仅显示当前实例的 OCI-N&T 审计记录。</p></div><button class="button small" data-detail-history-refresh type="button">刷新</button></div>
    <div data-detail-history-list class="nt-detail-history-list"><div class="nt-detail-loading compact"><span></span><strong>正在读取操作记录</strong></div></div>
  </section>`;
}

async function ntInstanceDetailLoadConsoleSessions() {
  const { account, instance } = ntInstanceDetailState;
  const box = document.querySelector("#nt-instance-detail-root [data-detail-console-sessions]");
  if (!box || !account || !instance || ntInstanceDetailState.tab !== "console") return;
  const requestId = ++ntInstanceDetailState.consoleRequest;
  try {
    const items = await api(`/accounts/${account.id}/vnc/sessions?instance_id=${encodeURIComponent(instance.id)}`);
    if (requestId !== ntInstanceDetailState.consoleRequest || ntInstanceDetailState.tab !== "console") return;
    box.innerHTML = !items?.length ? '<div class="nt-detail-empty"><strong>暂无 VNC 会话</strong><span>需要时打开 VNC 工作区创建连接。</span></div>' : items.slice(0, 8).map((item) => `
      <article class="nt-detail-history-row"><div><strong>${esc(String(item.status || "UNKNOWN").toUpperCase())}</strong><span>${esc(fmtDate(item.created_at))}</span></div><div><span>会话 #${esc(item.id)}</span><small>${item.expires_at ? `到期 ${esc(fmtDate(item.expires_at))}` : ""}</small></div></article>`).join("");
  } catch (error) {
    if (requestId === ntInstanceDetailState.consoleRequest) box.innerHTML = `<div class="nt-detail-empty"><strong>读取失败</strong><span>${esc(error.message)}</span></div>`;
  }
}

async function ntInstanceDetailLoadHistory() {
  const { instance } = ntInstanceDetailState;
  const box = document.querySelector("#nt-instance-detail-root [data-detail-history-list]");
  if (!box || !instance || ntInstanceDetailState.tab !== "history") return;
  const requestId = ++ntInstanceDetailState.historyRequest;
  try {
    const query = new URLSearchParams({ limit: "100", resource_id: instance.id });
    const items = await api(`/audit-logs?${query}`);
    if (requestId !== ntInstanceDetailState.historyRequest || ntInstanceDetailState.tab !== "history") return;
    box.innerHTML = !items?.length ? '<div class="nt-detail-empty"><strong>暂无操作记录</strong><span>对该实例执行操作后会在这里显示。</span></div>' : items.map((item) => `
      <article class="nt-detail-history-row"><div><strong>${esc(item.action_label || item.action || "操作")}</strong><span>${esc(fmtDate(item.created_at))}</span></div><div><span>${esc(item.username || "system")}</span><small>${esc(item.detail || "")}</small></div></article>`).join("");
  } catch (error) {
    if (requestId === ntInstanceDetailState.historyRequest) box.innerHTML = `<div class="nt-detail-empty"><strong>读取失败</strong><span>${esc(error.message)}</span></div>`;
  }
}


function ntInstanceDetailConnection(instance) {
  const vnic = primaryVnic(instance);
  const publicIp = String(vnic?.public_ip || "").trim();
  const privateIp = String(vnic?.private_ip || "").trim();
  const ubuntuCommand = publicIp ? `ssh ubuntu@${publicIp}` : "";
  const rootCommand = publicIp ? `ssh root@${publicIp}` : "";
  const disabled = publicIp ? "" : " disabled";
  return `<section class="nt-detail-section nt-detail-wide nt-detail-connect-section">
    <div class="nt-detail-section-head">
      <div><h3>网络与连接</h3><p>${publicIp ? "连接命令已按当前缓存公网 IP 生成" : "当前实例没有缓存公网 IP"}</p></div>
      <button class="button small" data-detail-open-tab="vnc" type="button">打开 VNC</button>
    </div>
    <div class="nt-detail-connect-grid">
      <article>
        <span>公网 IPv4</span>
        <strong>${esc(publicIp || "未分配")}</strong>
        <button class="nt-detail-copy" data-copy-text="${esc(publicIp)}" data-copy-label="公网 IP" type="button"${disabled}>复制 IP</button>
      </article>
      <article>
        <span>私网 IPv4</span>
        <strong>${esc(privateIp || "未读取")}</strong>
        <button class="nt-detail-copy" data-copy-text="${esc(privateIp)}" data-copy-label="私网 IP" type="button"${privateIp ? "" : " disabled"}>复制 IP</button>
      </article>
      <article class="nt-detail-command-card">
        <span>Ubuntu SSH</span>
        <code>${esc(ubuntuCommand || "需要公网 IP")}</code>
        <button class="nt-detail-copy" data-copy-text="${esc(ubuntuCommand)}" data-copy-label="Ubuntu SSH 命令" type="button"${disabled}>复制命令</button>
      </article>
      <article class="nt-detail-command-card">
        <span>Root SSH</span>
        <code>${esc(rootCommand || "需要公网 IP")}</code>
        <button class="nt-detail-copy" data-copy-text="${esc(rootCommand)}" data-copy-label="Root SSH 命令" type="button"${disabled}>复制命令</button>
      </article>
    </div>
  </section>`;
}

function ntInstanceDetailOverview(instance) {
  const vnics = instance.vnics || [];
  const primary = primaryVnic(instance);
  const volumes = instance.boot_volumes || [];
  const notes = instance.freeform_tags?.["OCI-N&T-Note"] || "未设置备注";
  return `<div class="nt-detail-overview-grid">
    <section class="nt-detail-section nt-detail-summary">
      <div class="nt-detail-section-head"><div><h3>计算配置</h3><p>来自最近一次实例同步缓存</p></div></div>
      <div class="nt-detail-kpis">
        <article><span>实例规格</span><strong>${esc(instance.shape || "—")}</strong></article>
        <article><span>处理器</span><strong>${instance.ocpus ?? "—"} OCPU</strong></article>
        <article><span>内存</span><strong>${instance.memory_in_gbs ?? "—"} GB</strong></article>
        <article><span>引导卷</span><strong>${volumes[0]?.size_in_gbs ? `${esc(volumes[0].size_in_gbs)} GB` : "—"}</strong></article>
      </div>
    </section>
    <section class="nt-detail-section">
      <div class="nt-detail-section-head"><div><h3>位置与生命周期</h3><p>无需实时访问 OCI</p></div></div>
      <dl class="nt-detail-list">
        <div><dt>区域</dt><dd>${esc(instance.region || ntInstanceDetailState.account?.region || "—")}</dd></div>
        <div><dt>可用域</dt><dd>${esc(instance.availability_domain || "—")}</dd></div>
        <div><dt>故障域</dt><dd>${esc(instance.fault_domain || "—")}</dd></div>
        <div><dt>创建时间</dt><dd>${esc(fmtDate(instance.time_created))}</dd></div>
        <div><dt>区间</dt><dd>${esc(instance.compartment_name || "—")}</dd></div>
        <div><dt>VNIC</dt><dd>${vnics.length} 个${primary?.public_ip ? " · 有公网 IP" : " · 无公网 IP"}</dd></div>
      </dl>
    </section>
    ${ntInstanceDetailConnection(instance)}
    <section class="nt-detail-section nt-detail-wide">
      <div class="nt-detail-section-head"><div><h3>标识与备注</h3><p>${esc(notes)}</p></div></div>
      <div class="nt-detail-id-row"><code title="${esc(instance.id)}">${esc(instance.id || "—")}</code>${ntInstanceDetailCopy(instance.id, "实例 OCID")}</div>
    </section>
  </div>`;
}

function ntInstanceDetailNetwork(instance) {
  const vnics = instance.vnics || [];
  if (!vnics.length) return '<div class="nt-detail-empty"><strong>缓存中没有 VNIC</strong><span>请在实例页执行一次“从 OCI 同步”。</span></div>';
  return `<div class="nt-detail-inline-toolbar"><div><strong>网络配置</strong><span>详情使用缓存；高级操作进入租户网络工作区。</span></div><button class="button small primary" data-detail-open-tab="network" type="button">高级网络管理</button></div><div class="nt-detail-vnic-list">${vnics.map((vnic, index) => {
    const subnet = vnic.subnet || {};
    const ipv6 = vnic.ipv6_addresses || vnic.ipv6s || [];
    return `<article class="nt-detail-vnic-card">
      <header><div><strong>${esc(vnic.display_name || `VNIC ${index + 1}`)}</strong><span>${vnic.is_primary ? "主网卡" : "附属网卡"}</span></div><span class="badge ${vnic.is_primary ? "good" : "muted"}">${vnic.is_primary ? "PRIMARY" : "ATTACHED"}</span></header>
      <div class="nt-detail-network-grid">
        <div><span>公网 IPv4</span><strong>${esc(vnic.public_ip || "未分配")}</strong>${ntInstanceDetailCopy(vnic.public_ip, "公网 IP")}</div>
        <div><span>私网 IPv4</span><strong>${esc(vnic.private_ip || "—")}</strong>${ntInstanceDetailCopy(vnic.private_ip, "私网 IP")}</div>
        <div><span>子网</span><strong>${esc(subnet.display_name || subnet.name || "—")}</strong><small>${esc(subnet.cidr_block || "")}</small></div>
        <div><span>IPv6</span><strong>${ipv6.length ? esc(ipv6[0]?.ip_address || ipv6[0]) : "未分配"}</strong><small>${ipv6.length > 1 ? `另有 ${ipv6.length - 1} 个地址` : ""}</small></div>
        <div><span>主机名</span><strong>${esc(vnic.hostname_label || "—")}</strong></div>
        <div><span>MAC</span><strong>${esc(vnic.mac_address || "—")}</strong></div>
      </div>
    </article>`;
  }).join("")}</div>`;
}

function ntInstanceDetailMetricCards(data) {
  const metrics = data?.metrics && typeof data.metrics === "object" ? data.metrics : {};
  const defs = [["CPU 使用率", "cpu"], ["内存使用率", "memory"], ["网络接收", "network_in"], ["网络发送", "network_out"]];
  const groups = defs.map(([label, key]) => {
    const points = ntAnalyticsMetricPoints(metrics[key] || []);
    return { label, key, points, latest: points.length ? points[points.length - 1].value : null };
  });
  return `<div class="nt-detail-metric-result">
    ${ntInstanceDetailMetricToolbar()}
    <div class="nt-detail-metric-meta"><span>${data?.from_cache ? "本地缓存" : "OCI 实时"}${data?.cached_at ? ` · ${esc(fmtDate(data.cached_at))}` : ""}</span><span>${Number(ntInstanceDetailState.metricHours || 1)} 小时范围</span></div>
    <div class="nt-detail-metric-grid">${groups.map((item) => `<article><span>${item.label}</span><strong>${ntAnalyticsMetricDisplay(item.key, item.latest)}</strong><small>${item.points.length} 个数据点</small>${ntAnalyticsSparkline(item.points)}</article>`).join("")}</div>
  </div>`;
}


function ntInstanceDetailStorage(instance) {
  const volumes = instance.boot_volumes || [];
  if (!volumes.length) {
    return '<div class="nt-detail-empty"><strong>缓存中没有引导卷信息</strong><span>下次手动同步实例时会同时更新引导卷缓存。</span></div>';
  }
  return `<div class="nt-detail-inline-toolbar"><div><strong>存储配置</strong><span>可直接修改引导卷，也可进入完整存储工作区。</span></div><button class="button small primary" data-detail-open-tab="volumes" type="button">完整存储管理</button></div><div class="nt-detail-volume-list">${volumes.map((volume, index) => `
    <article class="nt-detail-volume-card">
      <header><div><strong>${esc(volume.display_name || `引导卷 ${index + 1}`)}</strong><span>${esc(volume.lifecycle_state || "状态未知")}</span></div><span class="badge ${String(volume.lifecycle_state || "").toUpperCase() === "AVAILABLE" ? "good" : "muted"}">${esc(volume.size_in_gbs ? `${volume.size_in_gbs} GB` : "BOOT")}</span></header>
      <div class="nt-detail-volume-grid">
        <div><span>容量</span><strong>${volume.size_in_gbs ? `${esc(volume.size_in_gbs)} GB` : "—"}</strong></div>
        <div><span>性能</span><strong>${volume.vpus_per_gb ?? "—"} VPU/GB</strong></div>
        <div><span>附件状态</span><strong>${esc(volume.lifecycle_state || "—")}</strong></div>
      </div>
      <div class="nt-detail-volume-actions">
        <button class="button small" data-detail-volume-edit="${esc(volume.id)}" type="button">调整容量与性能</button>
        <button class="button small primary-soft" data-detail-volume-backup="${esc(volume.id)}" type="button">创建备份</button>
      </div>
      <div class="nt-detail-id-row"><code title="${esc(volume.id)}">${esc(volume.id || "—")}</code>${ntInstanceDetailCopy(volume.id, "引导卷 OCID")}</div>
    </article>`).join("")}</div>`;
}

function ntInstanceDetailActions(instance) {
  const status = String(instance.lifecycle_state || "UNKNOWN").toUpperCase();
  const running = status === "RUNNING";
  const stopped = status === "STOPPED";
  const primary = primaryVnic(instance);
  return `<div class="nt-detail-actionbar">
    <div><strong>快捷操作</strong><span>提交后不会自动重复查询 OCI</span></div>
    <div class="nt-detail-action-buttons">
      ${stopped ? '<button class="button primary" data-detail-action="START" type="button">启动</button>' : ""}
      ${running ? '<button class="button" data-detail-action="SOFTSTOP" type="button">停止</button><button class="button" data-detail-action="SOFTRESET" type="button">重启</button>' : ""}
      ${primary?.private_ip_id ? '<button class="button" data-detail-replace-ip type="button">更换 IP</button>' : ""}
      <button class="button" data-detail-edit type="button">修改配置</button>
      <button class="button danger" data-detail-terminate type="button">终止</button>
    </div>
  </div>`;
}

async function ntInstanceDetailRefreshLocal() {
  if (state.currentPage === "instances" && typeof ui2LoadGlobalInstances === "function") {
    await ui2LoadGlobalInstances(false);
    return;
  }
  if (state.currentPage === "tenant" && state.tenantTab === "instances") {
    await loadTenantInstances(false);
  }
}

async function ntInstanceDetailAction(button) {
  const { account, instance } = ntInstanceDetailState;
  if (!account || !instance) return;
  const action = String(button.dataset.detailAction || "");
  const names = { START: "启动", SOFTSTOP: "停止", SOFTRESET: "重启" };
  if (!names[action]) return;
  if (action !== "START") {
    const confirmed = await openConfirmDialog({
      title: `${names[action]}实例`,
      message: `确定要${names[action]} ${instance.display_name || "当前实例"}？`,
      submitText: `确认${names[action]}`,
      danger: action === "SOFTSTOP",
    });
    if (!confirmed) return;
  }
  setBusy(button, true, `${names[action]}中……`);
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/actions`, {
      method: "POST",
      body: { action, region: instance.region || account.region },
    });
    toast(`${names[action]}请求已提交`, "good");
    await ntInstanceDetailRefreshLocal();
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function ntInstanceDetailReplaceIp(button) {
  const { account, instance } = ntInstanceDetailState;
  const vnic = primaryVnic(instance || {});
  if (!account || !instance || !vnic?.private_ip_id) return;
  const confirmed = await openConfirmDialog({
    title: "更换公网 IP",
    message: `将释放 ${vnic.public_ip || "当前临时公网 IP"} 并申请新地址，旧地址无法恢复。`,
    submitText: "确认更换",
    danger: true,
  });
  if (!confirmed) return;
  setBusy(button, true, "更换中……");
  try {
    const result = await api(`/accounts/${account.id}/public-ips/${encodeURIComponent(vnic.private_ip_id)}/replace`, {
      method: "POST",
      body: { region: instance.region || account.region },
    });
    toast(`新公网 IP：${result.new_ip || "正在分配"}`, "good");
    ntInstanceDetailDialog().close();
    await ntInstanceDetailRefreshLocal();
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function ntInstanceDetailEdit() {
  const { account, instance } = ntInstanceDetailState;
  if (!account || !instance) return;
  const bootVolume = (instance.boot_volumes || [])[0] || null;
  const currentBootSize = Number(bootVolume?.size_in_gbs || 0);
  const detailDialog = ntInstanceDetailDialog();

  // Close the detail modal before opening the editor modal.
  if (detailDialog.open) detailDialog.close();
  await new Promise((resolve) => requestAnimationFrame(resolve));

  const values = await openUtilityDialog({
    title: "修改实例与引导卷",
    copy: `${instance.display_name || "N&T"} · ${instance.shape || "未知配置"}`,
    fields: [
      { name: "display_name", label: "实例名称", value: instance.display_name || "N&T", required: true },
      { name: "note", label: "实例备注", value: instance.freeform_tags?.["OCI-N&T-Note"] || "", placeholder: "留空表示清除备注" },
      { name: "ocpus", label: "OCPU", type: "number", value: instance.ocpus ?? "", min: 1, step: 1, help: "仅灵活规格可修改" },
      { name: "memory_in_gbs", label: "内存 GB", type: "number", value: instance.memory_in_gbs ?? "", min: 1, step: 1, help: "仅灵活规格可修改" },
      {
        name: "boot_volume_size_in_gbs",
        label: "引导卷 GB",
        type: "number",
        value: currentBootSize || "",
        min: currentBootSize || 50,
        max: 32768,
        step: 1,
        disabled: !bootVolume,
        help: bootVolume
          ? `当前 ${currentBootSize} GB，只能扩容，不能缩容`
          : "当前实例缓存中没有引导卷信息，请先手动同步实例",
      },
    ],
    submitText: "保存修改",
    validate: (data) => {
      if (!String(data.display_name || "").trim()) return "实例名称不能为空";
      if (!bootVolume || data.boot_volume_size_in_gbs === "" || data.boot_volume_size_in_gbs == null) return "";
      const size = Number(data.boot_volume_size_in_gbs);
      if (!Number.isInteger(size)) return "引导卷容量必须是整数 GB";
      if (size < 50) return "引导卷容量不能小于 50 GB";
      if (size < currentBootSize) return `引导卷只能扩容，当前容量为 ${currentBootSize} GB`;
      if (size > 32768) return "引导卷容量不能超过 32768 GB";
      return "";
    },
  });
  if (!values) {
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
    return;
  }
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/details`, {
      method: "PUT",
      body: {
        region: instance.region || account.region,
        display_name: String(values.display_name).trim(),
        note: String(values.note || "").trim(),
        ocpus: values.ocpus,
        memory_in_gbs: values.memory_in_gbs,
      },
    });

    const requestedBootSize = Number(values.boot_volume_size_in_gbs || 0);
    if (bootVolume && requestedBootSize > currentBootSize) {
      try {
        await api(`/accounts/${account.id}/boot-volumes/${encodeURIComponent(bootVolume.id)}/details`, {
          method: "PUT",
          body: {
            region: instance.region || account.region,
            display_name: null,
            size_in_gbs: requestedBootSize,
            vpus_per_gb: null,
            is_auto_tune_enabled: null,
          },
        });
      } catch (volumeError) {
        toast(`实例资料已修改，但引导卷扩容失败：${volumeError.message}`, "bad");
        ntInstanceDetailDialog().close();
        await ntInstanceDetailRefreshLocal();
        return;
      }
    }
    toast(requestedBootSize > currentBootSize ? `实例修改已提交，引导卷正在扩容至 ${requestedBootSize} GB` : "实例修改已提交", "good");
    ntInstanceDetailDialog().close();
    await ntInstanceDetailRefreshLocal();
  } catch (error) {
    toast(error.message, "bad");
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
  }
}

async function ntInstanceDetailTerminate(button) {
  const { account, instance } = ntInstanceDetailState;
  if (!account || !instance) return;
  const confirmed = await openConfirmDialog({
    title: "终止实例",
    copy: `${instance.display_name || "当前实例"} · 此操作不可撤销`,
    message: "终止后实例无法恢复，引导卷将按保留策略处理。",
    confirmationText: "TERMINATE",
    submitText: "永久终止",
    danger: true,
  });
  if (!confirmed) return;
  setBusy(button, true, "终止中……");
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/terminate`, {
      method: "POST",
      body: {
        region: instance.region || account.region,
        preserve_boot_volume: true,
        confirmation: "TERMINATE",
      },
    });
    toast("实例终止请求已提交，引导卷将保留", "good");
    ntInstanceDetailDialog().close();
    await ntInstanceDetailRefreshLocal();
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}


async function ntInstanceDetailEditVolume(button) {
  const { account, instance } = ntInstanceDetailState;
  const volume = (instance?.boot_volumes || []).find((item) => String(item.id) === String(button.dataset.detailVolumeEdit));
  if (!account || !instance || !volume) return;
  const currentSize = Number(volume.size_in_gbs || 50);
  const detailDialog = ntInstanceDetailDialog();
  if (detailDialog.open) detailDialog.close();
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const values = await openUtilityDialog({
    title: "调整引导卷",
    copy: `${volume.display_name || "引导卷"} · 当前 ${currentSize} GB`,
    fields: [
      { name: "display_name", label: "引导卷名称", value: volume.display_name || "" },
      { name: "size_in_gbs", label: "容量 GB", type: "number", value: currentSize, min: currentSize, max: 32768, step: 1, help: "只能扩容，不能缩容" },
      { name: "vpus_per_gb", label: "VPU/GB", type: "number", value: volume.vpus_per_gb ?? 10, min: 0, max: 120, step: 10, help: "0–120，费用可能随性能提高" },
      { name: "is_auto_tune_enabled", label: "启用性能自动调优", type: "checkbox", value: Boolean(volume.is_auto_tune_enabled), full: true },
    ],
    submitText: "保存引导卷",
    validate: (data) => Number(data.size_in_gbs) < currentSize ? `容量不能小于当前 ${currentSize} GB` : "",
  });
  if (!values) {
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
    return;
  }
  try {
    const result = await api(`/accounts/${account.id}/boot-volumes/${encodeURIComponent(volume.id)}/details`, {
      method: "PUT",
      body: { region: instance.region || account.region, ...values },
    });
    Object.assign(volume, result || {}, values);
    toast("引导卷修改已提交", "good");
    ntInstanceDetailState.tab = "storage";
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
  } catch (error) {
    toast(error.message, "bad");
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
  }
}

async function ntInstanceDetailBackupVolume(button) {
  const { account, instance } = ntInstanceDetailState;
  const volume = (instance?.boot_volumes || []).find((item) => String(item.id) === String(button.dataset.detailVolumeBackup));
  if (!account || !instance || !volume) return;
  const detailDialog = ntInstanceDetailDialog();
  if (detailDialog.open) detailDialog.close();
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const values = await openUtilityDialog({
    title: "创建引导卷备份",
    copy: volume.display_name || "当前引导卷",
    fields: [
      { name: "display_name", label: "备份名称", value: `${volume.display_name || "N&T"}-Backup-${new Date().toISOString().slice(0,10)}`, required: true },
      { name: "type", label: "备份类型", type: "select", value: "INCREMENTAL", options: [{value:"INCREMENTAL",label:"增量备份"},{value:"FULL",label:"完整备份"}] },
    ],
    submitText: "创建备份",
    validate: (data) => !String(data.display_name || "").trim() ? "备份名称不能为空" : "",
  });
  if (!values) {
    ntInstanceDetailRender();
    if (!detailDialog.open) detailDialog.showModal();
    return;
  }
  try {
    await api(`/accounts/${account.id}/boot-volumes/${encodeURIComponent(volume.id)}/backups`, {
      method: "POST",
      body: { region: instance.region || account.region, display_name: String(values.display_name).trim(), type: values.type },
    });
    toast("引导卷备份任务已创建", "good");
  } catch (error) {
    toast(error.message, "bad");
  }
  ntInstanceDetailRender();
  if (!detailDialog.open) detailDialog.showModal();
}

function ntInstanceDetailRender() {
  const root = document.getElementById("nt-instance-detail-root");
  const { account, instance, tab } = ntInstanceDetailState;
  if (!root || !account || !instance) return;
  const primary = primaryVnic(instance);
  const tabs = [["overview", "概览"], ["network", `网络 ${instance.vnics?.length || 0}`], ["storage", `存储 ${instance.boot_volumes?.length || 0}`], ["metrics", "监控"], ["console", "控制台"], ["history", "操作记录"]];
  root.innerHTML = `<div class="nt-detail-shell">
    <header class="nt-detail-header">
      <div class="nt-detail-title"><div class="nt-detail-icon">${esc((instance.display_name || "N").slice(0, 1).toUpperCase())}</div><div><div class="nt-detail-title-line"><h2>${esc(instance.display_name || "N&T")}</h2>${ntInstanceDetailStatus(instance)}</div><p>${esc(account.custom_name || account.tenancy_name || "OCI 租户")} · ${esc(instance.region || account.region || "—")}</p></div></div>
      <div class="nt-detail-header-actions">${primary?.public_ip ? `<span class="nt-detail-ip">${esc(primary.public_ip)}</span>` : ""}<button class="dialog-close" data-detail-close type="button" aria-label="关闭">×</button></div>
    </header>
    <nav class="nt-detail-tabs" aria-label="实例详情">${tabs.map(([key, label]) => `<button class="${tab === key ? "active" : ""}" data-detail-tab="${key}" type="button">${label}</button>`).join("")}</nav>
    ${ntInstanceDetailActions(instance)}
    <main class="nt-detail-body" data-detail-body>${tab === "overview" ? ntInstanceDetailOverview(instance) : tab === "network" ? ntInstanceDetailNetwork(instance) : tab === "storage" ? ntInstanceDetailStorage(instance) : tab === "metrics" ? ntInstanceDetailMetricEmpty() : tab === "console" ? ntInstanceDetailConsole(instance) : ntInstanceDetailHistory()}</main>
    <footer class="nt-detail-footer">
      <div class="nt-detail-related-actions">
        <span>相关管理</span>
        <button class="button small" data-detail-open-tab="vnc" type="button">VNC</button>
        <button class="button small" data-detail-open-tab="security" type="button">安全规则</button>
        <button class="button small" data-detail-open-tab="volumes" type="button">存储管理</button>
        <button class="button small" data-detail-open-tab="instances" type="button">完整实例页</button>
      </div>
      <button class="button" data-detail-close type="button">关闭</button>
    </footer>
  </div>`;
  if (tab === "metrics") ntInstanceDetailLoadMetrics(false);
  if (tab === "console") ntInstanceDetailLoadConsoleSessions();
  if (tab === "history") ntInstanceDetailLoadHistory();
}

/* OCI-N&T 2.0 stable metrics hotfix: instance detail includes compartment_id */
async function ntInstanceDetailLoadMetrics(live) {
  const { account, instance, metricHours } = ntInstanceDetailState;
  const body = document.querySelector("#nt-instance-detail-root [data-detail-body]");
  if (!body || !account || !instance || ntInstanceDetailState.tab !== "metrics") return;
  const request = ++ntInstanceDetailState.metricRequest;
  if (live) body.innerHTML = '<div class="nt-detail-loading"><span></span><strong>正在读取 OCI Monitoring</strong><small>查询在后台进行，可随时关闭详情。</small></div>';
  try {
    const query = new URLSearchParams({
      region: instance.region || account.region || account.home_region_key || "",
      compartment_id: instance.compartment_id || account.tenancy_ocid || "",
      hours: String(metricHours),
      cached: live ? "false" : "true",
      direct: "true",
    });
    const data = await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/metrics?${query}`);
    if (request !== ntInstanceDetailState.metricRequest || ntInstanceDetailState.tab !== "metrics") return;
    body.innerHTML = ntInstanceDetailMetricCards(data);
  } catch (error) {
    if (request !== ntInstanceDetailState.metricRequest || ntInstanceDetailState.tab !== "metrics") return;
    body.innerHTML = ntInstanceDetailMetricEmpty(live ? `读取失败：${error.message}` : "暂无本地指标缓存；点击后才会访问 OCI Monitoring。明细通常需要几秒返回。即使切换页面，结果也会写入缓存。 ");
  }
}

function ntOpenInstanceDetail(accountId, instanceId) {
  const found = ntInstanceDetailFind(accountId, instanceId);
  if (!found.account || !found.instance) {
    toast("实例缓存已变化，请刷新列表后重试", "bad");
    return;
  }
  Object.assign(ntInstanceDetailState, found, { tab: "overview", metricRequest: ntInstanceDetailState.metricRequest + 1 });
  const dialog = ntInstanceDetailDialog();
  ntInstanceDetailRender();
  if (!dialog.open) dialog.showModal();
}

document.addEventListener("click", (event) => {
  const detail = event.target.closest("[data-instance-detail]");
  if (detail) {
    event.preventDefault();
    ntOpenInstanceDetail(detail.dataset.accountId, detail.dataset.instanceId);
    return;
  }
  const close = event.target.closest("[data-detail-close]");
  if (close) {
    ntInstanceDetailState.metricRequest += 1;
    ntInstanceDetailDialog().close();
    return;
  }
  const tab = event.target.closest("[data-detail-tab]");
  if (tab) {
    ntInstanceDetailState.metricRequest += 1;
    ntInstanceDetailState.tab = tab.dataset.detailTab;
    ntInstanceDetailRender();
    return;
  }
  if (event.target.closest("[data-detail-metrics-live]")) {
    ntInstanceDetailLoadMetrics(true);
    return;
  }
  const action = event.target.closest("[data-detail-action]");
  if (action) {
    ntInstanceDetailAction(action);
    return;
  }
  const replaceIp = event.target.closest("[data-detail-replace-ip]");
  if (replaceIp) {
    ntInstanceDetailReplaceIp(replaceIp);
    return;
  }
  if (event.target.closest("[data-detail-edit]")) {
    ntInstanceDetailEdit().catch((error) => {
      toast(`无法打开修改窗口：${error.message}`, "bad");
      const detailDialog = ntInstanceDetailDialog();
      ntInstanceDetailRender();
      if (!detailDialog.open) detailDialog.showModal();
    });
    return;
  }
  const terminate = event.target.closest("[data-detail-terminate]");
  if (terminate) {
    ntInstanceDetailTerminate(terminate);
    return;
  }
  const range = event.target.closest("[data-detail-metric-hours]");
  if (range) {
    ntInstanceDetailState.metricHours = Number(range.dataset.detailMetricHours || 1);
    ntInstanceDetailLoadMetrics(false);
    return;
  }
  if (event.target.closest("[data-detail-metrics-cache]")) {
    ntInstanceDetailLoadMetrics(false);
    return;
  }
  const volumeEdit = event.target.closest("[data-detail-volume-edit]");
  if (volumeEdit) {
    ntInstanceDetailEditVolume(volumeEdit).catch((error) => toast(error.message, "bad"));
    return;
  }
  const volumeBackup = event.target.closest("[data-detail-volume-backup]");
  if (volumeBackup) {
    ntInstanceDetailBackupVolume(volumeBackup).catch((error) => toast(error.message, "bad"));
    return;
  }
  if (event.target.closest("[data-detail-console-refresh]")) {
    ntInstanceDetailLoadConsoleSessions();
    return;
  }
  if (event.target.closest("[data-detail-history-refresh]")) {
    ntInstanceDetailLoadHistory();
    return;
  }
  const related = event.target.closest("[data-detail-open-tab]");
  if (related) {
    const account = ntInstanceDetailState.account;
    if (!account) return;
    const returnPage = state.currentPage || "instances";
    ntInstanceDetailState.metricRequest += 1;
    ntInstanceDetailDialog().close();
    ui2OpenTenant(account.id, related.dataset.detailOpenTab, returnPage);
  }
});
/* END OCI-N&T V1.0.5 1.0.5-instance-detail10 */
