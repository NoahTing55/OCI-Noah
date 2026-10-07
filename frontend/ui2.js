"use strict";

/* OCI-N&T list-oriented management console. */

state.ui2 = {
  accountView: localStorage.getItem("oci_nt_account_view") || "list",
  selectedInstanceAccount: Number(localStorage.getItem("oci_nt_instance_account") || 0) || null,
  selectedLaunchAccount: Number(localStorage.getItem("oci_nt_launch_account") || 0) || null,
  instanceResult: null,
  instanceSelection: new Set(),
  launchProfiles: [],
  launchJobs: [],
  launchLoadErrors: [],
  launchRequestId: 0,
  dashboardInstances: [],
  dashboardJobs: [],
  dashboardLoaded: false,
  accountSort: localStorage.getItem("oci_nt_account_sort_rc312") || "",
  accountRegionFilter: localStorage.getItem("oci_nt_filter_account_region") || "",
  accountPage: 1,
  accountPageSize: localStorage.getItem("oci_nt_account_page_size") || "10",
  instancePage: 1,
  instancePageSize: localStorage.getItem("oci_nt_instance_page_size") || "10",
  accountColumns: (() => { try { return JSON.parse(localStorage.getItem("oci_nt_account_columns") || "null"); } catch { return null; } })() || { tenant: true, proxy: true, type: true, age: true, region: true, status: true },
  instanceColumns: (() => { try { return JSON.parse(localStorage.getItem("oci_nt_instance_columns") || "null"); } catch { return null; } })() || { tenant: true, status: true, shape: true, network: true, region: true, created: true },
};

window.ui2TenantBackPage = "accounts";

function ui2StoredValue(key, fallback = "") {
  const value = localStorage.getItem(key);
  return value === null ? fallback : value;
}

function ui2SaveControlValue(id, key) {
  const element = $(id);
  if (!element) return;
  if (element.value) localStorage.setItem(key, element.value);
  else localStorage.removeItem(key);
}

function ui2PageWindow(items, sizeValue, pageValue) {
  if (sizeValue === "all") return { items: [...items], page: 1, pages: 1, start: 0 };
  const size = Math.max(1, Number(sizeValue || 10));
  const pages = Math.max(1, Math.ceil(items.length / size));
  const page = Math.max(1, Math.min(Number(pageValue || 1), pages));
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), page, pages, start };
}

function ui2UpdatePagination(prefix, page, pages, total, sizeValue) {
  const host = $(`${prefix}-pagination`);
  if (!host) return;
  host.hidden = sizeValue === "all" || total <= Number(sizeValue || 10);
  const copy = $(`${prefix}-page-copy`);
  if (copy) copy.textContent = `第 ${page} / ${pages} 页 · 共 ${total} 项`;
  const previous = host.querySelector(`[data-${prefix}-page="prev"]`);
  const next = host.querySelector(`[data-${prefix}-page="next"]`);
  if (previous) previous.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
}

function ui2ColumnVisible(scope, key) {
  return state.ui2[`${scope}Columns`]?.[key] !== false;
}

function ui2HiddenColumn(scope, key) {
  return ui2ColumnVisible(scope, key) ? "" : " hidden";
}

async function ui2ConfigureColumns(scope) {
  const definitions = scope === "account"
    ? [
        ["tenant", "租户 / 邮箱"], ["proxy", "独立代理"], ["type", "账户类型"],
        ["age", "存活时间"], ["region", "主区域"], ["status", "状态"],
      ]
    : [
        ["tenant", "租户"], ["status", "状态"], ["shape", "配置"],
        ["network", "网络"], ["region", "区域"], ["created", "创建时间"],
      ];
  const property = `${scope}Columns`;
  const values = await openUtilityDialog({
    title: scope === "account" ? "API 表格列设置" : "实例表格列设置",
    copy: "序号、主要名称和操作列始终显示；其他列可按需隐藏。",
    fields: definitions.map(([name, label]) => ({
      name,
      label,
      type: "checkbox",
      value: ui2ColumnVisible(scope, name),
      full: true,
    })),
    submitText: "保存列设置",
  });
  if (!values) return;
  state.ui2[property] = Object.fromEntries(definitions.map(([name]) => [name, Boolean(values[name])]));
  localStorage.setItem(`oci_nt_${scope}_columns`, JSON.stringify(state.ui2[property]));
  if (scope === "account") renderAccounts();
  else ui2RenderCurrentInstanceResult();
}

async function ui2CopyText(button) {
  const value = String(button?.dataset?.copyText || "");
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    toast(button.dataset.copyLabel ? `${button.dataset.copyLabel}已复制` : "内容已复制", "good");
  } catch {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast(button.dataset.copyLabel ? `${button.dataset.copyLabel}已复制` : "内容已复制", "good");
  }
}

function ui2AccountById(value) {
  return state.accounts.find((item) => Number(item.id) === Number(value)) || null;
}

function ui2CompactId(value) {
  const text = String(value || "");
  if (text.length <= 30) return text || "—";
  return `${text.slice(0, 17)}…${text.slice(-10)}`;
}

function ui2SelectedAccount(selectId) {
  const select = $(selectId);
  return select ? ui2AccountById(select.value) : null;
}

function ui2LaunchAccountForElement(element = null) {
  const explicitId = element?.dataset?.ui2AccountId;
  return ui2AccountById(explicitId) || ui2SelectedAccount("global-launch-account");
}

function ui2UpdateLaunchScopeState() {
  const account = ui2SelectedAccount("global-launch-account");
  const scopedButtons = [
    ["global-launch-create", "请先选择一个租户再添加配置"],
    ["global-profiles-enable", "请先选择一个租户再批量启用"],
    ["global-profiles-disable", "请先选择一个租户再批量停用"],
    ["global-jobs-reset", "请先选择一个租户再重置统计"],
    ["global-jobs-cancel", "请先选择一个租户再取消任务"],
  ];
  scopedButtons.forEach(([id, emptyTitle]) => {
    const button = $(id);
    if (!button) return;
    button.disabled = !account;
    button.title = account ? `${account.custom_name || "所选租户"}` : emptyTitle;
  });
  const profileCopy = document.querySelector(".launch-summary-profile small");
  if (profileCopy) profileCopy.textContent = account ? "当前租户本地配置" : "全部租户本地配置";
}

function ui2FillSelector(id, preferredId = null) {
  const select = $(id);
  if (!select) return null;
  const previous = preferredId === null || preferredId === undefined
    ? String(select.value || "")
    : String(preferredId || "");
  select.innerHTML = '<option value="">全部租户</option>' + state.accounts.map((account) =>
    `<option value="${account.id}">${esc(account.custom_name)} · ${esc(account.email || "未读取邮箱")}</option>`
  ).join("");
  const target = state.accounts.some((item) => String(item.id) === previous) ? previous : "";
  select.value = target;
  ui2UpdateLaunchScopeState();
  return target ? Number(target) : null;
}

function ui2FillInstanceSelector(preferredId = null) {
  const select = $("global-instance-account");
  if (!select) return null;
  const previous = preferredId === null || preferredId === undefined ? select.value : String(preferredId || "");
  select.innerHTML = '<option value="">全部租户</option>' + state.accounts.map((account) =>
    `<option value="${account.id}">${esc(account.custom_name)} · ${esc(account.email || "未读取邮箱")}</option>`
  ).join("");
  const target = state.accounts.some((item) => String(item.id) === String(previous)) ? String(previous) : "";
  select.value = target;
  ui2UpdateInstanceSyncState();
  return target ? Number(target) : null;
}

function ui2UpdateInstanceSyncState() {
  const button = $("global-instance-sync");
  const account = ui2SelectedAccount("global-instance-account");
  if (!button) return;
  button.disabled = !account;
  button.title = account ? `同步 ${account.custom_name || "所选租户"}` : "请先选择一个租户再同步";
}

function ui2StatusBadge(status) {
  const [text, cls] = accountStatus(status);
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function ui2TaskStatus(status) {
  const text = launchStatusLabel(status);
  const cls = status === "COMPLETED" ? "good" : status === "FAILED" ? "bad" : ["RUNNING", "PENDING", "WAITING", "CANCELLING"].includes(status) ? "warn" : "muted";
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function ui2OpenTenant(accountId, tab = "instances", returnPage = state.currentPage || "accounts") {
  window.ui2TenantBackPage = returnPage;
  openTenant(Number(accountId), tab);
}

const UI2_REGION_CITY_NAMES = Object.freeze({
  "af-johannesburg-1": ["约翰内斯堡", "Johannesburg"],
  "ap-chuncheon-1": ["春川", "Chuncheon"],
  "ap-hyderabad-1": ["海得拉巴", "Hyderabad"],
  "ap-melbourne-1": ["墨尔本", "Melbourne"],
  "ap-mumbai-1": ["孟买", "Mumbai"],
  "ap-osaka-1": ["大阪", "Osaka"],
  "ap-seoul-1": ["首尔", "Seoul"],
  "ap-singapore-1": ["新加坡", "Singapore"],
  "ap-singapore-2": ["新加坡 2", "Singapore 2"],
  "ap-sydney-1": ["悉尼", "Sydney"],
  "ap-tokyo-1": ["东京", "Tokyo"],
  "ca-montreal-1": ["蒙特利尔", "Montreal"],
  "ca-toronto-1": ["多伦多", "Toronto"],
  "eu-amsterdam-1": ["阿姆斯特丹", "Amsterdam"],
  "eu-frankfurt-1": ["法兰克福", "Frankfurt"],
  "eu-madrid-1": ["马德里", "Madrid"],
  "eu-marseille-1": ["马赛", "Marseille"],
  "eu-milan-1": ["米兰", "Milan"],
  "eu-paris-1": ["巴黎", "Paris"],
  "eu-stockholm-1": ["斯德哥尔摩", "Stockholm"],
  "eu-zurich-1": ["苏黎世", "Zurich"],
  "il-jerusalem-1": ["耶路撒冷", "Jerusalem"],
  "me-abudhabi-1": ["阿布扎比", "Abu Dhabi"],
  "me-dubai-1": ["迪拜", "Dubai"],
  "me-jeddah-1": ["吉达", "Jeddah"],
  "mx-monterrey-1": ["蒙特雷", "Monterrey"],
  "mx-queretaro-1": ["克雷塔罗", "Queretaro"],
  "sa-bogota-1": ["波哥大", "Bogota"],
  "sa-santiago-1": ["圣地亚哥", "Santiago"],
  "sa-saopaulo-1": ["圣保罗", "Sao Paulo"],
  "sa-vinhedo-1": ["维涅杜", "Vinhedo"],
  "uk-cardiff-1": ["卡迪夫", "Cardiff"],
  "uk-london-1": ["伦敦", "London"],
  "us-ashburn-1": ["阿什本", "Ashburn"],
  "us-chicago-1": ["芝加哥", "Chicago"],
  "us-phoenix-1": ["凤凰城", "Phoenix"],
  "us-sanjose-1": ["圣何塞", "San Jose"],
});

function ui2ActiveAccountTypeFilter() {
  return $("account-type-filter")?.value || "";
}

function ui2ActiveAccountSort() {
  const allowed = new Set(["", "tenancy_asc", "tenancy_desc", "survival_asc", "survival_desc"]);
  return allowed.has(state.ui2.accountSort) ? state.ui2.accountSort : "";
}

function ui2SyncAccountSortControl() {
  const control = $("account-sort-filter");
  if (!control) return;
  const mode = ui2ActiveAccountSort();
  control.querySelectorAll("[data-account-sort-key]").forEach((button) => {
    const key = button.dataset.accountSortKey;
    const active = mode === `${key}_asc` || mode === `${key}_desc`;
    const direction = active ? (mode.endsWith("_asc") ? "↑" : "↓") : "↕";
    const indicator = button.querySelector("b");
    if (indicator) indicator.textContent = direction;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.title = active
      ? `${key === "tenancy" ? "租户" : "存活时间"}${direction === "↑" ? "正序" : "反序"}；再次点击切换方向`
      : `按${key === "tenancy" ? "租户" : "存活时间"}正序排列`;
  });
}

function ui2ToggleAccountSort(key) {
  if (key !== "tenancy" && key !== "survival") return;
  const current = ui2ActiveAccountSort();
  const ascending = `${key}_asc`;
  const descending = `${key}_desc`;
  state.ui2.accountSort = current === ascending ? descending : ascending;
  localStorage.setItem("oci_nt_account_sort_rc312", state.ui2.accountSort);
  ui2SyncAccountSortControl();
  renderAccounts();
}

function ui2AccountRegionCode(account) {
  const homeName = String(account.home_region_name || "").trim();
  if (/^[a-z]{2,4}-[a-z0-9-]+-\d+$/i.test(homeName)) return homeName;
  const configured = String(account.region || "").trim();
  if (configured) return configured;
  return String(account.home_region_key || homeName || "").trim();
}

function ui2FriendlyRegionName(code, account = null) {
  const normalized = String(code || "").trim().toLowerCase();
  const mapped = UI2_REGION_CITY_NAMES[normalized];
  if (mapped) return `${mapped[0]}（${mapped[1]}）`;
  const source = String(account?.home_region_name || "").trim();
  if (source && source.toLowerCase() !== normalized && !/^\w{2,4}-[\w-]+-\d+$/.test(source)) return source;
  const parts = normalized.split("-");
  if (parts.length >= 3) {
    return parts.slice(1, -1).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
  }
  return code || "未读取";
}

function ui2FormatCompactDate(value) {
  if (!value) return "尚未同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function ui2PopulateAccountFilters() {
  const select = $("account-region-filter");
  if (!select) return;
  const previous = select.value;
  const typeFilter = ui2ActiveAccountTypeFilter();
  const regions = new Map();
  state.accounts
    .filter((account) => !typeFilter || account.account_type === typeFilter)
    .forEach((account) => {
      const value = ui2AccountRegionCode(account);
      if (!value) return;
      regions.set(value, {
        label: ui2FriendlyRegionName(value, account),
        title: [account.home_region_name, value].filter(Boolean).join(" · "),
      });
    });
  select.innerHTML = '<option value="">全部区域</option>' + [...regions.entries()]
    .sort((a, b) => a[1].label.localeCompare(b[1].label, "en", { sensitivity: "base" }))
    .map(([value, item]) => `<option value="${esc(value)}" title="${esc(item.title)}">${esc(item.label)}</option>`).join("");
  select.value = [...select.options].some((option) => option.value === previous) ? previous : "";
}



function ui2RegistrationDate(account) {
  const value = account?.registration_time || account?.subscription_time_start;
  return value ? fmtDate(value, false) : "未读取";
}

function ui2RegistrationDateTime(account) {
  const value = account?.registration_time || account?.subscription_time_start;
  return value ? fmtDate(value) : "OCI 未返回订阅开始时间";
}

function ui2ComparableSurvival(account) {
  const raw = account.survival_days;
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function ui2CompareNullableNumber(a, b, descending = false) {
  const aMissing = a === null || a === undefined || !Number.isFinite(a);
  const bMissing = b === null || b === undefined || !Number.isFinite(b);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  return descending ? b - a : a - b;
}

function ui2ComparableTenancy(account) {
  return String(account.tenancy_name || account.custom_name || account.email || "").trim();
}

function ui2SortAccounts(accounts) {
  const mode = ui2ActiveAccountSort();
  state.ui2.accountSort = mode;
  if (mode) localStorage.setItem("oci_nt_account_sort_rc312", mode);
  else localStorage.removeItem("oci_nt_account_sort_rc312");
  ui2SyncAccountSortControl();
  if (!mode) return [...accounts];
  const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
  return accounts.map((account, index) => ({ account, index })).sort((left, right) => {
    const a = left.account;
    const b = right.account;
    let result = 0;
    if (mode === "tenancy_asc" || mode === "tenancy_desc") {
      result = collator.compare(ui2ComparableTenancy(a), ui2ComparableTenancy(b));
      if (mode === "tenancy_desc") result *= -1;
    } else {
      result = ui2CompareNullableNumber(ui2ComparableSurvival(a), ui2ComparableSurvival(b), mode === "survival_desc");
    }
    if (result === 0) result = collator.compare(String(a.custom_name || ""), String(b.custom_name || ""));
    if (result === 0) result = left.index - right.index;
    return result;
  }).map((item) => item.account);
}

window.ui2AccountsChanged = function ui2AccountsChanged() {
  const currentInstance = ui2FillInstanceSelector(state.ui2.selectedInstanceAccount);
  const currentLaunch = ui2FillSelector("global-launch-account", state.ui2.selectedLaunchAccount);
  state.ui2.selectedInstanceAccount = currentInstance;
  state.ui2.selectedLaunchAccount = currentLaunch;
  ui2PopulateAccountFilters();
  if ($("account-region-filter") && state.ui2.accountRegionFilter && [...$("account-region-filter").options].some((option) => option.value === state.ui2.accountRegionFilter)) {
    $("account-region-filter").value = state.ui2.accountRegionFilter;
  }
  if (state.currentPage === "accounts") renderAccounts();
  if (state.currentPage === "dashboard") ui2LoadDashboard();
};

window.ui2PageOpened = function ui2PageOpened(page) {
  if (page === "dashboard") ui2LoadDashboard();
  if (page === "accounts") renderAccounts();
  if (page === "instances") {
    state.ui2.selectedInstanceAccount = ui2FillInstanceSelector(state.ui2.selectedInstanceAccount);
    ui2LoadGlobalInstances(false);
  }
  if (page === "launch") {
    state.ui2.selectedLaunchAccount = ui2FillSelector("global-launch-account", state.ui2.selectedLaunchAccount);
    ui2LoadGlobalLaunch();
  }
};

window.ui2RenderAccounts = function ui2RenderAccounts() {
  const box = $("account-list");
  if (!box) return false;
  const aliveCount = state.accounts.filter((account) => account.account_status === "ALIVE").length;
  const proxyCount = state.accounts.filter((account) => account.proxy_enabled && account.has_proxy).length;
  const abnormalCount = state.accounts.length - aliveCount;
  if ($("api-summary-total")) $("api-summary-total").textContent = String(state.accounts.length);
  if ($("api-summary-alive")) $("api-summary-alive").textContent = String(aliveCount);
  if ($("api-summary-proxy")) $("api-summary-proxy").textContent = String(proxyCount);
  if ($("api-summary-abnormal")) $("api-summary-abnormal").textContent = String(abnormalCount);
  const query = ($("account-search")?.value || "").trim().toLowerCase();
  const accountTypeFilter = ui2ActiveAccountTypeFilter();
  const regionFilter = $("account-region-filter")?.value || "";
  const filteredAccounts = state.accounts.filter((account) => {
    const content = [account.custom_name, account.email, account.tenancy_name,
      account.user_name, account.home_region_name, account.home_region_key, account.region]
      .filter(Boolean).join(" ").toLowerCase();
    const accountRegion = ui2AccountRegionCode(account);
    return (!accountTypeFilter || account.account_type === accountTypeFilter)
      && (!regionFilter || accountRegion === regionFilter)
      && (!query || content.includes(query));
  });
  const accounts = ui2SortAccounts(filteredAccounts);
  const accountWindow = ui2PageWindow(accounts, state.ui2.accountPageSize, state.ui2.accountPage);
  state.ui2.accountPage = accountWindow.page;
  const visibleAccounts = accountWindow.items;
  ui2UpdatePagination("account", accountWindow.page, accountWindow.pages, accounts.length, state.ui2.accountPageSize);
  if ($("account-filter-count")) $("account-filter-count").textContent = `${accounts.length} / ${state.accounts.length} 个账户`;
  box.classList.toggle("card-mode", state.ui2.accountView === "card");
  $("account-view-list")?.classList.toggle("active", state.ui2.accountView === "list");
  $("account-view-card")?.classList.toggle("active", state.ui2.accountView === "card");
  if (!accounts.length) {
    box.innerHTML = `<div class="empty">${state.accounts.length ? "没有匹配账户" : "暂无 OCI API"}</div>`;
    return true;
  }

  if (state.ui2.accountView === "card") {
    box.innerHTML = visibleAccounts.map((account, index) => {
      const [statusText, statusClass] = accountStatus(account);
      return `<article class="api-card"><span class="card-sequence">${accountWindow.start + index + 1}</span>
        <header><div class="account-avatar">${esc((account.custom_name || "N")[0].toUpperCase())}</div><div><strong>${esc(account.custom_name)}</strong><small>${esc(account.email || "未读取邮箱")}</small></div>${ui2AccountMenu(account)}</header>
        <dl><div><dt>租户</dt><dd>${esc(account.tenancy_name || ui2CompactId(account.tenancy_ocid))}<button class="copy-inline-button" data-copy-text="${esc(account.tenancy_ocid || "")}" data-copy-label="租户 OCID" type="button" aria-label="复制租户 OCID">⧉</button></dd></div><div><dt>独立代理</dt><dd><button class="proxy-inline-select ${account.proxy_enabled ? "is-enabled" : ""}" data-config-proxy="${account.id}" type="button">${esc(account.proxy_enabled ? (account.proxy_profile_name || account.proxy_label || "已启用") : "未启用")}</button><small>${esc(account.proxy_enabled ? (account.proxy_last_ip || "尚未测试出口 IP") : (account.proxy_profile_name || account.proxy_label || "点击选择代理"))}</small></dd></div><div><dt>账户类型</dt><dd>${esc(accountType(account))}</dd></div><div><dt>存活时间</dt><dd class="api-card-age">${esc(survival(account))}<small title="${esc(ui2RegistrationDateTime(account))}">注册 ${esc(ui2RegistrationDate(account))}</small></dd></div><div><dt>主区域</dt><dd>${esc(ui2FriendlyRegionName(ui2AccountRegionCode(account), account))}<small>${esc(account.home_region_key || account.region || "—")}</small></dd></div></dl>
        <footer><span class="badge ${statusClass}" title="${esc(account.last_error || "")}">${statusText}</span><button class="button" data-config-proxy="${account.id}" type="button">代理</button><button class="button" data-ui2-create="${account.id}" type="button">开机任务</button><button class="button primary" data-ui2-manage="${account.id}" type="button">管理</button></footer>
      </article>`;
    }).join("");
    return true;
  }

  box.innerHTML = `<div class="table-wrap api-table-wrap${accounts.length > 8 ? " is-scrollable" : ""}"><table class="api-table"><thead><tr><th>序号</th><th>API 名称</th><th${ui2HiddenColumn("account", "tenant")}>租户 / 邮箱</th><th${ui2HiddenColumn("account", "proxy")}>独立代理</th><th${ui2HiddenColumn("account", "type")}>账户类型</th><th${ui2HiddenColumn("account", "age")}>存活时间</th><th${ui2HiddenColumn("account", "region")}>主区域</th><th${ui2HiddenColumn("account", "status")}>状态</th><th>操作</th></tr></thead><tbody>${visibleAccounts.map((account, index) => {
    const [statusText, statusClass] = accountStatus(account);
    const proxyName = account.proxy_profile_name || account.proxy_label || "未选择代理";
    return `<tr>
      <td class="sequence-cell"><span>${accountWindow.start + index + 1}</span></td>
      <td class="api-name-cell"><button class="row-title-button" data-ui2-manage="${account.id}" type="button">${esc(account.custom_name)}</button><small class="mono copyable-line" title="${esc(account.user_ocid || account.user || "")}">${esc(ui2CompactId(account.user_ocid || account.user))}<button class="copy-inline-button" data-copy-text="${esc(account.user_ocid || account.user || "")}" data-copy-label="用户 OCID" type="button" aria-label="复制用户 OCID">⧉</button></small></td>
      <td class="api-tenant-cell"${ui2HiddenColumn("account", "tenant")}><strong>${esc(account.tenancy_name || "未读取租户")}</strong><small title="${esc(account.email || "")}">${esc(account.email || "未读取邮箱")}</small></td>
      <td class="api-proxy-cell"${ui2HiddenColumn("account", "proxy")}><button class="proxy-inline-select ${account.proxy_enabled ? "is-enabled" : ""}" data-config-proxy="${account.id}" type="button">${esc(account.proxy_enabled ? proxyName : "未启用")}</button><small>${esc(account.proxy_enabled ? (account.proxy_last_ip || "尚未测试") : (account.proxy_profile_id ? `已选择：${proxyName}` : "点击选择代理"))}</small></td>
      <td${ui2HiddenColumn("account", "type")}>${esc(accountType(account))}</td>
      <td class="account-age-cell"${ui2HiddenColumn("account", "age")}><strong>${esc(survival(account))}</strong><small title="${esc(ui2RegistrationDateTime(account))}">注册 ${esc(ui2RegistrationDate(account))}</small></td>
      <td class="api-region-cell"${ui2HiddenColumn("account", "region")}><strong>${esc(ui2FriendlyRegionName(ui2AccountRegionCode(account), account))}</strong><small class="mono">${esc(account.home_region_key || account.region || "—")}</small></td>
      <td class="api-status-cell"${ui2HiddenColumn("account", "status")}><span class="badge ${statusClass}" title="${esc(account.last_error || "")}">${statusText}</span><small>${fmtDate(account.last_checked_at)}</small></td>
      <td><div class="row-actions compact"><button class="icon-action" aria-label="开机任务" data-action-tooltip="开机任务" data-ui2-create="${account.id}" type="button">＋</button><button class="icon-action" aria-label="实例" data-action-tooltip="实例" data-ui2-account-instances="${account.id}" type="button">☁</button><button class="icon-action" aria-label="检测" data-action-tooltip="检测" data-check-account="${account.id}" type="button">✓</button><button class="button primary small" data-ui2-manage="${account.id}" type="button">管理</button>${ui2AccountMenu(account)}</div></td>
    </tr>`;
  }).join("")}</tbody></table></div>`;
  return true;
};

function ui2AccountMenu(account) {
  return `<div class="more-menu"><button class="more-toggle" data-ui2-account-menu="${account.id}" data-action-tooltip="更多操作" type="button" aria-label="更多操作" aria-expanded="false">⋮</button></div>`;
}

function ui2AccountMenuPopover(accountId) {
  let box = document.getElementById("ui2-account-menu-popover");
  if (!box) {
    box = document.createElement("div");
    box.id = "ui2-account-menu-popover";
    box.className = "more-popover account-more-popover account-more-popover-global";
    document.body.appendChild(box);
  }
  box.innerHTML = `
    <button data-ui2-account-launch="${accountId}" type="button">OCI 开机管理</button>
    <button data-ui2-rename="${accountId}" type="button">修改自定义名称</button>
    <button data-ui2-manage="${accountId}" data-ui2-tab="tenancy" type="button">区域与 IAM</button>
    <button class="danger-text" data-delete-account="${accountId}" type="button">删除 API</button>`;
  box.dataset.accountId = String(accountId);
  box.hidden = true;
  return box;
}

function ui2DashboardInstanceState(instance) {
  const stateName = String(instance?.lifecycle_state || "UNKNOWN").toUpperCase();
  const labels = {
    RUNNING: ["运行中", "good"],
    STARTING: ["启动中", "warn"],
    STOPPING: ["停止中", "warn"],
    STOPPED: ["已停止", "muted"],
    TERMINATING: ["终止中", "warn"],
    TERMINATED: ["已终止", "bad"],
  };
  return labels[stateName] || [stateName === "UNKNOWN" ? "未知" : stateName, "muted"];
}

async function ui2LoadDashboard() {
  if (!state.token || !$('dashboard-page') || $('dashboard-page').hidden) return;

  const accountTotal = state.accounts.length;
  const alive = state.accounts.filter((account) => account.account_status === 'ALIVE').length;
  const pendingOrAbnormal = Math.max(0, accountTotal - alive);

  $('dash-account-count').textContent = String(accountTotal);
  $('dash-alive-count').textContent = String(alive);
  $('dash-account-copy').textContent = accountTotal ? `${pendingOrAbnormal} 个待检测或异常` : '尚未导入 OCI API';
  $('dash-alive-copy').textContent = accountTotal ? `有效率 ${Math.round((alive / accountTotal) * 100)}%` : '检测后显示有效状态';
  if ($('dash-account-list-count')) $('dash-account-list-count').textContent = `${accountTotal} 个账户`;

  $('dashboard-account-list').innerHTML = accountTotal
    ? state.accounts.slice(0, 8).map((account) => {
      const [statusText, statusClass] = accountStatus(account);
      return `<button class="dashboard-list-row dashboard-account-row" data-ui2-manage="${account.id}" type="button">
        <span class="status-dot ${statusClass}" aria-hidden="true"></span>
        <span class="dashboard-row-main"><strong>${esc(account.custom_name)}</strong><small>${esc(account.email || '未读取邮箱')}</small></span>
        <span class="dashboard-row-meta"><strong>${esc(regionName(account))}</strong><small>${esc(accountType(account))}</small></span>
        <span class="badge ${statusClass}">${statusText}</span>
      </button>`;
    }).join('')
    : '<div class="empty dashboard-empty"><strong>暂无 OCI API</strong><span>前往 API 管理导入账户后，这里会显示本地状态概览。</span></div>';

  if (!accountTotal) {
    $('dash-instance-count').textContent = '0';
    $('dash-instance-copy').textContent = '尚无实例缓存';
    $('dash-job-count').textContent = '0';
    $('dash-job-copy').textContent = '尚无本地任务记录';
    if ($('dash-instance-list-count')) $('dash-instance-list-count').textContent = '0 个实例';
    $('dashboard-instance-list').innerHTML = '<div class="empty dashboard-empty"><strong>暂无实例缓存</strong><span>在实例管理中同步租户后，这里会显示最近实例。</span></div>';
    return;
  }

  try {
    const results = await Promise.all(state.accounts.map(async (account) => {
      const [instances, jobs] = await Promise.all([
        api(`/accounts/${account.id}/instances`).catch(() => ({ instances: [] })),
        api(`/accounts/${account.id}/launch/jobs?limit=20`).catch(() => []),
      ]);
      return { account, instances: instances.instances || [], jobs: jobs || [] };
    }));
    if ($('dashboard-page').hidden) return;

    state.ui2.dashboardInstances = results.flatMap((result) => result.instances.map((item) => ({ ...item, account_id: result.account.id, account_name: result.account.custom_name })));
    state.ui2.dashboardJobs = results.flatMap((result) => result.jobs.map((item) => ({ ...item, account_name: result.account.custom_name })));

    const runningInstances = state.ui2.dashboardInstances.filter((item) => String(item.lifecycle_state).toUpperCase() === 'RUNNING').length;
    const runningJobs = state.ui2.dashboardJobs.filter((job) => job.is_active).length;
    const instanceTotal = state.ui2.dashboardInstances.length;

    $('dash-instance-count').textContent = String(instanceTotal);
    $('dash-instance-copy').textContent = instanceTotal ? `${runningInstances} 台运行中` : '尚无实例缓存';
    $('dash-job-count').textContent = String(runningJobs);
    $('dash-job-copy').textContent = `${state.ui2.dashboardJobs.length} 条本地任务记录`;
    if ($('dash-instance-list-count')) $('dash-instance-list-count').textContent = `${instanceTotal} 个实例`;

    const recent = state.ui2.dashboardInstances.slice(0, 8);
    $('dashboard-instance-list').innerHTML = recent.length
      ? recent.map((instance) => {
        const [stateText, stateClass] = ui2DashboardInstanceState(instance);
        return `<button class="dashboard-list-row dashboard-instance-row" data-ui2-account-instances="${instance.account_id}" type="button">
          <span class="status-dot ${stateClass}" aria-hidden="true"></span>
          <span class="dashboard-row-main"><strong>${esc(instance.display_name || 'N&T')}</strong><small>${esc(instance.account_name)}</small></span>
          <span class="dashboard-row-meta"><strong>${esc(instance.shape || '—')}</strong><small>${esc(instance.region || '—')}</small></span>
          <span class="badge ${stateClass}">${esc(stateText)}</span>
        </button>`;
      }).join('')
      : '<div class="empty dashboard-empty"><strong>暂无实例缓存</strong><span>在实例管理中同步租户后，这里会显示最近实例。</span></div>';
  } catch (error) {
    if ($('dash-instance-list-count')) $('dash-instance-list-count').textContent = '读取失败';
    $('dashboard-instance-list').innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  }
}
function ui2InstanceFilterState() {
  return {
    accountId: $("global-instance-account")?.value || "",
    status: $("global-instance-status")?.value || "",
    query: ($("global-instance-search")?.value || "").trim().toLowerCase(),
  };
}

function ui2ResetInstanceOverview() {
  state.ui2.instancePageItems = [];
  if ($("instance-pagination")) $("instance-pagination").hidden = true;
  if ($("instance-summary-total")) $("instance-summary-total").textContent = "0";
  if ($("instance-summary-running")) $("instance-summary-running").textContent = "0";
  if ($("instance-summary-stopped")) $("instance-summary-stopped").textContent = "0";
  if ($("instance-summary-public-ip")) $("instance-summary-public-ip").textContent = "0";
  if ($("instance-filter-count")) $("instance-filter-count").textContent = "0 / 0 台实例";
}

function ui2InstanceAccount(instance) {
  return ui2AccountById(instance?.account_id) || null;
}

function ui2InstanceSelectionKey(accountId, instanceId) {
  return `${Number(accountId)}::${String(instanceId || "")}`;
}

function ui2AllCachedInstances() {
  return state.ui2.instanceResult?.instances || [];
}

function ui2SelectedInstances() {
  const selected = state.ui2.instanceSelection;
  return ui2AllCachedInstances().filter((instance) => selected.has(
    ui2InstanceSelectionKey(instance.account_id, instance.id)
  ));
}

function ui2PruneInstanceSelection(instances = ui2AllCachedInstances()) {
  const available = new Set(instances.map((instance) =>
    ui2InstanceSelectionKey(instance.account_id, instance.id)
  ));
  for (const key of state.ui2.instanceSelection) {
    if (!available.has(key)) state.ui2.instanceSelection.delete(key);
  }
}

function ui2ClearInstanceSelection(render = true) {
  state.ui2.instanceSelection.clear();
  if (render && state.ui2.instanceResult) ui2RenderCurrentInstanceResult();
  else ui2UpdateInstanceBatchToolbar();
}

function ui2UpdateInstanceBatchToolbar(visibleInstances = null) {
  const toolbar = $("instance-batch-toolbar");
  const count = $("instance-batch-count");
  const selectedInstances = ui2SelectedInstances();
  if (toolbar) toolbar.hidden = selectedInstances.length === 0;
  if (count) {
    const tenants = new Set(selectedInstances.map((item) => Number(item.account_id))).size;
    count.textContent = `已选择 ${selectedInstances.length} 台实例 · ${tenants} 个租户`;
  }

  const rows = Array.isArray(visibleInstances)
    ? visibleInstances
    : ui2FilteredInstances(ui2ScopedInstances(ui2AllCachedInstances()));
  const visibleKeys = rows.map((instance) =>
    ui2InstanceSelectionKey(instance.account_id, instance.id)
  );
  const checked = visibleKeys.filter((key) => state.ui2.instanceSelection.has(key)).length;
  const selectAll = $("instance-select-all");
  if (selectAll) {
    selectAll.checked = visibleKeys.length > 0 && checked === visibleKeys.length;
    selectAll.indeterminate = checked > 0 && checked < visibleKeys.length;
    selectAll.disabled = visibleKeys.length === 0;
  }
}

function ui2InstanceBatchOperationLabel(operation) {
  const labels = {
    START: "批量开机",
    SOFTSTOP: "批量关机",
    SOFTRESET: "批量重启",
    REPLACE_PUBLIC_IP: "批量更换公网 IP",
    SYNC_ACCOUNTS: "同步所选租户",
  };
  return labels[operation] || operation;
}

function ui2InstanceBatchPayload(operation) {
  const selected = ui2SelectedInstances();
  if (operation === "SYNC_ACCOUNTS") {
    const accounts = new Map();
    selected.forEach((instance) => {
      const account = ui2InstanceAccount(instance);
      const accountId = Number(instance.account_id || account?.id || 0);
      if (!accountId || accounts.has(accountId)) return;
      accounts.set(accountId, {
        account_id: accountId,
        account_name: account?.custom_name || instance.account_name || `租户 ${accountId}`,
      });
    });
    return { items: [...accounts.values()], skipped: 0 };
  }

  const items = [];
  let skipped = 0;
  selected.forEach((instance) => {
    const account = ui2InstanceAccount(instance);
    const stateName = String(instance.lifecycle_state || "UNKNOWN").toUpperCase();
    const vnic = primaryVnic(instance);
    const compatible = operation === "START"
      ? stateName === "STOPPED"
      : ["SOFTSTOP", "SOFTRESET"].includes(operation)
        ? stateName === "RUNNING"
        : operation === "REPLACE_PUBLIC_IP"
          ? Boolean(vnic?.private_ip_id)
          : false;
    if (!compatible) {
      skipped += 1;
      return;
    }
    items.push({
      account_id: Number(instance.account_id || account?.id || 0),
      account_name: account?.custom_name || instance.account_name || null,
      instance_id: instance.id,
      display_name: instance.display_name || "N&T",
      region: instance.region || account?.region || null,
      private_ip_id: vnic?.private_ip_id || null,
    });
  });
  return { items, skipped };
}

async function ui2StartInstanceBatch(operation, button = null) {
  const selected = ui2SelectedInstances();
  if (!selected.length) {
    toast("请先选择实例", "bad");
    return;
  }
  const { items, skipped } = ui2InstanceBatchPayload(operation);
  if (!items.length) {
    const requirement = operation === "START"
      ? "请选择已停止的实例"
      : ["SOFTSTOP", "SOFTRESET"].includes(operation)
        ? "请选择运行中的实例"
        : operation === "REPLACE_PUBLIC_IP"
          ? "所选实例没有可更换公网 IP 的私网 IP"
          : "没有可执行项目";
    toast(requirement, "bad");
    return;
  }
  const tenantCount = new Set(items.map((item) => item.account_id)).size;
  const names = items.slice(0, 6).map((item) => item.display_name || item.account_name).join("、");
  const message = [
    `将执行：${ui2InstanceBatchOperationLabel(operation)}`,
    `影响范围：${tenantCount} 个租户 · ${items.length} 个项目`,
    names ? `项目：${names}${items.length > 6 ? ` 等 ${items.length} 项` : ""}` : "",
    skipped ? `因状态或资源不兼容，将跳过 ${skipped} 台实例。` : "",
    "任务将按串行低并发执行，可在任务中心查看、取消和重试失败项。",
  ].filter(Boolean).join("\n");
  const confirmed = await openConfirmDialog({
    title: ui2InstanceBatchOperationLabel(operation),
    message,
    submitText: "创建任务",
    danger: operation === "REPLACE_PUBLIC_IP",
  });
  if (!confirmed) return;

  setBusy(button, true, "创建中……");
  try {
    const task = await api("/tasks/instance-batch", {
      method: "POST",
      body: { operation, items, interval_seconds: 2, idempotency_key: createIdempotencyKey(`instance-${operation.toLowerCase()}`) },
    });
    ui2ClearInstanceSelection(false);
    toast(task.deduplicated ? `相同操作任务已存在（#${task.id}）` : `任务 #${task.id} 已创建`, "good");
    showPage("tasks");
    await loadTasks(null, true);
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

function ui2ScopedInstances(instances) {
  const accountId = $("global-instance-account")?.value || "";
  return accountId ? instances.filter((instance) => String(instance.account_id) === String(accountId)) : [...instances];
}

function ui2FilteredInstances(instances) {
  const { status, query } = ui2InstanceFilterState();
  return instances.filter((instance) => {
    const account = ui2InstanceAccount(instance);
    const stateName = String(instance.lifecycle_state || "UNKNOWN").toUpperCase();
    const statusMatches = !status
      || stateName === status
      || (status === "OTHER" && !["RUNNING", "STOPPED"].includes(stateName));
    if (!statusMatches) return false;
    if (!query) return true;
    const vnic = primaryVnic(instance);
    const region = instance.region || account?.region || "";
    const haystack = [
      account?.custom_name,
      account?.email,
      account?.tenancy_name,
      instance.account_name,
      instance.account_email,
      instance.display_name,
      instance.id,
      instance.shape,
      instance.lifecycle_state,
      region,
      ui2FriendlyRegionName(region, account),
      vnic?.public_ip,
      vnic?.private_ip,
      instance.ocpus,
      instance.memory_in_gbs,
    ].map((value) => String(value ?? "").toLowerCase()).join(" ");
    return haystack.includes(query);
  });
}

function ui2UpdateInstanceOverview(instances, filteredInstances) {
  const running = instances.filter((instance) => String(instance.lifecycle_state || "").toUpperCase() === "RUNNING").length;
  const stopped = instances.filter((instance) => String(instance.lifecycle_state || "").toUpperCase() === "STOPPED").length;
  const publicIpCount = instances.filter((instance) => Boolean(primaryVnic(instance)?.public_ip)).length;
  if ($("instance-summary-total")) $("instance-summary-total").textContent = String(instances.length);
  if ($("instance-summary-running")) $("instance-summary-running").textContent = String(running);
  if ($("instance-summary-stopped")) $("instance-summary-stopped").textContent = String(stopped);
  if ($("instance-summary-public-ip")) $("instance-summary-public-ip").textContent = String(publicIpCount);
  if ($("instance-filter-count")) $("instance-filter-count").textContent = `${filteredInstances.length} / ${instances.length} 台实例`;
}

async function ui2LoadGlobalInstances(sync = false) {
  const account = ui2SelectedAccount("global-instance-account");
  state.ui2.selectedInstanceAccount = account?.id || null;
  ui2UpdateInstanceSyncState();
  if (sync && !account) {
    toast("请先选择一个租户再同步", "bad");
    return;
  }
  if (!state.accounts.length) {
    state.ui2.instanceSelection.clear();
    ui2UpdateInstanceBatchToolbar([]);
    ui2ResetInstanceOverview();
    if ($("global-instance-summary")) $("global-instance-summary").innerHTML = '<strong>暂无账户</strong><span>请先在 API 管理中导入 OCI 账户。</span>';
    $("global-instance-list").innerHTML = '<div class="empty instance-empty">暂无 OCI 账户</div>';
    return;
  }
  state.ui2.instanceResult = null;
  $("global-instance-list").innerHTML = `<div class="empty instance-empty">${sync ? "正在从 OCI 同步所选租户……" : "正在读取全部租户本地缓存……"}</div>`;
  try {
    if (sync) await api(`/accounts/${account.id}/instances/sync`, { method: "POST" });
    const result = await api("/instances");
    state.ui2.instanceResult = result;
    ui2PruneInstanceSelection(result.instances || []);
    ui2RenderGlobalInstances(result);
    if (sync) toast(`${account.custom_name || "所选租户"}实例已同步`, "good");
  } catch (error) {
    ui2ResetInstanceOverview();
    if ($("global-instance-summary")) $("global-instance-summary").innerHTML = `<strong>读取失败</strong><span>${esc(error.message)}</span>`;
    $("global-instance-list").innerHTML = `<div class="form-error instance-load-error">${esc(error.message)}</div>`;
  }
}

function ui2RenderGlobalInstances(result) {
  const allInstances = result.instances || [];
  const scopedInstances = ui2ScopedInstances(allInstances);
  const visibleInstances = ui2FilteredInstances(scopedInstances);
  const instanceWindow = ui2PageWindow(visibleInstances, state.ui2.instancePageSize, state.ui2.instancePage);
  state.ui2.instancePage = instanceWindow.page;
  state.ui2.instancePageItems = instanceWindow.items;
  const pageInstances = instanceWindow.items;
  ui2UpdatePagination("instance", instanceWindow.page, instanceWindow.pages, visibleInstances.length, state.ui2.instancePageSize);
  ui2UpdateInstanceOverview(scopedInstances, visibleInstances);
  const dataTime = ui2FormatCompactDate(result.last_synced_at);
  const selectedAccount = ui2SelectedAccount("global-instance-account");
  const scopeLabel = selectedAccount
    ? `${selectedAccount.custom_name || selectedAccount.tenancy_name || "所选租户"}`
    : `${new Set(allInstances.map((item) => item.account_id).filter(Boolean)).size || result.accounts_scanned || 0} 个租户`;
  const filterState = ui2InstanceFilterState();
  const visibleCopy = (filterState.status || filterState.query) ? ` · 显示 ${visibleInstances.length} 台` : "";
  $("global-instance-summary").innerHTML = `<strong>${scopedInstances.length} 台缓存实例</strong><span>${esc(scopeLabel)} · 更新于 ${esc(dataTime)}${visibleCopy}</span>`;
  if (!scopedInstances.length) {
    const emptyTitle = selectedAccount ? "该租户暂无实例缓存" : "全部租户暂无实例缓存";
    const emptyCopy = selectedAccount
      ? "当前仅显示本地 SQLite 缓存。点击同步后才会请求这个 OCI 租户。"
      : "先选择一个 OCI 租户，再按需同步；不会自动请求其他租户。";
    const emptyAction = selectedAccount
      ? '<button class="button primary small" data-instance-empty-sync type="button">同步所选租户</button>'
      : '<button class="button small" data-instance-empty-focus type="button">选择 OCI 租户</button>';
    const tenantChoices = state.accounts.slice(0, 8).map((item) => `<button class="empty-tenant-chip" data-ui2-account-instances="${item.id}" type="button"><strong>${esc(item.custom_name || item.tenancy_name || `租户 #${item.id}`)}</strong><small>${esc(item.home_region_key || item.region || "未读取区域")}</small></button>`).join("");
    $("global-instance-list").innerHTML = `<div class="instance-empty-state instance-empty-rich">
      <div class="instance-empty-mark" aria-hidden="true">OCI</div>
      <strong>${esc(emptyTitle)}</strong>
      <span>${esc(emptyCopy)}</span>
      <div class="instance-empty-actions">${emptyAction}</div>
      ${tenantChoices ? `<div class="instance-empty-tenants"><div class="empty-section-label">快速选择租户</div><div class="empty-tenant-grid">${tenantChoices}</div></div>` : ""}
      <small>同步结果仅写入本地缓存；后续浏览不会自动访问 OCI。</small>
    </div>`;
    ui2UpdateInstanceBatchToolbar([]);
    return;
  }
  if (!visibleInstances.length) {
    $("global-instance-list").innerHTML = '<div class="instance-empty-state compact"><div class="instance-empty-mark" aria-hidden="true">⌕</div><strong>没有符合条件的实例</strong><span>请调整租户、状态或搜索条件。</span><div class="instance-empty-actions"><button class="button small" data-instance-empty-reset type="button">重置筛选</button></div></div>';
    ui2UpdateInstanceBatchToolbar([]);
    return;
  }
  $("global-instance-list").innerHTML = `<table class="instance-table dense-table all-tenant-instance-table"><thead><tr><th class="instance-select-head"><input id="instance-select-all" type="checkbox" aria-label="选择当前页实例"></th><th>序号</th><th${ui2HiddenColumn("instance", "tenant")}>租户</th><th>实例</th><th${ui2HiddenColumn("instance", "status")}>状态</th><th${ui2HiddenColumn("instance", "shape")}>配置</th><th${ui2HiddenColumn("instance", "network")}>网络</th><th${ui2HiddenColumn("instance", "region")}>区域</th><th${ui2HiddenColumn("instance", "created")}>创建时间</th><th class="instance-actions-head">操作</th></tr></thead><tbody>${pageInstances.map((instance, index) => {
    const account = ui2InstanceAccount(instance);
    const accountId = Number(instance.account_id || account?.id || 0);
    const vnic = primaryVnic(instance);
    const stateName = String(instance.lifecycle_state || "UNKNOWN").toUpperCase();
    const running = stateName === "RUNNING";
    const stopped = stateName === "STOPPED";
    const [stateText, stateClass] = ui2DashboardInstanceState(instance);
    const regionCode = instance.region || account?.region || "";
    const regionName = ui2FriendlyRegionName(regionCode, account);
    const tenantName = account?.custom_name || instance.account_name || account?.tenancy_name || "未知租户";
    const tenantEmail = account?.email || instance.account_email || "未读取邮箱";
    const selectionKey = ui2InstanceSelectionKey(accountId, instance.id);
    const selected = state.ui2.instanceSelection.has(selectionKey);
    return `<tr class="${selected ? "is-selected" : ""}">
      <td class="instance-select-cell"><input type="checkbox" data-instance-select="${esc(selectionKey)}" aria-label="选择 ${esc(instance.display_name || "实例")}" ${selected ? "checked" : ""}></td>
      <td class="sequence-cell"><span>${instanceWindow.start + index + 1}</span></td>
      <td class="instance-tenant-cell"${ui2HiddenColumn("instance", "tenant")}><strong>${esc(tenantName)}</strong><small title="${esc(tenantEmail)}">${esc(tenantEmail)}</small></td>
      <td class="instance-name-cell"><button class="instance-detail-link" data-instance-detail data-account-id="${accountId}" data-instance-id="${esc(instance.id)}" type="button">${esc(instance.display_name || "N&T")}</button><small class="mono copyable-line" title="${esc(instance.id)}">${esc(ui2CompactId(instance.id))}<button class="copy-inline-button" data-copy-text="${esc(instance.id)}" data-copy-label="实例 OCID" type="button" aria-label="复制实例 OCID">⧉</button></small></td>
      <td${ui2HiddenColumn("instance", "status")}><span class="badge ${stateClass}">${esc(stateText)}</span></td>
      <td${ui2HiddenColumn("instance", "shape")}><strong>${esc(instance.shape || "—")}</strong><small>${instance.ocpus ?? "?"} OCPU · ${instance.memory_in_gbs ?? "?"} GB</small></td>
      <td class="instance-network-cell"${ui2HiddenColumn("instance", "network")}>
        <span class="instance-ip-line"><b class="instance-ip-label public">公网</b><strong class="instance-public-ip">${esc(vnic?.public_ip || "无公网 IP")}</strong>${vnic?.public_ip ? `<button class="copy-inline-button" data-copy-text="${esc(vnic.public_ip)}" data-copy-label="公网 IP" type="button" aria-label="复制公网 IP">⧉</button>` : ""}</span>
        <span class="instance-ip-line"><b class="instance-ip-label private">私网</b><small>${esc(vnic?.private_ip || "无私网 IP")}</small>${vnic?.private_ip ? `<button class="copy-inline-button" data-copy-text="${esc(vnic.private_ip)}" data-copy-label="私网 IP" type="button" aria-label="复制私网 IP">⧉</button>` : ""}</span>
      </td>
      <td${ui2HiddenColumn("instance", "region")}><strong>${esc(regionName)}</strong><small>${esc(regionCode || "未读取")}</small></td>
      <td class="instance-created-cell"${ui2HiddenColumn("instance", "created")}><time datetime="${esc(instance.time_created || "")}">${esc(fmtDate(instance.time_created))}</time></td>
      <td class="instance-actions-cell"><div class="row-actions compact instance-row-actions"><button class="button small primary-soft" data-instance-detail data-account-id="${accountId}" data-instance-id="${esc(instance.id)}" type="button">详情</button>${stopped ? `<button class="button small primary" data-ui2-account-id="${accountId}" data-ui2-instance-action="START" data-ui2-instance="${esc(instance.id)}" data-ui2-region="${esc(regionCode)}" type="button">启动</button>` : ""}${running ? `<button class="button small" data-ui2-account-id="${accountId}" data-ui2-instance-action="SOFTSTOP" data-ui2-instance="${esc(instance.id)}" data-ui2-region="${esc(regionCode)}" type="button">停止</button><button class="button small" data-ui2-account-id="${accountId}" data-ui2-instance-action="SOFTRESET" data-ui2-instance="${esc(instance.id)}" data-ui2-region="${esc(regionCode)}" type="button">重启</button>` : ""}${vnic?.private_ip_id ? `<button class="button small danger" data-ui2-account-id="${accountId}" data-ui2-replace-ip="${esc(vnic.private_ip_id)}" data-ui2-region="${esc(regionCode)}" type="button">换 IP</button>` : ""}<button class="button small" data-ui2-account-id="${accountId}" data-ui2-edit-instance="${esc(instance.id)}" data-ui2-region="${esc(regionCode)}" type="button">修改</button><button class="button small instance-manage-button" data-ui2-instance-more="${accountId}" type="button">高级管理</button></div></td>
    </tr>`;
  }).join("")}</tbody></table>`;
  ui2UpdateInstanceBatchToolbar(pageInstances);
}

async function ui2InstanceAction(button) {
  const account = ui2AccountById(button.dataset.ui2AccountId);
  if (!account) return;
  setBusy(button, true);
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(button.dataset.ui2Instance)}/actions`, { method: "POST", body: { action: button.dataset.ui2InstanceAction, region: button.dataset.ui2Region } });
    toast("实例操作已提交", "good");
    await ui2LoadGlobalInstances(false);
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function ui2ReplaceIp(button) {
  const account = ui2AccountById(button.dataset.ui2AccountId);
  if (!account || !await openConfirmDialog({ title: "更换公网 IP", message: `将释放 ${account.custom_name || "该租户"}实例的现有临时公网 IP 并申请新 IP，旧 IP 无法恢复。`, submitText: "确认更换", danger: true })) return;
  setBusy(button, true, "更换中……");
  try {
    const result = await api(`/accounts/${account.id}/public-ips/${encodeURIComponent(button.dataset.ui2ReplaceIp)}/replace`, { method: "POST", body: { region: button.dataset.ui2Region } });
    toast(`新公网 IP：${result.new_ip || "正在分配"}`, "good");
    await ui2LoadGlobalInstances(false);
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function ui2EditInstance(button) {
  const account = ui2AccountById(button.dataset.ui2AccountId);
  const instance = (state.ui2.instanceResult?.instances || []).find((item) => String(item.id) === String(button.dataset.ui2EditInstance) && Number(item.account_id) === Number(account?.id));
  if (!account || !instance) return;
  const values = await openUtilityDialog({
    title: "修改实例",
    copy: `${account.custom_name} · ${instance.display_name || "N&T"}`,
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
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/details`, {
      method: "PUT",
      body: {
        region: button.dataset.ui2Region,
        display_name: values.display_name.trim() || "N&T",
        note: String(values.note || "").trim(),
        ocpus: values.ocpus,
        memory_in_gbs: values.memory_in_gbs,
      },
    });
    toast("实例配置已提交", "good");
    await ui2LoadGlobalInstances(false);
  } catch (error) { toast(error.message, "bad"); }
}

function ui2LaunchJobFilter(job, mode) {
  const status = String(job.status || "").toUpperCase();
  if (!mode) return true;
  if (mode === "active") return Boolean(job.is_active) || ["RUNNING", "PENDING", "WAITING", "CANCELLING"].includes(status);
  if (mode === "completed") return status === "COMPLETED";
  if (mode === "failed") return status === "FAILED";
  if (mode === "cancelled") return ["CANCELLED", "CANCELED"].includes(status);
  return true;
}

function ui2LaunchProfileSearchText(profile) {
  const payload = profile.payload || {};
  return [profile.id, profile.name, profile._accountName, profile._accountEmail, payload.display_name, payload.architecture, payload.shape, payload.region, payload.image_name].join(" ").toLowerCase();
}

function ui2LaunchJobSearchText(job) {
  const request = job.request || {};
  return [job.id, job.mode, job.status, job._accountName, job._accountEmail, request.display_name, request.architecture, request.shape, request.region, request.image_name].join(" ").toLowerCase();
}

function ui2CurrentLaunchView() {
  const query = String($("global-launch-search")?.value || "").trim().toLowerCase();
  const status = $("global-launch-status")?.value || "";
  const profiles = state.ui2.launchProfiles.filter((profile) => !query || ui2LaunchProfileSearchText(profile).includes(query));
  const jobs = state.ui2.launchJobs.filter((job) => ui2LaunchJobFilter(job, status) && (!query || ui2LaunchJobSearchText(job).includes(query)));
  return { profiles, jobs, query, status };
}

function ui2ResetGlobalLaunchState(message = "请选择账户。") {
  state.ui2.launchProfiles = [];
  state.ui2.launchJobs = [];
  $("boot-profile-count").textContent = "0";
  $("boot-running-count").textContent = "0";
  $("boot-success-count").textContent = "0";
  $("boot-failure-count").textContent = "0";
  if ($("launch-visible-summary")) $("launch-visible-summary").textContent = "0 个配置 · 0 条任务";
  if ($("launch-profile-visible-count")) $("launch-profile-visible-count").textContent = "0 个配置";
  if ($("launch-job-visible-count")) $("launch-job-visible-count").textContent = "0 条任务";
  if ($("global-launch-summary")) $("global-launch-summary").innerHTML = `<strong>尚未读取</strong><span>${esc(message)}</span>`;
}

async function ui2LoadGlobalLaunch() {
  const requestId = ++state.ui2.launchRequestId;
  const selectedAccount = ui2SelectedAccount("global-launch-account");
  const accounts = selectedAccount ? [selectedAccount] : state.accounts;
  state.ui2.selectedLaunchAccount = selectedAccount?.id || null;
  ui2UpdateLaunchScopeState();

  if (!accounts.length) {
    ui2ResetGlobalLaunchState("暂无 OCI 账户。");
    $("global-launch-profiles").innerHTML = '<div class="empty launch-empty">暂无 OCI 账户</div>';
    $("global-launch-jobs").innerHTML = '<div class="empty launch-empty">暂无 OCI 账户</div>';
    return;
  }

  const scopeText = selectedAccount ? (selectedAccount.custom_name || "当前租户") : "全部租户";
  if ($("global-launch-summary")) $("global-launch-summary").innerHTML = `<strong>正在读取</strong><span>${esc(scopeText)}的本地开机数据</span>`;

  const results = await Promise.allSettled(accounts.map(async (account) => {
    const [profiles, jobs] = await Promise.all([
      api(`/accounts/${account.id}/launch/profiles`),
      api(`/accounts/${account.id}/launch/jobs?limit=100`),
    ]);
    const accountMeta = {
      _accountId: Number(account.id),
      _accountName: account.custom_name || "未命名租户",
      _accountEmail: account.email || account.tenancy_name || "未读取邮箱",
    };
    return {
      profiles: (profiles || []).map((item) => ({ ...item, ...accountMeta })),
      jobs: (jobs || []).map((item) => ({ ...item, ...accountMeta })),
    };
  }));

  if (requestId !== state.ui2.launchRequestId) return;

  state.ui2.launchProfiles = [];
  state.ui2.launchJobs = [];
  state.ui2.launchLoadErrors = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      state.ui2.launchProfiles.push(...result.value.profiles);
      state.ui2.launchJobs.push(...result.value.jobs);
    } else {
      const account = accounts[index];
      state.ui2.launchLoadErrors.push(`${account.custom_name || "租户"}：${result.reason?.message || "读取失败"}`);
    }
  });

  if (state.ui2.launchLoadErrors.length === accounts.length) {
    const message = state.ui2.launchLoadErrors.join("；");
    ui2ResetGlobalLaunchState(message);
    $("global-launch-profiles").innerHTML = `<div class="form-error">${esc(message)}</div>`;
    $("global-launch-jobs").innerHTML = `<div class="form-error">${esc(message)}</div>`;
    return;
  }

  ui2RenderGlobalLaunch(selectedAccount);
}

function ui2RenderGlobalLaunch(account = null) {
  const allProfiles = state.ui2.launchProfiles;
  const allJobs = state.ui2.launchJobs;
  const { profiles, jobs, query, status } = ui2CurrentLaunchView();
  const activeJobs = allJobs.filter((job) => ui2LaunchJobFilter(job, "active")).length;
  const successTotal = allJobs.reduce((sum, job) => sum + Number(job.success_count || 0), 0);
  const executionTotal = allJobs.reduce(
    (sum, job) => sum + Number(job.success_count || 0) + Number(job.failure_count || 0),
    0
  );
  const scopeText = account ? (account.custom_name || "当前租户") : "全部租户";

  $("boot-profile-count").textContent = String(allProfiles.length);
  $("boot-running-count").textContent = String(activeJobs);
  $("boot-success-count").textContent = String(successTotal);
  $("boot-failure-count").textContent = String(executionTotal);
  if ($("launch-visible-summary")) $("launch-visible-summary").textContent = `${profiles.length} 个配置 · ${jobs.length} 条任务`;
  if ($("launch-profile-visible-count")) $("launch-profile-visible-count").textContent = `${profiles.length} 个配置`;
  if ($("launch-job-visible-count")) $("launch-job-visible-count").textContent = `${jobs.length} 条任务`;
  if ($("global-launch-summary")) {
    const filterCopy = query || status ? ` · 当前显示 ${profiles.length} / ${jobs.length}` : "";
    const errorCopy = state.ui2.launchLoadErrors.length ? ` · ${state.ui2.launchLoadErrors.length} 个租户读取失败` : "";
    $("global-launch-summary").innerHTML = `<strong>${allProfiles.length} 配置 · ${allJobs.length} 任务</strong><span>${esc(scopeText)}${esc(filterCopy)}${esc(errorCopy)}</span>`;
  }

  $("global-launch-profiles").innerHTML = profiles.length ? `<div class="launch-table-wrap"><table class="launch-table launch-table-all-tenants"><thead><tr><th>配置名称</th><th>租户</th><th>架构 / Shape</th><th>数量 / 并发</th><th>重试策略</th><th>状态</th><th>操作</th></tr></thead><tbody>${profiles.map((profile) => {
    const payload = profile.payload || {};
    return `<tr class="launch-profile-row"><td><strong>${esc(profile.name || "未命名配置")}</strong><small>实例名 ${esc(payload.display_name || "N&T")}</small></td><td><strong>${esc(profile._accountName || "未命名租户")}</strong><small>${esc(profile._accountEmail || "未读取邮箱")}</small></td><td><span class="launch-architecture">${esc(payload.architecture || "—")}</span><small>${esc(payload.shape || "未选择 Shape")}</small></td><td><strong>${Number(payload.requested_count || 1)} 台</strong><small>并发 ${Number(payload.concurrency || 1)}</small></td><td><strong>直到成功</strong><small>每 ${Number(payload.retry_interval_seconds || 30)} 秒</small></td><td><span class="badge ${profile.enabled ? "good" : "muted"}">${profile.enabled ? "已启用" : "已停用"}</span></td><td><div class="launch-profile-actions"><button class="button small primary" data-ui2-account-id="${profile._accountId}" data-ui2-profile-run="${profile.id}" type="button" ${profile.enabled ? "" : "disabled"}>运行</button><button class="button small" data-ui2-account-id="${profile._accountId}" data-ui2-profile-once="${profile.id}" type="button">单次</button><button class="button small" data-ui2-account-id="${profile._accountId}" data-ui2-profile-preflight="${profile.id}" type="button">预检</button><button class="button small" data-ui2-account-id="${profile._accountId}" data-ui2-profile-edit="${profile.id}" type="button">编辑</button><div class="more-menu"><button class="more-toggle" data-ui2-menu="launch-profile-${profile._accountId}-${profile.id}" type="button" aria-expanded="false" aria-label="更多配置操作">⋮</button><div class="more-popover" data-ui2-menu-box="launch-profile-${profile._accountId}-${profile.id}" hidden><button data-ui2-account-id="${profile._accountId}" data-ui2-profile-clone="${profile.id}" type="button">复制当前租户副本</button><button data-ui2-account-id="${profile._accountId}" data-ui2-profile-copy-to="${profile.id}" type="button">复制到其他租户</button><button data-ui2-account-id="${profile._accountId}" data-ui2-profile-toggle="${profile.id}" data-ui2-enabled="${profile.enabled ? "1" : "0"}" type="button">${profile.enabled ? "停用配置" : "启用配置"}</button><button class="danger-text" data-ui2-account-id="${profile._accountId}" data-ui2-profile-delete="${profile.id}" type="button">删除配置</button></div></div></div></td></tr>`;
  }).join("")}</tbody></table></div>` : (() => {
    if (allProfiles.length) return '<div class="empty launch-empty">没有符合当前筛选条件的配置</div>';
    const quickTenants = state.accounts.slice(0, 10).map((item) => `<button class="launch-quick-tenant" data-ui2-open-launch-config="${item.id}" type="button"><span class="launch-quick-avatar">${esc((item.custom_name || item.tenancy_name || "N")[0].toUpperCase())}</span><span><strong>${esc(item.custom_name || item.tenancy_name || `租户 #${item.id}`)}</strong><small>${esc(item.home_region_key || item.region || "未读取区域")} · ${esc(item.email || "未读取邮箱")}</small></span><b>配置 →</b></button>`).join("");
    return `<div class="launch-empty-start">
      <div class="launch-empty-icon">OCI</div>
      <div class="launch-empty-copy"><strong>还没有开机配置</strong><span>直接选择一个 OCI 租户进入开机工作区，系统会读取该租户可用区域、网络和 Ubuntu 镜像，再保存为可重复运行的配置。</span></div>
      <div class="launch-empty-steps"><span><b>1</b>选择租户</span><span><b>2</b>自动读取环境</span><span><b>3</b>保存并运行</span></div>
      <div class="launch-quick-grid">${quickTenants}</div>
    </div>`;
  })();

  $("global-launch-jobs").innerHTML = jobs.length ? `<div class="launch-job-stack">${jobs.map((job) => {
    const request = job.request || {};
    const requested = Math.max(1, Number(job.requested_count || request.requested_count || 1));
    const success = Number(job.success_count || 0);
    const failed = Number(job.failure_count || 0);
    const executed = success + failed;
    const progress = Math.max(0, Math.min(100, Math.round((success / requested) * 100)));
    const currentAttempt = Number(job.current_attempt || 0);
    const maxAttempts = Number(job.max_attempts ?? request.max_attempts ?? 1);
    const modeText = job.mode === "CAPACITY_RETRY" ? "抢机任务" : "创建任务";
    const timeText = job.updated_at || job.created_at ? fmtDate(job.updated_at || job.created_at) : "本地任务记录";
    const capacity = /out of host capacity|outofhostcapacity|容量不足/i.test(String(job.last_error || ""));
    const statusBadge = capacity ? `<span class="badge ${job.is_active ? "warn" : "bad"}">${job.is_active ? "等待容量" : "容量不足"}</span>` : ui2TaskStatus(job.status);
    const roundsText = maxAttempts === 0 ? `已运行 ${currentAttempt} 轮 · 直到成功` : `第 ${currentAttempt} / ${maxAttempts} 轮`;
    return `<article class="launch-job-card ${job.is_active ? "is-active" : ""}"><div class="launch-job-main"><div class="launch-job-title"><strong>${modeText}</strong>${statusBadge}<span class="launch-job-tenant">${esc(job._accountName || "未命名租户")}</span></div><div class="launch-job-name">${esc(request.display_name || "N&T")}</div><small>${esc(job._accountEmail || "未读取邮箱")} · ${esc(request.architecture || "—")} · ${esc(request.shape || "未选择 Shape")} · ${esc(timeText)}</small></div><div class="launch-job-progress"><div class="launch-job-progress-head"><span>成功 ${success} / ${requested}</span><strong>${progress}%</strong></div><div class="launch-progress-track"><i style="width:${progress}%"></i></div><small>执行 ${executed} 次 · 成功 ${success} 次 · 失败 ${failed} 次${maxAttempts === 0 ? " · 直到成功" : ""}</small></div><div class="launch-job-actions"><button class="button small" data-ui2-account-id="${job._accountId}" data-ui2-job-detail="${job.id}" type="button">查看详情</button>${job.is_active ? `<button class="button small danger" data-ui2-account-id="${job._accountId}" data-ui2-job-cancel="${job.id}" type="button">停止任务</button>` : `${success < requested ? `<button class="button small primary" data-ui2-account-id="${job._accountId}" data-ui2-job-retry-failed="${job.id}" type="button">只重试失败</button>` : ""}<button class="button small" data-ui2-account-id="${job._accountId}" data-ui2-job-clone="${job.id}" type="button">重新运行</button><button class="button small" data-ui2-account-id="${job._accountId}" data-ui2-job-reset="${job.id}" type="button">重置统计</button><button class="button small danger" data-ui2-account-id="${job._accountId}" data-ui2-job-delete="${job.id}" type="button">删除任务</button>`}</div></article>`;
  }).join("")}</div>` : `<div class="empty launch-empty">${allJobs.length ? "没有符合当前筛选条件的任务记录" : "全部租户暂无任务记录"}</div>`;
}

async function ui2ProfileAction(button, action) {
  const account = ui2LaunchAccountForElement(button);
  if (!account) return;
  const id = button.dataset[`ui2Profile${action}`];
  try {
    if (action === "Run") await api(`/accounts/${account.id}/launch/profiles/${id}/run`, { method: "POST" });
    else if (action === "Once") await api(`/accounts/${account.id}/launch/profiles/${id}/run-once`, { method: "POST" });
    else if (action === "Clone") await api(`/accounts/${account.id}/launch/profiles/${id}/clone`, { method: "POST" });
    else if (action === "Toggle") await api(`/accounts/${account.id}/launch/profiles/${id}`, { method: "PUT", body: { enabled: button.dataset.ui2Enabled !== "1" } });
    else if (action === "Delete") {
      if (!await openConfirmDialog({ title: `删除开机配置 #${id}`, message: "删除后无法恢复，但不会影响已经创建的实例和历史任务。", submitText: "删除配置", danger: true })) return;
      await api(`/accounts/${account.id}/launch/profiles/${id}`, { method: "DELETE" });
    }
    toast(action === "Run" || action === "Once" ? "开机任务已启动" : "配置已更新", "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

function ui2PreflightMessage(result) {
  const symbols = { ok: "✓", updated: "↻", error: "✕" };
  const lines = (result.checks || []).map((item) => `${symbols[item.status] || "•"} ${item.label}：${item.value || "—"}\n  ${item.message || ""}`);
  const changed = (result.changed_fields || []).length
    ? `\n已更新字段：${result.changed_fields.join("、")}`
    : "\n配置字段无需更新";
  return `${result.ok ? "预检通过" : "预检未通过"}\n\n${lines.join("\n")}${changed}${result.error ? `\n\n失败原因：${result.error}` : ""}`;
}

async function ui2PreflightProfile(button) {
  const account = ui2LaunchAccountForElement(button);
  if (!account) return;
  const id = Number(button.dataset.ui2ProfilePreflight);
  setBusy(button, true, "预检中…");
  try {
    const result = await api(`/accounts/${account.id}/launch/profiles/${id}/preflight`, {
      method: "POST",
      body: { apply: true },
    });
    await openMessageDialog({
      title: `配置 #${id} 资源预检`,
      message: ui2PreflightMessage(result),
      preformatted: true,
    });
    if (result.ok) {
      toast("资源预检通过，当前租户资源已写回配置", "good");
      await ui2LoadGlobalLaunch();
    }
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function ui2CopyProfileToAccounts(button) {
  const account = ui2LaunchAccountForElement(button);
  const profileId = Number(button.dataset.ui2ProfileCopyTo);
  const profile = state.ui2.launchProfiles.find((item) => Number(item.id) === profileId && Number(item._accountId) === Number(account?.id));
  if (!account || !profile) return;
  const targets = state.accounts.filter((item) => Number(item.id) !== Number(account.id));
  if (!targets.length) { toast("没有其他租户可供复制", "bad"); return; }

  const values = await openUtilityDialog({
    title: "复制开机配置到其他租户",
    copy: `${profile.name} · 来源：${account.custom_name || account.tenancy_name || account.id}`,
    fields: [
      {
        name: "target_account_ids",
        label: "目标租户（可多选）",
        type: "multiselect",
        full: true,
        required: true,
        size: Math.min(8, Math.max(3, targets.length)),
        options: targets.map((item) => ({
          value: item.id,
          label: `${item.custom_name || item.tenancy_name || `租户 #${item.id}`} · ${item.email || "未读取邮箱"}`,
        })),
        help: "按住 Ctrl（macOS 使用 Command）可选择多个租户。租户专属 OCID 会在目标租户运行前自动预检并重新匹配。",
      },
      { name: "enabled", label: "复制后启用配置", type: "checkbox", value: true, full: true },
    ],
    submitText: "复制配置",
    validate: (data) => data.target_account_ids?.length ? "" : "请至少选择一个目标租户",
  });
  if (!values) return;

  try {
    const result = await api(`/accounts/${account.id}/launch/profiles/${profileId}/copy`, {
      method: "POST",
      body: {
        target_account_ids: values.target_account_ids.map(Number),
        enabled: Boolean(values.enabled),
      },
    });
    const failureLines = (result.errors || []).map((item) => `租户 #${item.account_id}：${item.error}`);
    toast(`已复制到 ${result.created_count} 个租户${result.failed_count ? `，失败 ${result.failed_count} 个` : ""}`, result.failed_count ? "warn" : "good");
    if (failureLines.length) {
      await openMessageDialog({ title: "部分租户复制失败", message: failureLines.join("\n"), preformatted: true });
    }
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

async function ui2EditProfile(button) {
  const account = ui2LaunchAccountForElement(button);
  const profile = state.ui2.launchProfiles.find((item) => String(item.id) === String(button.dataset.ui2ProfileEdit) && Number(item._accountId || account?.id) === Number(account?.id));
  if (!account || !profile) return;
  const payload = { ...(profile.payload || {}) };
  const values = await openUtilityDialog({
    title: "编辑开机配置",
    copy: `${account.custom_name} · ${payload.architecture || "ARM/AMD"}`,
    fields: [
      { name: "name", label: "配置名称", value: profile.name, required: true, full: true },
      { name: "requested_count", label: "创建数量", type: "number", value: payload.requested_count || 1, min: 1, max: 20, step: 1 },
      { name: "concurrency", label: "并发数", type: "number", value: payload.concurrency || 1, min: 1, max: 5, step: 1 },
      { name: "retry_interval_seconds", label: "运行间隔（秒）", type: "number", value: payload.retry_interval_seconds || 30, min: 15, max: 86400, step: 1 },
    ],
    submitText: "保存配置",
  });
  if (!values) return;
  payload.requested_count = values.requested_count;
  payload.mode = "CAPACITY_RETRY";
  payload.max_attempts = 0;
  payload.retry_interval_seconds = values.retry_interval_seconds;
  payload.concurrency = values.concurrency;
  try {
    await api(`/accounts/${account.id}/launch/profiles/${profile.id}`, { method: "PUT", body: { name: values.name.trim(), payload } });
    toast("开机配置已更新", "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

async function ui2JobAction(button, action) {
  const account = ui2LaunchAccountForElement(button);
  if (!account) return;
  const id = button.dataset[`ui2Job${action}`];
  try {
    if (action === "Detail") {
      const job = await api(`/accounts/${account.id}/launch/jobs/${id}`);
      const lines = (job.attempts || []).map((attempt) => `第 ${attempt.round_no} 轮 / #${attempt.sequence_no} / ${attempt.display_name} / ${attempt.status}${attempt.instance_id ? ` / ${attempt.instance_id}` : ""}${attempt.error ? ` / ${attempt.error}` : ""}`);
      await openMessageDialog({ title: "开机尝试详情", message: lines.length ? lines.join("\n") : "尚无尝试记录", preformatted: true });
      return;
    }
    if (action === "Cancel") {
      if (!await openConfirmDialog({ title: "停止开机任务", message: "当前开机流程会在本次请求结束后停止。", submitText: "确认停止", danger: true })) return;
      await api(`/accounts/${account.id}/launch/jobs/${id}/cancel`, { method: "POST" });
    } else if (action === "RetryFailed") {
      const job = state.ui2.launchJobs.find((item) => Number(item.id) === Number(id) && Number(item._accountId) === Number(account.id));
      const remaining = Math.max(0, Number(job?.requested_count || 0) - Number(job?.success_count || 0));
      if (!remaining) return;
      if (!await openConfirmDialog({ title: "继续未成功的配置", message: `将启动任务，仅执行尚未成功的 ${remaining} 个配置；已经成功的项目不会重跑。`, submitText: "继续运行" })) return;
      await api(`/accounts/${account.id}/launch/jobs/${id}/retry-failed`, { method: "POST" });
    } else if (action === "Clone") {
      if (!await openConfirmDialog({ title: "重新运行开机任务", message: "将按相同配置启动新的开机任务。", submitText: "重新运行" })) return;
      await api(`/accounts/${account.id}/launch/jobs/${id}/clone`, { method: "POST" });
    } else if (action === "Reset") {
      if (!await openConfirmDialog({ title: "重置任务统计", message: "将清除尝试与失败统计，但保留任务配置。", submitText: "确认重置", danger: true })) return;
      await api(`/accounts/${account.id}/launch/jobs/${id}/reset`, { method: "POST" });
    } else if (action === "Delete") {
      if (!await openConfirmDialog({ title: "删除开机任务", message: "将永久删除任务及尝试记录，不会影响已经创建的 OCI 实例。", submitText: "删除任务", danger: true })) return;
      await api(`/accounts/${account.id}/launch/jobs/${id}`, { method: "DELETE" });
    }
    toast("任务操作已提交", "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

async function ui2BulkProfiles(enabled) {
  const account = ui2SelectedAccount("global-launch-account");
  if (!account) { toast("请先选择一个租户再执行批量操作", "bad"); return; }
  try {
    await api(`/accounts/${account.id}/launch/profiles`, { method: "PUT", body: { enabled } });
    toast(enabled ? "配置已全部启用" : "配置已全部停用", "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

async function ui2CancelActiveJobs() {
  const account = ui2SelectedAccount("global-launch-account");
  if (!account) { toast("请先选择一个租户再取消运行任务", "bad"); return; }
  if (!await openConfirmDialog({ title: "取消全部运行任务", message: "当前账户所有运行中的创建和抢机任务都会停止。", submitText: "确认取消", danger: true })) return;
  try {
    const result = await api(`/accounts/${account.id}/launch/actions/cancel-active`, { method: "POST" });
    toast(`已提交取消 ${result.count} 个任务`, "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

function ui2RenameAccount(accountId) {
  const account = ui2AccountById(accountId);
  if (!account) return;
  $("account-rename-id").value = String(account.id);
  $("account-rename-value").value = account.custom_name || "";
  $("account-rename-current").textContent = account.custom_name || "未命名 API";
  $("account-rename-email").textContent = account.email || account.tenancy_name || "未读取邮箱";
  $("account-rename-avatar").textContent = (account.custom_name || "N")[0].toUpperCase();
  $("account-rename-copy").textContent = `${account.tenancy_name || "当前租户"} · 仅修改本地显示名称`;
  $("account-rename-error").hidden = true;
  $("account-rename-dialog").showModal();
  requestAnimationFrame(() => { $("account-rename-value").focus(); $("account-rename-value").select(); });
}

async function ui2SubmitRename(event) {
  event.preventDefault();
  const accountId = Number($("account-rename-id").value);
  const customName = $("account-rename-value").value.trim();
  const errorBox = $("account-rename-error");
  const button = $("account-rename-submit");
  if (!accountId || !customName) {
    errorBox.textContent = "自定义名称不能为空";
    errorBox.hidden = false;
    return;
  }
  setBusy(button, true, "保存中……");
  try {
    await api(`/accounts/${accountId}`, { method: "PUT", body: { custom_name: customName } });
    $("account-rename-dialog").close();
    await loadAccounts();
    toast("自定义名称已修改", "good");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function ui2ResetAllJobs() {
  const account = ui2SelectedAccount("global-launch-account");
  if (!account) { toast("请先选择一个租户再重置统计", "bad"); return; }
  if (!await openConfirmDialog({ title: "重置全部已结束任务", message: "将清除当前账户已结束任务的尝试和失败统计，但保留任务配置。", submitText: "确认重置", danger: true })) return;
  try {
    const result = await api(`/accounts/${account.id}/launch/actions/reset-history`, { method: "POST" });
    toast(`已重置 ${result.count} 个任务`, "good");
    await ui2LoadGlobalLaunch();
  } catch (error) { toast(error.message, "bad"); }
}

function ui2CloseMenus(exceptId = null) {
  document.querySelectorAll("[data-ui2-menu-box]").forEach((box) => {
    if (String(box.dataset.ui2MenuBox) !== String(exceptId)) {
      box.hidden = true;
      document.querySelector(`[data-ui2-menu="${CSS.escape(String(box.dataset.ui2MenuBox))}"]`)?.setAttribute("aria-expanded", "false");
    }
  });
  const accountBox = document.getElementById("ui2-account-menu-popover");
  if (accountBox && String(accountBox.dataset.accountId) !== String(exceptId)) accountBox.hidden = true;
  document.querySelectorAll("[data-ui2-account-menu]").forEach((button) => {
    if (String(button.dataset.ui2AccountMenu) !== String(exceptId)) button.setAttribute("aria-expanded", "false");
  });
}

function ui2PositionAccountMenu(button, box) {
  if (!button || !box) return;
  box.hidden = false;
  box.style.position = "fixed";
  box.style.inset = "auto";
  box.style.visibility = "hidden";
  const buttonRect = button.getBoundingClientRect();
  const menuRect = box.getBoundingClientRect();
  const gap = 7;
  const edge = 10;
  let left = buttonRect.right - menuRect.width;
  left = Math.max(edge, Math.min(left, window.innerWidth - menuRect.width - edge));
  let top = buttonRect.bottom + gap;
  if (top + menuRect.height > window.innerHeight - edge) {
    top = Math.max(edge, buttonRect.top - menuRect.height - gap);
  }
  box.style.left = `${Math.round(left)}px`;
  box.style.top = `${Math.round(top)}px`;
  box.style.visibility = "visible";
  button.setAttribute("aria-expanded", "true");
}

function ui2ExportAccounts() {
  const rows = [["自定义名称", "完整邮箱", "租户名称", "账户类型", "主区域", "区域Key", "状态", "存活天数", "上次检测"]];
  state.accounts.forEach((account) => rows.push([
    account.custom_name || "", account.email || "", account.tenancy_name || "",
    accountType(account), regionName(account), account.home_region_key || account.region || "",
    accountStatus(account)[0], account.survival_days ?? "", account.last_checked_at || "",
  ]));
  const csv = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `oci-nt-accounts-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  toast("账户清单已导出；不包含 OCI 私钥和登录密码", "good");
}

function ui2RenderCurrentInstanceResult() {
  if (!state.ui2.instanceResult) return;
  ui2UpdateInstanceSyncState();
  ui2RenderGlobalInstances(state.ui2.instanceResult);
}

function ui2Bind() {
  $("account-export")?.addEventListener("click", ui2ExportAccounts);
  $("account-rename-form")?.addEventListener("submit", ui2SubmitRename);
  $("account-type-filter")?.addEventListener("change", () => {
    state.ui2.accountPage = 1;
    ui2SaveControlValue("account-type-filter", "oci_nt_filter_account_type");
    ui2PopulateAccountFilters();
    renderAccounts();
  });
  $("account-region-filter")?.addEventListener("change", () => {
    state.ui2.accountPage = 1;
    state.ui2.accountRegionFilter = $("account-region-filter")?.value || "";
    ui2SaveControlValue("account-region-filter", "oci_nt_filter_account_region");
    renderAccounts();
  });
  $("account-sort-filter")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-account-sort-key]");
    if (!button) return;
    ui2ToggleAccountSort(button.dataset.accountSortKey);
  });
  $("account-filter-reset")?.addEventListener("click", () => {
    if ($("account-type-filter")) $("account-type-filter").value = "";
    if ($("account-region-filter")) $("account-region-filter").value = "";
    state.ui2.accountSort = "";
    localStorage.removeItem("oci_nt_account_sort_rc312");
    ui2SyncAccountSortControl();
    if ($("account-search")) $("account-search").value = "";
    state.ui2.accountRegionFilter = "";
    state.ui2.accountPage = 1;
    ["oci_nt_filter_account_type", "oci_nt_filter_account_region", "oci_nt_filter_account_search"].forEach((key) => localStorage.removeItem(key));
    ui2PopulateAccountFilters();
    renderAccounts();
  });
  $("account-view-list")?.addEventListener("click", () => { state.ui2.accountView = "list"; localStorage.setItem("oci_nt_account_view", "list"); renderAccounts(); });
  $("account-view-card")?.addEventListener("click", () => { state.ui2.accountView = "card"; localStorage.setItem("oci_nt_account_view", "card"); renderAccounts(); });
  $("account-page-size")?.addEventListener("change", () => {
    state.ui2.accountPageSize = $("account-page-size").value;
    state.ui2.accountPage = 1;
    localStorage.setItem("oci_nt_account_page_size", state.ui2.accountPageSize);
    renderAccounts();
  });
  $("account-columns")?.addEventListener("click", () => ui2ConfigureColumns("account"));
  $("account-pagination")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-account-page]");
    if (!button) return;
    state.ui2.accountPage += button.dataset.accountPage === "next" ? 1 : -1;
    renderAccounts();
  });
  $("global-instance-account")?.addEventListener("change", () => {
    state.ui2.selectedInstanceAccount = ui2SelectedAccount("global-instance-account")?.id || null;
    state.ui2.instancePage = 1;
    if (state.ui2.selectedInstanceAccount) localStorage.setItem("oci_nt_instance_account", String(state.ui2.selectedInstanceAccount));
    else localStorage.removeItem("oci_nt_instance_account");
    state.ui2.instanceSelection.clear();
    ui2UpdateInstanceSyncState();
    ui2RenderCurrentInstanceResult();
  });
  $("global-instance-status")?.addEventListener("change", () => {
    state.ui2.instancePage = 1;
    ui2SaveControlValue("global-instance-status", "oci_nt_filter_instance_status");
    ui2RenderCurrentInstanceResult();
  });
  $("global-instance-search")?.addEventListener("input", () => {
    state.ui2.instancePage = 1;
    ui2SaveControlValue("global-instance-search", "oci_nt_filter_instance_search");
    ui2RenderCurrentInstanceResult();
  });
  $("global-instance-reset")?.addEventListener("click", () => {
    if ($("global-instance-status")) $("global-instance-status").value = "";
    if ($("global-instance-search")) $("global-instance-search").value = "";
    state.ui2.instancePage = 1;
    ["oci_nt_filter_instance_status", "oci_nt_filter_instance_search"].forEach((key) => localStorage.removeItem(key));
    ui2RenderCurrentInstanceResult();
  });
  $("instance-page-size")?.addEventListener("change", () => {
    state.ui2.instancePageSize = $("instance-page-size").value;
    state.ui2.instancePage = 1;
    localStorage.setItem("oci_nt_instance_page_size", state.ui2.instancePageSize);
    ui2RenderCurrentInstanceResult();
  });
  $("instance-columns")?.addEventListener("click", () => ui2ConfigureColumns("instance"));
  $("instance-pagination")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-instance-page]");
    if (!button) return;
    state.ui2.instancePage += button.dataset.instancePage === "next" ? 1 : -1;
    ui2RenderCurrentInstanceResult();
  });
  $("global-instance-local")?.addEventListener("click", () => ui2LoadGlobalInstances(false));
  $("global-instance-sync")?.addEventListener("click", (event) => { const button = event.currentTarget; setBusy(button, true, "同步中……"); ui2LoadGlobalInstances(true).finally(() => setBusy(button, false)); });
  $("instance-batch-clear")?.addEventListener("click", () => ui2ClearInstanceSelection());
  document.querySelectorAll("[data-instance-batch-operation]").forEach((button) => {
    button.addEventListener("click", () => ui2StartInstanceBatch(button.dataset.instanceBatchOperation, button));
  });
  document.addEventListener("change", (event) => {
    const selectAll = event.target.closest("#instance-select-all");
    if (selectAll) {
      const visible = state.ui2.instancePageItems || ui2FilteredInstances(ui2ScopedInstances(ui2AllCachedInstances()));
      visible.forEach((instance) => {
        const key = ui2InstanceSelectionKey(instance.account_id, instance.id);
        if (selectAll.checked) state.ui2.instanceSelection.add(key);
        else state.ui2.instanceSelection.delete(key);
      });
      ui2RenderCurrentInstanceResult();
      return;
    }
    const checkbox = event.target.closest("[data-instance-select]");
    if (!checkbox) return;
    if (checkbox.checked) state.ui2.instanceSelection.add(checkbox.dataset.instanceSelect);
    else state.ui2.instanceSelection.delete(checkbox.dataset.instanceSelect);
    checkbox.closest("tr")?.classList.toggle("is-selected", checkbox.checked);
    ui2UpdateInstanceBatchToolbar();
  });
  $("global-launch-account")?.addEventListener("change", () => {
    state.ui2.selectedLaunchAccount = ui2SelectedAccount("global-launch-account")?.id || null;
    if (state.ui2.selectedLaunchAccount) localStorage.setItem("oci_nt_launch_account", String(state.ui2.selectedLaunchAccount));
    else localStorage.removeItem("oci_nt_launch_account");
    ui2UpdateLaunchScopeState();
    ui2LoadGlobalLaunch();
  });
  $("global-launch-status")?.addEventListener("change", () => {
    ui2SaveControlValue("global-launch-status", "oci_nt_filter_launch_status");
    ui2RenderGlobalLaunch(ui2SelectedAccount("global-launch-account"));
  });
  $("global-launch-search")?.addEventListener("input", () => {
    ui2SaveControlValue("global-launch-search", "oci_nt_filter_launch_search");
    ui2RenderGlobalLaunch(ui2SelectedAccount("global-launch-account"));
  });
  $("global-launch-reset")?.addEventListener("click", () => {
    if ($("global-launch-status")) $("global-launch-status").value = "";
    if ($("global-launch-search")) $("global-launch-search").value = "";
    ["oci_nt_filter_launch_status", "oci_nt_filter_launch_search"].forEach((key) => localStorage.removeItem(key));
    ui2RenderGlobalLaunch(ui2SelectedAccount("global-launch-account"));
  });
  $("global-launch-refresh")?.addEventListener("click", (event) => { const button = event.currentTarget; setBusy(button, true, "刷新中……"); ui2LoadGlobalLaunch().finally(() => setBusy(button, false)); });
  $("global-launch-create")?.addEventListener("click", () => {
    const account = ui2SelectedAccount("global-launch-account");
    if (!account) { toast("请先选择一个租户再添加配置", "bad"); return; }
    ui2OpenTenant(account.id, "launch", "launch");
  });
  $("global-profiles-enable")?.addEventListener("click", () => ui2BulkProfiles(true));
  $("global-profiles-disable")?.addEventListener("click", () => ui2BulkProfiles(false));
  $("global-jobs-cancel")?.addEventListener("click", ui2CancelActiveJobs);
  $("global-jobs-reset")?.addEventListener("click", ui2ResetAllJobs);
  window.addEventListener("resize", () => ui2CloseMenus());
  document.addEventListener("scroll", () => ui2CloseMenus(), true);

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) { ui2CloseMenus(); return; }
    if (button.dataset.copyText !== undefined) {
      ui2CopyText(button);
      event.stopPropagation();
      return;
    }
    if (button.dataset.ui2AccountMenu) {
      const accountId = String(button.dataset.ui2AccountMenu);
      const existingBox = document.getElementById("ui2-account-menu-popover");
      const willOpen = !existingBox || existingBox.hidden || String(existingBox.dataset.accountId) !== accountId;
      ui2CloseMenus();
      if (willOpen) {
        const box = ui2AccountMenuPopover(accountId);
        ui2PositionAccountMenu(button, box);
      }
      event.stopPropagation();
      return;
    }
    if (button.dataset.ui2Menu) {
      const box = document.querySelector(`[data-ui2-menu-box="${CSS.escape(button.dataset.ui2Menu)}"]`);
      const willOpen = Boolean(box?.hidden);
      ui2CloseMenus(button.dataset.ui2Menu);
      if (box) {
        if (willOpen) ui2PositionAccountMenu(button, box);
        else { box.hidden = true; button.setAttribute("aria-expanded", "false"); }
      }
      event.stopPropagation();
      return;
    }
    if (!button.closest(".more-menu")) ui2CloseMenus();
    if (button.dataset.ui2Rename) ui2RenameAccount(Number(button.dataset.ui2Rename));
    else if (button.dataset.ui2Manage) ui2OpenTenant(button.dataset.ui2Manage, button.dataset.ui2Tab || "instances", state.currentPage);
    else if (button.dataset.ui2Create) ui2OpenTenant(button.dataset.ui2Create, "launch", "accounts");
    else if (button.dataset.ui2AccountInstances) {
      state.ui2.selectedInstanceAccount = Number(button.dataset.ui2AccountInstances);
      showPage("instances");
      ui2FillInstanceSelector(state.ui2.selectedInstanceAccount);
      ui2LoadGlobalInstances(false);
    } else if (button.dataset.ui2AccountLaunch) {
      state.ui2.selectedLaunchAccount = Number(button.dataset.ui2AccountLaunch);
      showPage("launch");
      ui2FillSelector("global-launch-account", state.ui2.selectedLaunchAccount);
      ui2LoadGlobalLaunch();
    } else if (button.dataset.ui2OpenLaunchConfig !== undefined) ui2OpenTenant(button.dataset.ui2OpenLaunchConfig, "launch", "launch");
    else if (button.dataset.instanceEmptySync !== undefined) $("global-instance-sync")?.click();
    else if (button.dataset.instanceEmptyFocus !== undefined) $("global-instance-account")?.focus();
    else if (button.dataset.instanceEmptyReset !== undefined) $("global-instance-reset")?.click();
    else if (button.dataset.ui2InstanceAction) ui2InstanceAction(button);
    else if (button.dataset.ui2ReplaceIp) ui2ReplaceIp(button);
    else if (button.dataset.ui2EditInstance) ui2EditInstance(button);
    else if (button.dataset.ui2InstanceMore) ui2OpenTenant(button.dataset.ui2InstanceMore, "instances", "instances");
    else if (button.dataset.ui2ProfileRun) ui2ProfileAction(button, "Run");
    else if (button.dataset.ui2ProfileOnce) ui2ProfileAction(button, "Once");
    else if (button.dataset.ui2ProfilePreflight) ui2PreflightProfile(button);
    else if (button.dataset.ui2ProfileEdit) ui2EditProfile(button);
    else if (button.dataset.ui2ProfileClone) ui2ProfileAction(button, "Clone");
    else if (button.dataset.ui2ProfileCopyTo) ui2CopyProfileToAccounts(button);
    else if (button.dataset.ui2ProfileToggle) ui2ProfileAction(button, "Toggle");
    else if (button.dataset.ui2ProfileDelete) ui2ProfileAction(button, "Delete");
    else if (button.dataset.ui2JobDetail) ui2JobAction(button, "Detail");
    else if (button.dataset.ui2JobCancel) ui2JobAction(button, "Cancel");
    else if (button.dataset.ui2JobRetryFailed) ui2JobAction(button, "RetryFailed");
    else if (button.dataset.ui2JobClone) ui2JobAction(button, "Clone");
    else if (button.dataset.ui2JobReset) ui2JobAction(button, "Reset");
    else if (button.dataset.ui2JobDelete) ui2JobAction(button, "Delete");
  });
}

/* Page-level action tooltip avoids table overflow clipping and remains visible while hovered or focused. */
(() => {
  let hoverTarget = null;
  let focusTarget = null;
  let visibleTarget = null;

  function tooltipElement() {
    let tooltip = document.getElementById("ui2-global-action-tooltip");
    if (tooltip) return tooltip;
    tooltip = document.createElement("div");
    tooltip.id = "ui2-global-action-tooltip";
    tooltip.className = "ui2-global-action-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    document.body.appendChild(tooltip);
    return tooltip;
  }

  function eligibleTarget(node) {
    const target = node instanceof Element ? node.closest("[data-action-tooltip]") : null;
    return target && target.isConnected ? target : null;
  }

  function hideTooltip() {
    const tooltip = document.getElementById("ui2-global-action-tooltip");
    if (tooltip) {
      tooltip.hidden = true;
      tooltip.style.visibility = "hidden";
    }
    visibleTarget = null;
  }

  function positionTooltip(target, tooltip) {
    if (!target || !target.isConnected || tooltip.hidden) return;
    const targetRect = target.getBoundingClientRect();
    if (targetRect.bottom < 0 || targetRect.top > window.innerHeight || targetRect.right < 0 || targetRect.left > window.innerWidth) {
      hideTooltip();
      return;
    }
    const tooltipRect = tooltip.getBoundingClientRect();
    const gap = 5;
    const edge = 6;
    let left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
    left = Math.max(edge, Math.min(left, window.innerWidth - tooltipRect.width - edge));
    let top = targetRect.bottom + gap;
    if (top + tooltipRect.height > window.innerHeight - edge) top = targetRect.top - tooltipRect.height - gap;
    top = Math.max(edge, Math.min(top, window.innerHeight - tooltipRect.height - edge));
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.style.visibility = "visible";
  }

  function showTooltip(target) {
    const text = String(target?.dataset?.actionTooltip || "").trim();
    if (!target || !text || target.disabled || target.getAttribute("aria-expanded") === "true") {
      hideTooltip();
      return;
    }
    const tooltip = tooltipElement();
    tooltip.textContent = text;
    tooltip.hidden = false;
    tooltip.style.visibility = "hidden";
    visibleTarget = target;
    positionTooltip(target, tooltip);
  }

  function refreshTooltip(preferred = null) {
    const target = preferred || focusTarget || hoverTarget;
    if (target && target.isConnected) showTooltip(target);
    else hideTooltip();
  }

  document.addEventListener("pointerover", (event) => {
    const target = eligibleTarget(event.target);
    if (!target || target.contains(event.relatedTarget)) return;
    hoverTarget = target;
    refreshTooltip(target);
  }, true);
  document.addEventListener("pointerout", (event) => {
    const target = eligibleTarget(event.target);
    if (!target || target.contains(event.relatedTarget)) return;
    if (hoverTarget === target) hoverTarget = null;
    refreshTooltip(focusTarget);
  }, true);
  document.addEventListener("focusin", (event) => {
    const target = eligibleTarget(event.target);
    if (!target) return;
    focusTarget = target;
    refreshTooltip(target);
  }, true);
  document.addEventListener("focusout", (event) => {
    const target = eligibleTarget(event.target);
    if (focusTarget === target) focusTarget = null;
    refreshTooltip(hoverTarget);
  }, true);
  document.addEventListener("click", (event) => {
    const target = eligibleTarget(event.target);
    if (target?.matches("[data-ui2-account-menu]")) hideTooltip();
  }, true);
  window.addEventListener?.("resize", () => {
    if (visibleTarget) positionTooltip(visibleTarget, tooltipElement());
  });
  document.addEventListener("scroll", () => {
    if (visibleTarget) positionTooltip(visibleTarget, tooltipElement());
  }, true);
})();

document.addEventListener("DOMContentLoaded", () => {
  state.ui2.accountSort = ui2ActiveAccountSort();
  if ($("account-type-filter")) $("account-type-filter").value = ui2StoredValue("oci_nt_filter_account_type");
  if ($("account-search")) $("account-search").value = ui2StoredValue("oci_nt_filter_account_search");
  if ($("global-instance-status")) $("global-instance-status").value = ui2StoredValue("oci_nt_filter_instance_status");
  if ($("global-instance-search")) $("global-instance-search").value = ui2StoredValue("oci_nt_filter_instance_search");
  if ($("global-launch-status")) $("global-launch-status").value = ui2StoredValue("oci_nt_filter_launch_status");
  if ($("global-launch-search")) $("global-launch-search").value = ui2StoredValue("oci_nt_filter_launch_search");
  if ($("account-page-size")) $("account-page-size").value = state.ui2.accountPageSize;
  if ($("instance-page-size")) $("instance-page-size").value = state.ui2.instancePageSize;
  ui2SyncAccountSortControl();
  ui2Bind();
  // Original bootstrap is async; account hooks take over after login completes.
  setTimeout(() => {
    if (state.token && !$("app-view").hidden) {
      ui2FillInstanceSelector();
      ui2FillSelector("global-launch-account");
      if (state.currentPage === "dashboard") ui2LoadDashboard();
    }
  }, 300);
});

/* BEGIN OCI-N&T V1.0.4 1.0.4-api-manage-account-detail-a4 */
function ntApiManageOpenAccountDetailA4(event) {
  const button = event.target.closest("[data-ui2-manage], [data-manage-account]");
  if (!button || !button.closest("#accounts-page")) return;

  const accountId = Number(
    button.dataset.ui2Manage ||
    button.dataset.manageAccount ||
    0
  );
  if (!accountId) return;

  // API 管理页“管理”语义：进入当前 OCI 账户的账户信息/设置，
  // 而不是默认进入实例页。使用 capture + stopImmediatePropagation
  // 阻止旧的 bubble handler 再次把 tab 切回 instances。
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();

  if (typeof openTenant !== "function") {
    if (typeof toast === "function") toast("账户工作区尚未就绪", "bad");
    return;
  }

  window.ui2TenantBackPage = "accounts";
  openTenant(accountId, "account");
}

document.addEventListener("click", ntApiManageOpenAccountDetailA4, true);
/* END OCI-N&T V1.0.4 1.0.4-api-manage-account-detail-a4 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-account-settings-iam-dashboard-a5.1 */
(function ntAccountSettingsIamDashboardA5Bootstrap() {
  const ntA5GovernanceName = "renderTenancyWorkspace";
  let ntA5Installed = false;

  function ntA5PanelByTitle(root, title) {
    if (!root) return null;
    const panels = root.querySelectorAll("section, article, .panel");
    for (const panel of panels) {
      const headings = panel.querySelectorAll("h1, h2, h3, h4, .panel-head strong, .rc-card-head strong");
      for (const heading of headings) {
        if ((heading.textContent || "").trim() === title) return panel;
      }
    }
    return null;
  }

  function ntA5Text(value, fallback = "—") {
    if (value === null || value === undefined || String(value).trim() === "") return fallback;
    return String(value);
  }

  function ntA5FmtDate(value) {
    if (!value) return "—";
    try {
      if (typeof fmtDate === "function") return fmtDate(value);
    } catch (_) {}
    return ntA5Text(value);
  }

  function ntA5AccountType(account) {
    try {
      if (typeof accountType === "function") return accountType(account);
    } catch (_) {}
    const value = String(account?.account_type || "");
    if (value === "PERSONAL_FREE") return "个人免费";
    if (value === "PERSONAL_UPGRADED") return "个人升级";
    return value || "—";
  }

  function ntA5Region(account) {
    try {
      if (typeof regionName === "function") return regionName(account);
    } catch (_) {}
    return ntA5Text(account?.home_region_name || account?.home_region_key || account?.region);
  }

  function ntA5Survival(account) {
    try {
      if (typeof survival === "function") return survival(account);
    } catch (_) {}
    const days = Number(account?.survival_days);
    return Number.isFinite(days) && days >= 0 ? `${days} 天` : "—";
  }

  function ntA5Registration(account) {
    try {
      if (typeof ui2RegistrationDateTime === "function") {
        const value = ui2RegistrationDateTime(account);
        if (value && value !== "—") return value;
      }
    } catch (_) {}
    return ntA5FmtDate(
      account?.subscription_time_start ||
      account?.registered_at ||
      account?.registration_time ||
      account?.created_at ||
      account?.time_created
    );
  }

  function ntA5Detail(label, value, copyable = false) {
    try {
      if (typeof detailItem === "function") return detailItem(label, value, copyable);
    } catch (_) {}
    const display = ntA5Text(value);
    return `<div class="detail-item"><span>${typeof esc === "function" ? esc(label) : label}</span><div><strong class="wrap">${typeof esc === "function" ? esc(display) : display}</strong></div></div>`;
  }

  function ntA5AccountInfoHtml(account) {
    const tenancyOcid = account?.tenancy_ocid || account?.tenancy || account?.tenant_ocid || "—";
    const userOcid = account?.user_ocid || account?.user || account?.user_id || "—";
    const fingerprint = account?.fingerprint || "—";
    return `
      <div class="nt-a5-section-head">
        <div><h2>账户设置</h2><p>集中查看当前 OCI 账户信息并管理 OCI IAM；账户检测与代理绑定统一从 API 管理和代理管理执行。</p></div>
      </div>
      <section class="panel nt-a5-account-info">
        <div class="panel-head"><div><h2>账户信息</h2><p>以下内容来自已保存的账户资料，不会因为打开本页而请求 OCI。</p></div></div>
        <div class="nt-a5-account-info-grid">
          ${ntA5Detail("API 名称", account?.custom_name)}
          ${ntA5Detail("邮箱", account?.email)}
          ${ntA5Detail("租户名称", account?.tenancy_name)}
          ${ntA5Detail("账户类型", ntA5AccountType(account))}
          ${ntA5Detail("主区域", ntA5Region(account))}
          ${ntA5Detail("存活时间", ntA5Survival(account))}
          ${ntA5Detail("注册时间", ntA5Registration(account))}
          ${ntA5Detail("上次检测", ntA5FmtDate(account?.last_checked_at))}
          <div class="nt-a5-info-wide">${ntA5Detail("Tenancy OCID", tenancyOcid, tenancyOcid !== "—")}</div>
          <div class="nt-a5-info-wide">${ntA5Detail("User OCID", userOcid, userOcid !== "—")}</div>
          <div class="nt-a5-info-wide">${ntA5Detail("Fingerprint", fingerprint, fingerprint !== "—")}</div>
        </div>
      </section>`;
  }

  function ntA5DangerHtml(account) {
    const id = Number(account?.id || 0);
    return `<section class="panel nt-a5-danger-panel">
      <div class="panel-head"><div><h2>危险操作</h2><p>删除后会移除该账户的本地凭据、缓存及关联记录；此操作需要再次确认。</p></div></div>
      <div class="nt-a5-danger-actions"><button class="button danger" data-delete-account="${id}" type="button">删除当前 OCI 账户</button></div>
    </section>`;
  }

  function ntA5Install() {
    if (ntA5Installed) return;
    const originalAccount = window.renderTenantAccountSettings;
    const originalGovernance = window[ntA5GovernanceName];
    if (typeof originalAccount !== "function" || typeof originalGovernance !== "function") {
      console.error("OCI-N&T A5: tenant renderer unavailable", {
        account: typeof originalAccount,
        governance: ntA5GovernanceName,
        governanceType: typeof originalGovernance,
      });
      return;
    }
    ntA5Installed = true;

    function renderAccountA5() {
      const content = document.getElementById("tenant-content");
      const account =
        (typeof currentTenantAccount === "function" ? currentTenantAccount() : null)
        || (
          typeof state !== "undefined"
          && Array.isArray(state.accounts)
          ? state.accounts.find((item) => Number(item.id) === Number(state.tenantId))
          : null
        );
      if (!content || !account) return originalAccount();

      // Reuse the already implemented IAM panel without duplicating OCI logic.
      // The original governance renderer only renders the workspace; OCI calls remain behind its manual buttons.
      originalGovernance();
      const iamSource = ntA5PanelByTitle(content, "OCI IAM");
      const iamPanel = iamSource || null;
      if (iamPanel) iamPanel.remove();

      content.innerHTML = ntA5AccountInfoHtml(account);
      content.classList.remove("nt-a5-governance");
      content.classList.add("nt-a5-account-settings");

      if (iamPanel) {
        iamPanel.classList.add("nt-a5-iam-panel");
        const head = iamPanel.querySelector(".panel-head p, header p");
        if (head) head.textContent = "管理当前 OCI 账户的用户、用户组、成员关系与密码策略；仅点击读取或操作时访问 OCI。";
        content.appendChild(iamPanel);
      } else {
        const fallback = document.createElement("section");
        fallback.className = "panel nt-a5-iam-panel";
        fallback.innerHTML = '<div class="panel-head"><div><h2>OCI IAM</h2><p>当前 IAM 组件暂未就绪，请刷新页面后重试。</p></div></div>';
        content.appendChild(fallback);
      }

      content.insertAdjacentHTML("beforeend", ntA5DangerHtml(account));
    }

    function renderGovernanceA5() {
      // IAM has moved to Account Settings. Keep only region subscription, limits and OCI audit here.
      originalGovernance();
      const content = document.getElementById("tenant-content");
      if (!content) return;
      content.classList.remove("nt-a5-account-settings");
      content.classList.add("nt-a5-governance");

      const iamPanel = ntA5PanelByTitle(content, "OCI IAM");
      if (iamPanel) iamPanel.remove();

      for (const heading of content.querySelectorAll("h1, h2, h3")) {
        if ((heading.textContent || "").trim() === "区域、IAM、配额与审计") {
          heading.textContent = "区域、配额与审计";
        }
      }
      const lead = content.querySelector(":scope > p");
      if (lead && /区域|IAM|配额|审计/.test(lead.textContent || "")) {
        lead.textContent = "区域订阅、服务配额与 OCI 审计；只有点击读取时才访问当前账户 OCI。";
      }
      const auditPanel = ntA5PanelByTitle(content, "OCI 审计");
      if (auditPanel) auditPanel.classList.add("nt-a5-governance-audit");
    }

    window.renderTenantAccountSettings = renderAccountA5;
    window.ntA5RenderAccountSettings = renderAccountA5;
    window[ntA5GovernanceName] = function ntA5GovernanceDispatcher() {
      if (typeof state !== "undefined" && state.tenantTab === "account") return renderAccountA5();
      return renderGovernanceA5();
    };

    function openDashboardAccountA5(event) {
      const row = event.target.closest?.(
        ".dashboard-account-row[data-ui2-manage], .dashboard-account-row[data-detail]"
      );
      if (!row || !row.closest("#dashboard-page")) return;
      const accountId = Number(row.dataset.ui2Manage || row.dataset.detail || 0);
      if (!accountId || typeof openTenant !== "function") return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      window.ui2TenantBackPage = "dashboard";
      openTenant(accountId, "account");
    }

    function backToDashboardA5(event) {
      const button = event.target.closest?.("#tenant-back");
      if (!button || window.ui2TenantBackPage !== "dashboard") return;
      if (typeof showPage !== "function") return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      window.ui2TenantBackPage = null;
      showPage("dashboard");
    }

    document.addEventListener("click", openDashboardAccountA5, true);
    document.addEventListener("click", backToDashboardA5, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ntA5Install, { once: true });
  } else {
    ntA5Install();
  }
})();
/* END OCI-N&T V1.0.4 1.0.4-account-settings-iam-dashboard-a5.1 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-account-settings-iam-a5.3 */
(() => {
  if (window.__ntA53AccountIamInstalled) return;
  window.__ntA53AccountIamInstalled = true;

  const ntA53BaseAccountRenderer = window.renderTenantAccountSettings;
  if (typeof ntA53BaseAccountRenderer !== "function") {
    console.warn("OCI-N&T A5.3: renderTenantAccountSettings 不可用");
    return;
  }

  const ntA53IamCache = window.__ntA53IamCache instanceof Map
    ? window.__ntA53IamCache
    : new Map();
  window.__ntA53IamCache = ntA53IamCache;

  function ntA53Text(value) {
    return String(value ?? "").trim();
  }

  function ntA53CurrentAccount() {
    try {
      if (typeof currentTenantAccount === "function") {
        const account = currentTenantAccount();
        if (account) return account;
      }
    } catch (_) {}
    if (typeof state !== "undefined" && Array.isArray(state.accounts)) {
      return state.accounts.find(
        (item) => Number(item.id) === Number(state.tenantId)
      ) || null;
    }
    return null;
  }

  function ntA53PanelByTitle(root, title) {
    if (!root) return null;
    const candidates = Array.from(root.querySelectorAll(
      ":scope > section, :scope > article, :scope > div, section.panel, article.panel, .panel"
    ));
    for (const panel of candidates) {
      const headings = panel.querySelectorAll("h1,h2,h3");
      for (const heading of headings) {
        if (ntA53Text(heading.textContent) === title) return panel;
      }
    }
    return null;
  }

  function ntA53IamPanel(root) {
    return ntA53PanelByTitle(root, "OCI IAM");
  }

  function ntA53DangerPanel(root) {
    return ntA53PanelByTitle(root, "危险操作");
  }

  function ntA53AccountInfoPanel(root) {
    return ntA53PanelByTitle(root, "账户信息");
  }

  function ntA53PolicyTextarea(panel) {
    if (!panel) return null;
    for (const textarea of panel.querySelectorAll("textarea")) {
      const context = ntA53Text(
        textarea.closest("label")?.textContent
        || textarea.parentElement?.textContent
        || ""
      );
      const value = ntA53Text(textarea.value);
      if (
        context.includes("密码策略")
        || value.includes("minimum_password_length")
        || value.includes("is_uppercase_characters_required")
      ) return textarea;
    }
    return null;
  }

  function ntA53ParsePolicy(textarea) {
    if (!textarea) return null;
    const raw = ntA53Text(textarea.value);
    if (!raw) return null;
    try {
      const data = JSON.parse(raw);
      return data && typeof data === "object" && !Array.isArray(data) ? data : null;
    } catch (_) {
      return null;
    }
  }

  const ntA53PolicyFields = [
    {
      key: "is_uppercase_characters_required",
      label: "必须包含大写字母",
      help: "密码中至少包含一个 A–Z 字符。",
    },
    {
      key: "is_lowercase_characters_required",
      label: "必须包含小写字母",
      help: "密码中至少包含一个 a–z 字符。",
    },
    {
      key: "is_numeric_characters_required",
      label: "必须包含数字",
      help: "密码中至少包含一个数字。",
    },
    {
      key: "is_special_characters_required",
      label: "必须包含特殊字符",
      help: "要求包含 OCI 支持的特殊字符。",
    },
    {
      key: "is_username_containment_allowed",
      label: "允许包含用户名",
      help: "开启后密码可以包含当前 IAM 用户名。",
    },
  ];

  function ntA53PolicyBaseObject(textarea) {
    const parsed = ntA53ParsePolicy(textarea);
    return parsed ? { ...parsed } : {};
  }

  function ntA53SyncPolicyToTextarea(editor, textarea) {
    if (!editor || !textarea) return;
    const data = ntA53PolicyBaseObject(textarea);
    const lengthInput = editor.querySelector('[data-nt-a53-policy="minimum_password_length"]');
    const length = Number(lengthInput?.value);
    if (Number.isFinite(length) && length > 0) {
      data.minimum_password_length = Math.trunc(length);
    }
    for (const field of ntA53PolicyFields) {
      const control = editor.querySelector(`[data-nt-a53-policy="${field.key}"]`);
      if (control) data[field.key] = Boolean(control.checked);
    }
    textarea.value = JSON.stringify(data, null, 2);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function ntA53RefreshPolicyEditor(panel) {
    if (!panel) return;
    const textarea = ntA53PolicyTextarea(panel);
    const editor = panel.querySelector(".nt-a53-policy-editor");
    if (!textarea || !editor) return;

    const data = ntA53ParsePolicy(textarea);
    const hint = editor.querySelector(".nt-a53-policy-state");
    const controls = editor.querySelectorAll("[data-nt-a53-policy]");

    if (!data) {
      controls.forEach((control) => { control.disabled = true; });
      if (hint) {
        hint.textContent = "尚未读取密码策略";
        hint.className = "nt-a53-policy-state muted";
      }
      return;
    }

    controls.forEach((control) => { control.disabled = false; });
    const lengthInput = editor.querySelector('[data-nt-a53-policy="minimum_password_length"]');
    if (lengthInput) {
      lengthInput.value = String(
        Number.isFinite(Number(data.minimum_password_length))
          ? Number(data.minimum_password_length)
          : 12
      );
    }
    for (const field of ntA53PolicyFields) {
      const control = editor.querySelector(`[data-nt-a53-policy="${field.key}"]`);
      if (control) control.checked = Boolean(data[field.key]);
    }
    if (hint) {
      hint.textContent = "已载入，可直接修改";
      hint.className = "nt-a53-policy-state good";
    }
  }

  function ntA53EnsurePolicyEditor(panel) {
    if (!panel) return;
    const textarea = ntA53PolicyTextarea(panel);
    if (!textarea) return;

    let editor = panel.querySelector(".nt-a53-policy-editor");
    if (!editor) {
      editor = document.createElement("section");
      editor.className = "nt-a53-policy-editor";
      editor.innerHTML = `
        <div class="nt-a53-policy-head">
          <div>
            <h3>密码策略</h3>
            <p>用可读字段直接修改；保存时仍调用原 OCI 密码策略接口。</p>
          </div>
          <span class="nt-a53-policy-state muted">尚未读取密码策略</span>
        </div>
        <div class="nt-a53-policy-grid">
          <label class="nt-a53-policy-length">
            <span>最小密码长度</span>
            <input
              data-nt-a53-policy="minimum_password_length"
              type="number"
              min="1"
              max="128"
              step="1"
              inputmode="numeric"
              disabled
            >
            <small>允许设置的具体范围仍以 OCI 返回结果为准。</small>
          </label>
          ${ntA53PolicyFields.map((field) => `
            <label class="switch-row compact-switch nt-a53-policy-toggle">
              <div>
                <strong>${field.label}</strong>
                <small>${field.help}</small>
              </div>
              <input
                data-nt-a53-policy="${field.key}"
                type="checkbox"
                disabled
              >
            </label>
          `).join("")}
        </div>
      `;

      const originalLabel = textarea.closest("label");
      const anchor = originalLabel || textarea;
      anchor.insertAdjacentElement("beforebegin", editor);

      if (originalLabel) {
        originalLabel.classList.add("nt-a53-policy-json-source");
        originalLabel.hidden = true;
      } else {
        textarea.classList.add("nt-a53-policy-json-source");
        textarea.hidden = true;
      }

      editor.addEventListener("input", (event) => {
        const target = event.target.closest?.("[data-nt-a53-policy]");
        if (!target) return;
        ntA53SyncPolicyToTextarea(editor, textarea);
      });
      editor.addEventListener("change", (event) => {
        const target = event.target.closest?.("[data-nt-a53-policy]");
        if (!target) return;
        ntA53SyncPolicyToTextarea(editor, textarea);
      });

      const saveButton = Array.from(panel.querySelectorAll("button")).find(
        (button) => ntA53Text(button.textContent) === "保存密码策略"
      );
      if (saveButton && !saveButton.closest(".nt-a53-policy-actions")) {
        const actions = document.createElement("div");
        actions.className = "nt-a53-policy-actions";
        editor.appendChild(actions);
        actions.appendChild(saveButton);
      }
    }

    ntA53RefreshPolicyEditor(panel);
  }

  function ntA53HasIamData(panel) {
    if (!panel) return false;
    const text = ntA53Text(panel.textContent);
    if (!text) return false;
    if (text.includes("IAM 用户") && panel.querySelector("tbody tr")) return true;
    if (text.includes("成员关系") && panel.querySelector("table")) return true;
    if (ntA53ParsePolicy(ntA53PolicyTextarea(panel))) return true;
    return false;
  }

  function ntA53CacheBadge(panel, entry) {
    if (!panel) return;
    const readButton = Array.from(panel.querySelectorAll("button")).find(
      (button) => ntA53Text(button.textContent) === "读取"
    );
    let badge = panel.querySelector(".nt-a53-cache-badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "nt-a53-cache-badge";
      badge.title = "缓存仅用于当前浏览器页面会话；不会后台访问 OCI。";
      if (readButton) {
        readButton.insertAdjacentElement("beforebegin", badge);
      } else {
        panel.insertAdjacentElement("afterbegin", badge);
      }
    }

    if (ntA53HasIamData(panel)) {
      if (!entry.cachedAt) entry.cachedAt = new Date();
      const time = entry.cachedAt.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      badge.textContent = `会话缓存 · ${time}`;
      badge.classList.add("is-cached");
    } else {
      badge.textContent = "尚未缓存";
      badge.classList.remove("is-cached");
    }
  }

  function ntA53ScheduleRefresh(panel, entry) {
    [0, 220, 650, 1300, 2400, 4000].forEach((delay) => {
      window.setTimeout(() => {
        if (!panel.isConnected && !ntA53IamCache.has(entry.key)) return;
        ntA53EnsurePolicyEditor(panel);
        if (ntA53HasIamData(panel)) entry.cachedAt = new Date();
        ntA53CacheBadge(panel, entry);
      }, delay);
    });
  }

  function ntA53WirePanel(panel, entry) {
    if (!panel || panel.dataset.ntA53Wired === "1") return;
    panel.dataset.ntA53Wired = "1";

    panel.addEventListener("click", (event) => {
      const button = event.target.closest?.("button");
      if (!button) return;
      const label = ntA53Text(button.textContent);
      if (
        label === "读取"
        || label.includes("密码策略")
        || label.includes("用户")
        || label.includes("用户组")
        || label.includes("成员")
        || label.includes("保存")
        || label.includes("移除")
        || label.includes("管理")
      ) {
        ntA53ScheduleRefresh(panel, entry);
      }
    }, true);
  }

  function ntA53DecorateTables(panel) {
    if (!panel) return;
    for (const table of panel.querySelectorAll("table")) {
      table.classList.add("nt-a53-iam-table");
      const wrap = table.parentElement;
      if (wrap) wrap.classList.add("nt-a53-table-wrap");
    }
    for (const heading of panel.querySelectorAll("h3")) {
      heading.classList.add("nt-a53-subtitle");
    }
  }

  function ntA53DecorateAccountPage(root, panel, entry) {
    const accountInfo = ntA53AccountInfoPanel(root);
    const danger = ntA53DangerPanel(root);
    root.classList.add("nt-a53-account-settings");
    if (accountInfo) accountInfo.classList.add("nt-a53-account-info");
    if (panel) panel.classList.add("nt-a53-iam-panel");
    if (danger) danger.classList.add("nt-a53-danger-panel");

    ntA53DecorateTables(panel);
    ntA53EnsurePolicyEditor(panel);
    ntA53WirePanel(panel, entry);
    ntA53CacheBadge(panel, entry);
  }

  function ntA53RenderAccountSettings() {
    const account = ntA53CurrentAccount();
    const accountId = Number(
      account?.id
      ?? (typeof state !== "undefined" ? state.tenantId : 0)
      ?? 0
    );
    const key = String(accountId || "unknown");
    const cached = ntA53IamCache.get(key) || null;

    ntA53BaseAccountRenderer();

    const root = document.getElementById("tenant-content");
    if (!root) return;

    let fresh = ntA53IamPanel(root);
    let entry = cached;

    if (entry?.panel && fresh && entry.panel !== fresh) {
      fresh.replaceWith(entry.panel);
      fresh = entry.panel;
    } else if (!entry && fresh) {
      entry = {
        key,
        accountId,
        panel: fresh,
        cachedAt: null,
      };
      ntA53IamCache.set(key, entry);
    } else if (entry?.panel && !fresh) {
      const danger = ntA53DangerPanel(root);
      if (danger) root.insertBefore(entry.panel, danger);
      else root.appendChild(entry.panel);
      fresh = entry.panel;
    }

    if (!entry && fresh) {
      entry = {
        key,
        accountId,
        panel: fresh,
        cachedAt: null,
      };
      ntA53IamCache.set(key, entry);
    }

    if (!entry) {
      entry = { key, accountId, panel: fresh, cachedAt: null };
    }

    ntA53DecorateAccountPage(root, fresh, entry);
  }

  window.renderTenantAccountSettings = ntA53RenderAccountSettings;
  window.ntA5RenderAccountSettings = ntA53RenderAccountSettings;
  window.ntA53ClearIamSessionCache = function ntA53ClearIamSessionCache(accountId = null) {
    if (accountId === null || accountId === undefined) {
      ntA53IamCache.clear();
      return;
    }
    ntA53IamCache.delete(String(Number(accountId)));
  };
})();
/* END OCI-N&T V1.0.4 1.0.4-account-settings-iam-a5.3 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-identity-domain-policy-a5.5.1 */
(() => {
  if (window.__ntA55Installed) return;
  window.__ntA55Installed = true;

  const FEATURE = "1.0.4-identity-domain-policy-a5.5.1";
  const STORE_PREFIX = "oci-nt:a5.5:identity-domain-policy:";

  const q = (root, selector) => root ? root.querySelector(selector) : null;
  const qa = (root, selector) => root ? Array.from(root.querySelectorAll(selector)) : [];
  const t = (value) => String(value ?? "").trim();
  const html = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function currentAccountId() {
    try {
      if (typeof currentTenantAccount === "function") {
        const account = currentTenantAccount();
        if (account?.id) return Number(account.id);
      }
    } catch (_) {}
    try {
      if (typeof state !== "undefined" && state?.tenantId) return Number(state.tenantId);
    } catch (_) {}
    return 0;
  }

  function tenantRoot() {
    return document.getElementById("tenant-content");
  }

  function buttonByText(root, label, excludeClass = "") {
    return qa(root, "button").find((button) => (
      t(button.textContent) === label &&
      (!excludeClass || !button.classList.contains(excludeClass))
    )) || null;
  }

  function headingByPrefix(root, prefix) {
    return qa(root, "h2,h3,h4,h5,strong").find((node) => (
      t(node.textContent).startsWith(prefix)
    )) || null;
  }

  function cacheKey(accountId) {
    return `${STORE_PREFIX}${Number(accountId)}`;
  }

  function readDomainCache(accountId) {
    try {
      const raw = localStorage.getItem(cacheKey(accountId));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || parsed.version !== 1 || !parsed.data) return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function writeDomainCache(accountId, data) {
    const entry = {
      version: 1,
      savedAt: new Date().toISOString(),
      data,
    };
    localStorage.setItem(cacheKey(accountId), JSON.stringify(entry));
    return entry;
  }

  function formatSavedAt(value) {
    if (!value) return "尚未读取";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "已缓存";
    return `本地缓存 · ${date.toLocaleString("zh-CN", { hour12: false })}`;
  }

  function fixClassicSevenDayLabel(root) {
    let card = q(root, ".nt-a54-password-expiry");
    if (!card) {
      const heading = qa(root, "strong,b,span,div").find((node) => {
        const value = t(node.textContent);
        return value === "密码有效期" || value === "一次性密码有效期";
      });
      card = heading?.closest("label,.nt-a53-policy-card,.policy-item,.setting-row,.card") || heading?.parentElement || null;
    }
    if (!card) return;

    const label = qa(card, "strong,b,span,div").find((node) => {
      const value = t(node.textContent);
      return value === "密码有效期" || value === "一次性密码有效期";
    });
    if (label) label.textContent = "一次性密码有效期";

    const small = q(card, "small");
    if (small) {
      small.textContent = "新建或重置生成的控制台一次性密码须在 7 天内首次使用；此值由 OCI 固定。";
    }
    card.title = "这里不是普通密码的过期天数。Identity Domains 自定义密码过期天数在下方单独设置。";
  }

  function arrangeIamHeader(root) {
    // R2.2: IAM 顶部操作区由 final-stabilization 唯一接管。
    // A5.5.1 继续只负责 Identity Domains 与成员关系弹窗。
    return;
  }

  function nativeMembershipControls(root) {
    const nativeButton = qa(root, "button").find((button) => (
      t(button.textContent) === "加入用户组" &&
      !button.classList.contains("nt-a55-membership-open") &&
      !button.classList.contains("nt-a55-membership-confirm")
    ));
    if (!nativeButton) return null;

    let node = nativeButton.parentElement;
    let fallback = null;
    while (node && node !== root) {
      const selects = qa(node, "select");
      if (selects.length >= 2) {
        fallback ||= { container: node, button: nativeButton, selects: selects.slice(0, 2) };
        if (!q(node, "table")) {
          return { container: node, button: nativeButton, selects: selects.slice(0, 2) };
        }
      }
      node = node.parentElement;
    }
    return fallback;
  }

  function ensureMembershipDialog() {
    let dialog = document.getElementById("iam-membership-dialog-a55");
    if (dialog) return dialog;

    dialog = document.createElement("dialog");
    dialog.id = "iam-membership-dialog-a55";
    dialog.className = "dialog nt-a55-dialog";
    dialog.innerHTML = `
      <form method="dialog" class="nt-a55-dialog-shell">
        <div class="nt-a55-dialog-head">
          <div>
            <h3>加入用户组</h3>
            <small>选择 IAM 用户与用户组后确认。</small>
          </div>
          <button class="button nt-a55-dialog-x" value="cancel" type="submit" aria-label="关闭">×</button>
        </div>
        <div class="nt-a55-dialog-body">
          <label>
            <span>用户</span>
            <select id="iam-membership-user-a55"></select>
          </label>
          <label>
            <span>用户组</span>
            <select id="iam-membership-group-a55"></select>
          </label>
        </div>
        <div class="nt-a55-dialog-actions">
          <button class="button" value="cancel" type="submit">取消</button>
          <button class="button primary nt-a55-membership-confirm" value="default" type="button">加入用户组</button>
        </div>
      </form>
    `;
    document.body.appendChild(dialog);

    q(dialog, ".nt-a55-membership-confirm").addEventListener("click", () => {
      const root = tenantRoot();
      const native = nativeMembershipControls(root);
      if (!native || native.selects.length < 2) {
        q(dialog, ".nt-a55-dialog-head small").textContent = "当前 IAM 用户/用户组控件不存在，请先重新读取 IAM。";
        return;
      }
      native.selects[0].value = q(dialog, "#iam-membership-user-a55").value;
      native.selects[1].value = q(dialog, "#iam-membership-group-a55").value;
      dialog.close();
      native.button.click();
    });

    return dialog;
  }

  function openMembershipDialog(root) {
    const native = nativeMembershipControls(root);
    const dialog = ensureMembershipDialog();
    const hint = q(dialog, ".nt-a55-dialog-head small");

    if (!native || native.selects.length < 2) {
      hint.textContent = "当前没有可用的 IAM 用户/用户组数据，请先点击“重新读取”。";
      q(dialog, "#iam-membership-user-a55").innerHTML = '<option value="">暂无用户</option>';
      q(dialog, "#iam-membership-group-a55").innerHTML = '<option value="">暂无用户组</option>';
    } else {
      hint.textContent = "选择 IAM 用户与用户组后确认。";
      const [sourceUser, sourceGroup] = native.selects;
      const userSelect = q(dialog, "#iam-membership-user-a55");
      const groupSelect = q(dialog, "#iam-membership-group-a55");
      userSelect.innerHTML = sourceUser.innerHTML;
      groupSelect.innerHTML = sourceGroup.innerHTML;
      userSelect.value = sourceUser.value;
      groupSelect.value = sourceGroup.value;
    }
    dialog.showModal();
  }

  function arrangeMembership(root) {
    const heading = headingByPrefix(root, "成员关系");
    const native = nativeMembershipControls(root);
    if (native?.container) native.container.classList.add("nt-a55-membership-inline-hidden");
    if (!heading) return;

    let button = q(root, ".nt-a55-membership-open");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "button nt-a55-membership-open";
      button.textContent = "加入用户组";
      button.addEventListener("click", () => openMembershipDialog(tenantRoot()));
    }

    // R2.4: never add .nt-a55-membership-head to heading.parentElement.
    const finalBar = heading.closest(".nt-final-membership-bar");
    if (finalBar) {
      if (button.parentElement !== finalBar) finalBar.appendChild(button);
    } else if (button.parentElement !== heading.parentElement) {
      heading.insertAdjacentElement("afterend", button);
    }
  }

  function domainSectionTemplate() {
    return `
      <div class="nt-a55-domain-policy-head">
        <div>
          <strong>Identity Domains 自定义密码策略</strong>
          <small>可设置真实密码过期天数；0 表示永不过期。仅在手动读取或保存时访问 OCI。</small>
        </div>
        <div class="nt-a55-domain-policy-actions">
          <span class="nt-a55-domain-cache">尚未读取</span>
          <button type="button" class="button nt-a55-domain-read">读取 Identity Domains</button>
        </div>
      </div>

      <div class="nt-a55-domain-status muted">尚未读取 Identity Domains 密码策略。</div>

      <div class="nt-a55-domain-selectors">
        <label>
          <span>Identity Domain</span>
          <select class="nt-a55-domain-select"></select>
        </label>
        <label>
          <span>Password Policy</span>
          <select class="nt-a55-policy-select"></select>
        </label>
      </div>

      <div class="nt-a55-domain-form" hidden>
        <div class="nt-a55-domain-policy-meta">
          <div><span>策略</span><strong class="nt-a55-policy-name">—</strong></div>
          <div><span>类型</span><strong class="nt-a55-policy-type">—</strong></div>
          <div><span>优先级</span><strong class="nt-a55-policy-priority">—</strong></div>
        </div>

        <div class="nt-a55-domain-grid">
          <label>
            <span>策略类型</span>
            <select data-a55-field="password_strength">
              <option value="Simple">Simple</option>
              <option value="Standard">Standard</option>
              <option value="Custom">Custom</option>
            </select>
            <small>Simple / Standard 规则由 Oracle 固定；选择 Custom 后可编辑下方规则。</small>
          </label>

          <label class="nt-a55-custom-field">
            <span>密码过期天数</span>
            <input data-a55-field="password_expires_after" type="number" min="0" step="1">
            <small><b>0 = 永不过期</b>；这是 Identity Domains 的真实密码有效期。</small>
          </label>

          <label class="nt-a55-custom-field">
            <span>过期前提醒（天）</span>
            <input data-a55-field="password_expire_warning" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>最小密码长度</span>
            <input data-a55-field="min_length" type="number" min="0" max="500" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>最大密码长度</span>
            <input data-a55-field="max_length" type="number" min="0" max="500" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>账户锁定阈值</span>
            <input data-a55-field="max_incorrect_attempts" type="number" min="0" step="1">
            <small>0 表示不因连续失败次数锁定。</small>
          </label>

          <label class="nt-a55-custom-field">
            <span>历史密码数量</span>
            <input data-a55-field="num_passwords_in_history" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>字母最少</span>
            <input data-a55-field="min_alphas" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>数字最少</span>
            <input data-a55-field="min_numerals" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>特殊字符最少</span>
            <input data-a55-field="min_special_chars" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>大写字母最少</span>
            <input data-a55-field="min_upper_case" type="number" min="0" step="1">
          </label>

          <label class="nt-a55-custom-field">
            <span>小写字母最少</span>
            <input data-a55-field="min_lower_case" type="number" min="0" step="1">
          </label>
        </div>

        <div class="nt-a55-domain-save-row">
          <span class="nt-a55-domain-save-hint">修改只会在点击保存后提交到当前 Identity Domain。</span>
          <button type="button" class="button primary nt-a55-domain-save">保存 Identity Domain 策略</button>
        </div>
      </div>
    `;
  }

  function ensureDomainSection(root) {
    let section = q(root, ".nt-a55-domain-policy");
    if (section) return section;

    section = document.createElement("section");
    section.className = "nt-a55-domain-policy";
    section.dataset.feature = FEATURE;
    section.innerHTML = domainSectionTemplate();

    const classic = q(root, ".nt-a53-policy-editor");
    if (classic?.parentElement) classic.insertAdjacentElement("afterend", section);
    else {
      const danger = headingByPrefix(root, "危险操作");
      if (danger) {
        const dangerBox = danger.closest("section,article,.card,.panel") || danger.parentElement;
        dangerBox?.insertAdjacentElement("beforebegin", section);
      } else {
        root.appendChild(section);
      }
    }

    q(section, ".nt-a55-domain-read").addEventListener("click", () => readDomains(section));
    q(section, ".nt-a55-domain-select").addEventListener("change", () => {
      section.dataset.domainIndex = q(section, ".nt-a55-domain-select").value;
      section.dataset.policyIndex = "0";
      renderDomainSection(section);
    });
    q(section, ".nt-a55-policy-select").addEventListener("change", () => {
      section.dataset.policyIndex = q(section, ".nt-a55-policy-select").value;
      renderDomainSection(section);
    });
    q(section, '[data-a55-field="password_strength"]').addEventListener("change", () => {
      updateCustomDisabled(section);
    });
    q(section, ".nt-a55-domain-save").addEventListener("click", () => saveDomainPolicy(section));

    return section;
  }

  function dataForSection(section) {
    const accountId = currentAccountId();
    const entry = readDomainCache(accountId);
    return { accountId, entry, data: entry?.data || null };
  }

  function selectedDomainPolicy(section) {
    const { data } = dataForSection(section);
    const domains = Array.isArray(data?.domains) ? data.domains : [];
    const di = Math.max(0, Number(section.dataset.domainIndex || 0));
    const domain = domains[di] || null;
    const policies = Array.isArray(domain?.policies) ? domain.policies : [];
    const pi = Math.max(0, Number(section.dataset.policyIndex || 0));
    const policy = policies[pi] || null;
    return { domain, policy, domains, policies, di, pi };
  }

  function setNumberField(section, name, value) {
    const input = q(section, `[data-a55-field="${name}"]`);
    if (!input) return;
    input.value = value === null || value === undefined ? "" : String(value);
  }

  function updateCustomDisabled(section) {
    const strength = t(q(section, '[data-a55-field="password_strength"]')?.value);
    const disabled = strength !== "Custom";
    qa(section, ".nt-a55-custom-field input,.nt-a55-custom-field select").forEach((control) => {
      control.disabled = disabled;
    });
    section.classList.toggle("is-non-custom", disabled);
  }

  function renderDomainSection(section) {
    const { accountId, entry, data } = dataForSection(section);
    const cache = q(section, ".nt-a55-domain-cache");
    cache.textContent = entry ? formatSavedAt(entry.savedAt) : "尚未读取";

    const status = q(section, ".nt-a55-domain-status");
    const readButton = q(section, ".nt-a55-domain-read");
    readButton.textContent = entry ? "重新读取 Identity Domains" : "读取 Identity Domains";

    const domains = Array.isArray(data?.domains) ? data.domains : [];
    const domainSelect = q(section, ".nt-a55-domain-select");
    const policySelect = q(section, ".nt-a55-policy-select");
    const form = q(section, ".nt-a55-domain-form");

    if (!accountId) {
      status.className = "nt-a55-domain-status bad";
      status.textContent = "未识别当前 OCI 账户。";
      form.hidden = true;
      return;
    }

    if (!entry) {
      status.className = "nt-a55-domain-status muted";
      status.textContent = "尚未读取。普通页面打开不会访问 OCI；需要时手动点击“读取 Identity Domains”。";
      domainSelect.innerHTML = '<option value="">尚未读取</option>';
      policySelect.innerHTML = '<option value="">尚未读取</option>';
      form.hidden = true;
      return;
    }

    if (!domains.length) {
      status.className = "nt-a55-domain-status warn";
      status.textContent = "当前租户没有读取到 ACTIVE Identity Domain。";
      domainSelect.innerHTML = '<option value="">无 ACTIVE Domain</option>';
      policySelect.innerHTML = '<option value="">无策略</option>';
      form.hidden = true;
      return;
    }

    let di = Math.max(0, Number(section.dataset.domainIndex || 0));
    if (di >= domains.length) di = 0;
    section.dataset.domainIndex = String(di);

    domainSelect.innerHTML = domains.map((domain, index) => {
      const name = domain.display_name || domain.id || `Domain ${index + 1}`;
      const type = domain.type ? ` · ${domain.type}` : "";
      return `<option value="${index}">${html(name)}${html(type)}</option>`;
    }).join("");
    domainSelect.value = String(di);

    const domain = domains[di];
    const policies = Array.isArray(domain.policies) ? domain.policies : [];
    if (domain.error) {
      status.className = "nt-a55-domain-status bad";
      status.textContent = `Identity Domain 读取失败：${domain.error}`;
    } else {
      status.className = "nt-a55-domain-status good";
      status.textContent = `已读取 ${domains.length} 个 ACTIVE Domain；当前 Domain 有 ${policies.length} 个密码策略。`;
    }

    if (!policies.length) {
      policySelect.innerHTML = '<option value="">无可用 Password Policy</option>';
      form.hidden = true;
      return;
    }

    let pi = Math.max(0, Number(section.dataset.policyIndex || 0));
    if (pi >= policies.length) pi = 0;
    section.dataset.policyIndex = String(pi);

    policySelect.innerHTML = policies.map((policy, index) => (
      `<option value="${index}">${html(policy.name || policy.id || `Policy ${index + 1}`)} · ${html(policy.password_strength || "未知")}</option>`
    )).join("");
    policySelect.value = String(pi);

    const policy = policies[pi];
    form.hidden = false;
    q(section, ".nt-a55-policy-name").textContent = policy.name || policy.id || "—";
    q(section, ".nt-a55-policy-type").textContent = policy.password_strength || "—";
    q(section, ".nt-a55-policy-priority").textContent = policy.priority ?? "—";

    q(section, '[data-a55-field="password_strength"]').value =
      ["Simple", "Standard", "Custom"].includes(policy.password_strength)
        ? policy.password_strength
        : "Custom";

    for (const name of [
      "password_expires_after",
      "password_expire_warning",
      "min_length",
      "max_length",
      "max_incorrect_attempts",
      "num_passwords_in_history",
      "min_alphas",
      "min_numerals",
      "min_special_chars",
      "min_upper_case",
      "min_lower_case",
    ]) {
      setNumberField(section, name, policy[name]);
    }

    const save = q(section, ".nt-a55-domain-save");
    const allowed = policy.update_allowed !== false;
    save.disabled = !allowed;
    q(section, ".nt-a55-domain-save-hint").textContent = allowed
      ? "修改只会在点击保存后提交到当前 Identity Domain。"
      : "Oracle 标记该密码策略为不可更新。";
    updateCustomDisabled(section);
  }

  async function readDomains(section) {
    const accountId = currentAccountId();
    if (!accountId) return;
    const button = q(section, ".nt-a55-domain-read");
    const status = q(section, ".nt-a55-domain-status");
    button.disabled = true;
    status.className = "nt-a55-domain-status loading";
    status.textContent = "正在读取 Identity Domains 密码策略…";
    try {
      const data = await api(`/accounts/${accountId}/iam/identity-domain/password-policies`);
      writeDomainCache(accountId, data);
      section.dataset.domainIndex = "0";
      section.dataset.policyIndex = "0";
      renderDomainSection(section);
    } catch (error) {
      status.className = "nt-a55-domain-status bad";
      status.textContent = error?.message || String(error);
    } finally {
      button.disabled = false;
    }
  }

  function numberValue(section, name) {
    const input = q(section, `[data-a55-field="${name}"]`);
    if (!input || input.value === "") return null;
    const value = Number(input.value);
    if (!Number.isInteger(value) || value < 0) {
      throw new Error(`${input.closest("label")?.querySelector("span")?.textContent || name} 必须是大于等于 0 的整数`);
    }
    return value;
  }

  function policyPayload(section) {
    const strength = t(q(section, '[data-a55-field="password_strength"]').value);
    if (strength !== "Custom") return { password_strength: strength };

    const payload = { password_strength: "Custom" };
    for (const name of [
      "password_expires_after",
      "password_expire_warning",
      "min_length",
      "max_length",
      "max_incorrect_attempts",
      "num_passwords_in_history",
      "min_alphas",
      "min_numerals",
      "min_special_chars",
      "min_upper_case",
      "min_lower_case",
    ]) {
      const value = numberValue(section, name);
      if (value !== null) payload[name] = value;
    }

    if (
      payload.min_length !== undefined &&
      payload.max_length !== undefined &&
      payload.min_length > payload.max_length
    ) {
      throw new Error("最小密码长度不能大于最大密码长度");
    }
    return payload;
  }

  async function saveDomainPolicy(section) {
    const accountId = currentAccountId();
    const { domain, policy, domains, di, pi } = selectedDomainPolicy(section);
    if (!accountId || !domain || !policy) return;

    const save = q(section, ".nt-a55-domain-save");
    const status = q(section, ".nt-a55-domain-status");
    let payload;
    try {
      payload = policyPayload(section);
    } catch (error) {
      status.className = "nt-a55-domain-status bad";
      status.textContent = error.message || String(error);
      return;
    }

    save.disabled = true;
    status.className = "nt-a55-domain-status loading";
    status.textContent = "正在保存 Identity Domain 密码策略…";
    try {
      const result = await api(
        `/accounts/${accountId}/iam/identity-domain/password-policies/${encodeURIComponent(domain.id)}/${encodeURIComponent(policy.id)}`,
        { method: "PUT", body: payload },
      );
      if (!result?.policy) throw new Error("保存成功，但返回的密码策略为空");

      const entry = readDomainCache(accountId);
      const data = entry?.data || { account_id: accountId, domains };
      if (Array.isArray(data.domains) && data.domains[di]?.policies?.[pi]) {
        data.domains[di].policies[pi] = result.policy;
      }
      data.read_at = new Date().toISOString();
      writeDomainCache(accountId, data);
      renderDomainSection(section);
      status.className = "nt-a55-domain-status good";
      status.textContent = result.policy.password_expires_after === 0
        ? "Identity Domain 密码策略已保存：密码设置为永不过期。"
        : `Identity Domain 密码策略已保存：${result.policy.password_expires_after ?? "—"} 天后过期。`;
    } catch (error) {
      status.className = "nt-a55-domain-status bad";
      status.textContent = error?.message || String(error);
      save.disabled = false;
    }
  }

  function decorate() {
    const root = tenantRoot();
    if (!root) return;
    const iamTitle = headingByPrefix(root, "OCI IAM");
    if (!iamTitle && !q(root, ".nt-a53-policy-editor")) return;

    fixClassicSevenDayLabel(root);
    arrangeIamHeader(root);
    arrangeMembership(root);
    const section = ensureDomainSection(root);
    renderDomainSection(section);
  }

  const previousRender = window.renderTenantAccountSettings;
  if (typeof previousRender === "function") {
    window.renderTenantAccountSettings = async function ntA55RenderTenantAccountSettings(...args) {
      const result = await previousRender.apply(this, args);
      decorate();
      return result;
    };
  }

  window.ntA55DecorateAccountSettings = decorate;
  window.ntA55ClearIdentityDomainCache = function ntA55ClearIdentityDomainCache(accountId) {
    try {
      localStorage.removeItem(cacheKey(Number(accountId || currentAccountId())));
    } catch (_) {}
  };

  function scheduleDecorate() {
    [0, 80, 220, 520, 1100].forEach((delay) => {
      window.setTimeout(decorate, delay);
    });
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("button,[data-tenant-tab]");
    if (!button) return;
    const tab = button.getAttribute?.("data-tenant-tab") || "";
    const label = t(button.textContent);
    if (
      tab === "account" ||
      tab === "tenancy" ||
      label === "账户设置" ||
      label === "密码策略" ||
      label === "重新读取" ||
      label === "读取"
    ) {
      scheduleDecorate();
    }
  }, true);

  window.addEventListener("hashchange", scheduleDecorate);
  scheduleDecorate();
})();
/* END OCI-N&T V1.0.4 1.0.4-identity-domain-policy-a5.5.1 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-final-stabilization1 */
(() => {
  if (window.__ntFinalStabilization1R2Installed) return;
  window.__ntFinalStabilization1R2Installed = true;

  const FEATURE = "1.0.4-final-stabilization1-r2.4.1-domain-policy-cache-replay";
  const q = (root, selector) => root ? root.querySelector(selector) : null;
  const qa = (root, selector) => root ? Array.from(root.querySelectorAll(selector)) : [];
  const text = value => String(value ?? "").trim();
  const compact = value => text(value).replace(/\s+/g, "");
  const nativeFetch = window.fetch.bind(window);
  const forceLive = new Map();
  const ntReadCacheMemo = new Map();
  const ntLegacyCacheProbeDone = new Set();
  const NT_READ_CACHE_MEMO_MS = 5000;

  function appState() {
    try {
      if (typeof state !== "undefined" && state) return state;
    } catch (_) {}
    return window.state || null;
  }

  function currentAccountId() {
    const source = appState();
    const raw = source?.tenantId ?? source?.tenantAccountId ?? window.state?.tenantId ?? "";
    const value = Number(raw);
    return Number.isInteger(value) && value > 0 ? value : null;
  }

  function fmtSavedAt(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return text(value);
    const p = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function requestMethod(input, init) {
    try {
      if (init?.method) return String(init.method).toUpperCase();
      if (input instanceof Request && input.method) return String(input.method).toUpperCase();
    } catch (_) {}
    return "GET";
  }

  function requestUrl(input) {
    try { return input instanceof Request ? input.url : String(input); }
    catch (_) { return String(input); }
  }

  function cacheMeta(raw) {
    let url;
    try { url = new URL(raw, location.origin); } catch (_) { return null; }
    if (url.origin !== location.origin) return null;
    const m = url.pathname.match(/\/api\/v1\/accounts\/(\d+)\/(.+)$/);
    if (!m) return null;
    const accountId = Number(m[1]);
    const tail = m[2].replace(/\/+$/, "");
    if (!accountId || tail === "read-cache-summary" || tail.startsWith("read-cache/")) return null;

    let category = null;
    if (/^iam\/identity-domain\/password-policies(?:\/|$)/.test(tail)) category = "identity-domain-password-policy";
    else if (/^iam\/password-policy(?:\/|$)/.test(tail)) category = "password-policy";
    else if (tail === "iam" || /^iam\/(?:users|groups|memberships)(?:\/|$)/.test(tail)) category = "iam";
    else if (tail === "regions" || /^regions\//.test(tail) || /region-subscriptions/.test(tail)) category = "regions";
    else if (tail === "limits" || /(?:^|\/)(?:limits|quotas)(?:\/|$)/.test(tail)) category = "limits";
    else if (tail === "oci-audit" || /(?:^|\/)audit(?:\/|$)/.test(tail)) category = "audit";
    else if (/^instances\/[^/]+\/network(?:\/|$)/.test(tail) || /(?:^|\/)(?:vnics|vnic-attachments|ipv6)(?:\/|$)/.test(tail)) category = "network";
    else if (/(?:security-lists|network-security-groups|security-rules)/.test(tail)) category = "security";
    else if (/(?:^|\/)boot-volumes?(?:\/|$)|boot-volume-backups?/.test(tail)) category = "boot-volumes";
    else if (/(?:^|\/)images?(?:\/|$)/.test(tail) && !tail.startsWith("launch/jobs")) category = "images";
    else if (/^vnc\/sessions(?:\/|$)/.test(tail) || /console-connections/.test(tail)) category = "vnc";
    else if (tail === "object-storage" || /^object-storage\//.test(tail)) category = "object-storage";
    else if (/(?:^|\/)metrics?(?:\/|$)/.test(tail)) category = "metrics";
    else if (tail === "costs" || /(?:cost|usage|spend)/.test(tail)) category = "costs";
    else if (tail === "compartments" || /^launch\/(?:catalog|images|shapes|availability)/.test(tail)) category = "launch-catalog";
    if (!category) return null;

    const params = new URLSearchParams(url.search);
    for (const name of ["refresh", "force", "reload"]) params.delete(name);
    const direct = params.get("direct") === "true";
    params.delete("direct");
    const query = params.toString();
    const key = `${tail}${query ? `?${query}` : ""}`;
    return { accountId, category, tail, key, direct };
  }

  // api() already prefixes /api/v1. This path MUST remain relative to that helper.
  const cacheApiPath = (accountId, category, key) => {
    const base = `/accounts/${accountId}/read-cache/${encodeURIComponent(category)}`;
    return key == null ? base : `${base}?key=${encodeURIComponent(key)}`;
  };

  async function cacheGet(accountId, category, key="default") {
    const canonical = String(key || "default");
    const memoKey = `${accountId}:${category}:${canonical}`;
    const now = Date.now();
    const memo = ntReadCacheMemo.get(memoKey);
    if (memo && memo.until > now) return await memo.promise;

    const load = (async () => {
      const readOne = async (candidate) => {
        try {
          const entry = await api(cacheApiPath(accountId, category, candidate));
          return entry?.cached ? entry : null;
        } catch (_) {
          return null;
        }
      };

      let entry = await readOne(canonical);
      if (entry) return entry;

      if (
        canonical !== "default" &&
        !/[?&](?:refresh|force|reload)=/i.test(canonical) &&
        !ntLegacyCacheProbeDone.has(memoKey)
      ) {
        ntLegacyCacheProbeDone.add(memoKey);
        const sep = canonical.includes("?") ? "&" : "?";
        for (const suffix of ["refresh=true", "force=true", "reload=true"]) {
          const legacyKey = `${canonical}${sep}${suffix}`;
          const legacy = await readOne(legacyKey);
          if (!legacy) continue;
          try {
            await cachePut(
              accountId,
              category,
              canonical,
              legacy.payload,
              legacy.read_at || legacy.saved_at || null,
            );
          } catch (_) {}
          return legacy;
        }
      }
      return null;
    })();

    ntReadCacheMemo.set(memoKey, { until: now + NT_READ_CACHE_MEMO_MS, promise: load });
    try {
      return await load;
    } catch (error) {
      ntReadCacheMemo.delete(memoKey);
      throw error;
    }
  }

  async function cachePut(accountId, category, key, payload, readAt) {
    const prefix = `${accountId}:${category}:`;
    for (const memoKey of ntReadCacheMemo.keys()) {
      if (memoKey.startsWith(prefix)) ntReadCacheMemo.delete(memoKey);
    }
    try {
      return await api(cacheApiPath(accountId, category, null), {
        method: "PUT",
        body: { key, payload, source: "OCI", read_at: readAt || new Date().toISOString() },
      });
    } catch (_) { return null; }
  }

  async function cacheDelete(accountId, category) {
    const prefix = `${accountId}:${category}:`;
    for (const memoKey of ntReadCacheMemo.keys()) {
      if (memoKey.startsWith(prefix)) ntReadCacheMemo.delete(memoKey);
    }
    try { await api(cacheApiPath(accountId, category, null), { method: "DELETE" }); } catch (_) {}
  }

  function cachedResponse(entry) {
    return new Response(JSON.stringify(entry.payload), {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-OCI-NT-Cache": "SQLITE-HIT",
        "X-OCI-NT-Cache-Saved-At": entry.read_at || "",
      },
    });
  }

  function forceKey(accountId, category) { return `${accountId}:${category}`; }
  function forceCategory(accountId, category, ttl=20000) {
    if (!accountId || !category) return;
    forceLive.set(forceKey(accountId, category), Date.now() + ttl);
  }
  function forceAccount(accountId) {
    if (!accountId) return;
    ["iam","password-policy","identity-domain-password-policy","regions","limits","audit","network","security","boot-volumes","images","vnc","object-storage","metrics","costs","launch-catalog"]
      .forEach(category => forceCategory(accountId, category));
  }

  function readLike(meta, method) {
    if (!meta) return false;
    if (method === "GET") return true;
    // OCI Usage/Cost endpoint is read-only but implemented as POST.
    return method === "POST" && meta.category === "costs" && meta.tail === "costs";
  }

  async function bodyToken(input, init, method) {
    if (method === "GET" || method === "HEAD") return "";
    let raw = init?.body;
    try {
      if (raw == null && input instanceof Request) raw = await input.clone().text();
    } catch (_) {}
    if (raw == null || raw === "") return "";
    if (typeof raw !== "string") {
      try { raw = JSON.stringify(raw); } catch (_) { raw = String(raw); }
    }
    raw = String(raw).trim();
    if (!raw) return "";
    let hash = 2166136261;
    for (let i=0; i<raw.length; i++) {
      hash ^= raw.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return `|body:${(hash >>> 0).toString(16)}:${raw.length}`;
  }

  window.fetch = async function ntFinalSqliteFetchR2(input, init) {
    const meta = cacheMeta(requestUrl(input));
    const method = requestMethod(input, init);
    if (meta?.direct) return nativeFetch(input, init);
    if (meta && readLike(meta, method)) {
      const token = await bodyToken(input, init, method);
      const cacheKey = `${meta.key}${token}`;
      const forced = Number(forceLive.get(forceKey(meta.accountId, meta.category)) || 0) > Date.now();
      if (!forced) {
        try {
          const entry = await cacheGet(meta.accountId, meta.category, cacheKey);
          if (entry) return cachedResponse(entry);
        } catch (_) {}
      }

      const response = await nativeFetch(input, init);
      if (response.ok) {
        try {
          const payload = await response.clone().json();
          const saved = await cachePut(meta.accountId, meta.category, cacheKey, payload);
          if (saved) {
            // Keep a very short live window for any sibling requests started by the same manual read,
            // then ordinary page loads fall back to SQLite instead of OCI.
            forceLive.set(forceKey(meta.accountId, meta.category), Date.now() + 2500);
            setTimeout(() => apply(), 0);
          }
        } catch (_) {}
      }
      return response;
    }

    const response = await nativeFetch(input, init);
    if (meta && !readLike(meta, method) && response.ok) {
      await cacheDelete(meta.accountId, meta.category);
      forceCategory(meta.accountId, meta.category);
      setTimeout(() => apply(), 0);
    }
    return response;
  };

  async function migrateIdentityDomainLegacy(accountId) {
    if (!accountId) return;
    const key = `oci-nt:a5.5:identity-domain-policy:${accountId}`;
    let old;
    try { old = JSON.parse(localStorage.getItem(key)); } catch (_) { return; }
    if (old?.data === undefined) return;
    const requestKey = "iam/identity-domain/password-policies";
    try {
      if (!(await cacheGet(accountId, "identity-domain-password-policy", requestKey))) {
        await cachePut(
          accountId,
          "identity-domain-password-policy",
          requestKey,
          old.data,
          old?.savedAt || old?.saved_at || null,
        );
      }
    } catch (_) {}
  }

  async function mirrorIdentityDomain(accountId) {
    if (!accountId) return false;
    try {
      const entry = await cacheGet(
        accountId,
        "identity-domain-password-policy",
        "iam/identity-domain/password-policies",
      );
      if (!entry) return false;

      // A5.5.1 readDomainCache() requires version === 1.
      // R2.4 previously omitted this field, so valid SQLite cache was rejected.
      localStorage.setItem(
        `oci-nt:a5.5:identity-domain-policy:${accountId}`,
        JSON.stringify({
          version: 1,
          savedAt: entry.read_at || entry.saved_at || new Date().toISOString(),
          data: entry.payload,
        }),
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  function stripPlus(label) {
    return String(label || "").replace(/^[+＋]\s*(?=(添加|增加|新增|加入|新建))/, "");
  }
  function normalizeButtons(scope=document) {
    qa(scope,"button").forEach(button => {
      const before = text(button.textContent), after = stripPlus(before);
      if (after !== before) button.textContent = after;
    });
  }

  function findHeading(scope, prefix) {
    return qa(scope,"h1,h2,h3,h4,h5,strong").find(n => text(n.textContent).startsWith(prefix)) || null;
  }

  function commonAncestor(nodes, stop) {
    const list = nodes.filter(Boolean);
    if (!list.length) return null;
    let node = list[0];
    while (node && node !== stop) {
      if (list.every(item => node.contains(item))) return node;
      node = node.parentElement;
    }
    return null;
  }

  function findIamPanel(scope) {
    const direct = q(scope, ".nt-a53-iam-panel");
    if (direct && findHeading(direct, "OCI IAM")) return direct;

    const title = findHeading(scope,"OCI IAM");
    if (!title) return null;

    let node = title.parentElement;
    while (node && node !== scope) {
      if (
        node.classList?.contains("panel") ||
        node.classList?.contains("page-card") ||
        node.matches?.("section,article")
      ) {
        const body = text(node.textContent);
        if (body.includes("OCI IAM")) return node;
      }
      node = node.parentElement;
    }
    return title.parentElement;
  }

  async function updateCacheBadge(panel, accountId) {
    const badge = q(panel,":scope > .nt-final-iam-toolbar .nt-final-cache");
    if (!badge) return;
    if (!accountId) {
      badge.textContent = "本地缓存 · 未选择账户";
      badge.classList.remove("is-cached");
      return;
    }
    let latest = null;
    try {
      const summary = await api(`/accounts/${accountId}/read-cache-summary`);
      for (const category of ["iam","password-policy"]) {
        const raw = summary?.categories?.[category]?.read_at;
        const stamp = raw ? new Date(raw).getTime() : NaN;
        if (!Number.isNaN(stamp) && (!latest || stamp > latest.t)) latest = {t:stamp, raw};
      }
    } catch (_) {}
    badge.textContent = latest ? `本地缓存 · ${fmtSavedAt(latest.raw)}` : "本地缓存 · 尚未读取";
    badge.classList.toggle("is-cached",Boolean(latest));
  }

  function retireHeaderArtifacts(panel, titleArea, toolbar) {
    qa(panel, ".nt-a53-cache-badge,.nt-a54-cache-badge,[data-cache-category='iam'],.nt-a54-iam-head-actions,.nt-a55-iam-head-actions,.nt-a552-iam-toolbar,.nt-a553-iam-toolbar,.nt-a554-toolbar")
      .forEach(node => { if (node !== toolbar && !toolbar.contains(node)) node.classList.add("nt-final-retired"); });

    if (titleArea) {
      qa(titleArea, ":scope > span,:scope > i,:scope > em").forEach(node => {
        if (!text(node.textContent) && !node.querySelector("input,select,button")) node.classList.add("nt-final-header-artifact");
      });
    }
    qa(panel, ".loading,.loader,.spinner,[class*='spinner']").forEach(node => {
      if ((titleArea?.contains(node) || toolbar.contains(node)) && !text(node.textContent)) {
        node.classList.add("nt-final-header-artifact");
      }
    });
  }

  function labelTextFor(select) {
    const label = select?.closest("label");
    const caption = label ? q(label,":scope > span") : null;
    return compact(caption?.textContent || label?.textContent || "");
  }

  function hideLegacyMembershipSource(panel, member) {
    if (!member) return;
    const selects = qa(panel,"select").filter(select => !select.closest(".nt-a55-domain-policy"));
    const user = selects.find(select => labelTextFor(select) === "用户");
    const group = selects.find(select => labelTextFor(select) === "用户组");
    if (!user || !group) return;
    const userNode = user.closest("label") || user;
    const groupNode = group.closest("label") || group;
    const container = commonAncestor([userNode,groupNode],panel);
    if (container && container !== panel && !container.contains(member) && !member.contains(container) && !q(container,"table")) {
      container.classList.add("nt-final-membership-source");
      return;
    }
    // Fallback for older flat DOMs where the two labels are direct children of the IAM card.
    userNode.classList.add("nt-final-membership-source");
    groupNode.classList.add("nt-final-membership-source");
    const nativeJoin = qa(panel,"button").find(button => (
      text(button.textContent).includes("加入用户组") &&
      !button.classList.contains("nt-a55-membership-open") &&
      !button.classList.contains("nt-a55-membership-confirm")
    ));
    if (nativeJoin && !nativeJoin.closest(".nt-final-membership-bar")) nativeJoin.classList.add("nt-final-membership-source");
  }

  function normalizeIam(scope, accountId) {
    const panel = findIamPanel(scope);
    if (!panel) return;
    panel.classList.add("nt-final-iam-panel","nt-r22-iam-panel");
    const title = findHeading(panel,"OCI IAM");
    const titleArea = title?.parentElement || null;
    if (titleArea) titleArea.classList.add("nt-final-iam-title-area");

    const buttons = qa(panel,"button");
    const add = buttons.find(b => /(?:添加|增加)用户/.test(compact(b.textContent)));
    const reads = buttons.filter(b =>
      !b.closest(".nt-a55-domain-policy") && !b.closest(".nt-a53-policy-editor") &&
      ["读取","重新读取"].includes(text(b.textContent))
    );
    const reload = reads.find(b => text(b.textContent)==="重新读取") || reads.at(-1) || null;

    buttons.forEach(b => {
      if (!b.closest(".nt-a55-domain-policy") && !b.closest(".nt-a53-policy-editor") && text(b.textContent)==="密码策略") b.remove();
    });

    let toolbar = q(panel,":scope > .nt-final-iam-toolbar");
    if (!toolbar) {
      toolbar = document.createElement("div");
      toolbar.className = "nt-final-iam-toolbar nt-r22-iam-toolbar";
      panel.insertBefore(toolbar,panel.firstChild);
    } else {
      toolbar.classList.add("nt-r22-iam-toolbar");
    }

    qa(panel,".nt-a54-iam-head-actions,.nt-a55-iam-head-actions,.nt-a552-iam-toolbar,.nt-a553-iam-toolbar,.nt-a554-toolbar")
      .forEach(node => { if (node !== toolbar) node.classList.add("nt-final-retired"); });
    reads.forEach(b => { if (b !== reload) b.classList.add("nt-final-retired"); });

    let badge = q(toolbar,".nt-final-cache");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "nt-final-cache";
    }
    if (!text(badge.textContent)) badge.textContent = "本地缓存 · 尚未读取";

    toolbar.replaceChildren(badge);
    if (reload) {
      reload.classList.remove("nt-final-retired");
      reload.textContent="重新读取";
      toolbar.appendChild(reload);
    }
    if (add) {
      add.classList.remove("nt-final-retired");
      add.textContent="增加用户";
      add.classList.add("nt-final-primary");
      toolbar.appendChild(add);
    }
    retireHeaderArtifacts(panel,titleArea,toolbar);
    updateCacheBadge(panel,accountId);

    const member = findHeading(panel,"成员关系");
    hideLegacyMembershipSource(panel,member);
    if (member) {
      const join = q(panel,".nt-a55-membership-open") || qa(panel,"button").find(b => text(b.textContent).includes("加入用户组") && !b.closest(".nt-final-membership-source"));
      if (join) {
        let bar=member.closest(".nt-final-membership-bar");
        if (!bar) {
          bar=document.createElement("div");
          bar.className="nt-final-membership-bar";
          member.parentElement?.insertBefore(bar,member);
          bar.appendChild(member);
        }
        join.textContent="加入用户组";
        join.classList.add("nt-final-membership-button");
        if (join.parentElement!==bar) bar.appendChild(join);
      }
    }
  }

  function normalizePolicies(scope) {
    const classic=q(scope,".nt-a53-policy-editor");
    if (classic) {
      classic.classList.add("nt-final-classic-policy");
      const h=findHeading(classic,"密码策略") || findHeading(classic,"OCI IAM 基础密码规则");
      if (h) h.textContent="OCI IAM 基础密码规则";
    }
    const domain=q(scope,".nt-a55-domain-policy");
    if (domain) {
      domain.classList.add("nt-final-domain-policy");
      const h=findHeading(domain,"Identity Domains") || findHeading(domain,"密码策略");
      if (h) h.textContent="密码策略";
    }
  }

  function reorderTabs() {
    const buttons=qa(document,"[data-tenant-tab]");
    if (!buttons.length) return;
    const parent=buttons[0].parentElement;
    if (!parent || !buttons.every(b => b.parentElement===parent)) return;
    const order=["实例","创建实例","开机任务","IP 与 VNIC","安全规则","引导卷与镜像","VNC","对象存储","实例指标","账户费用","区域、配额与审计","账户设置"];
    const rank=b => { const i=order.indexOf(text(b.textContent)); return i<0?999:i; };
    buttons.slice().sort((a,b)=>rank(a)-rank(b)).forEach(b=>parent.appendChild(b));
  }

  async function verifyBackups(button) {
    const original=text(button.textContent)||"验证全部备份";
    button.disabled=true;
    try {
      const rows=await api("/system/backups");
      if (!Array.isArray(rows) || !rows.length) { toast("暂无备份需要验证"); return; }
      let passed=0;
      for (let i=0;i<rows.length;i++) {
        if (!rows[i]?.name) continue;
        button.textContent=`验证中 ${i+1}/${rows.length}`;
        await api(`/system/backups/${encodeURIComponent(rows[i].name)}/verify`,{method:"POST"});
        passed++;
      }
      toast(`备份完整性验证完成：${passed}/${rows.length} 通过`,"good");
    } catch (error) {
      toast(`备份验证失败：${error?.message || error}`,"bad");
    } finally {
      button.disabled=false; button.textContent=original;
    }
  }

  async function apply() {
    normalizeButtons(document);
    reorderTabs();

    const accountId=currentAccountId();
    const scope=document.getElementById("tenant-content");

    if (accountId) {
      await migrateIdentityDomainLegacy(accountId);
      await mirrorIdentityDomain(accountId);
    }

    if (scope) {
      try {
        if (accountId && typeof window.ntA55DecorateAccountSettings === "function") {
          window.ntA55DecorateAccountSettings();
        }
      } catch (_) {}

      const iamPanel = q(scope, ".nt-a53-iam-panel") || findIamPanel(scope);
      if (iamPanel) {
        qa(iamPanel, ".nt-a55-membership-head").forEach(node => {
          if (!node.classList.contains("nt-final-membership-bar")) {
            node.classList.remove("nt-a55-membership-head");
          }
        });
      }

      normalizeIam(scope,accountId);
      normalizePolicies(scope);

      if (accountId) ntR23ScheduleHydration(accountId);
    }
  }
  function settle() { [0,60,160,360,800,1600,3000].forEach(ms=>setTimeout(apply,ms)); }

  document.addEventListener("click",event=>{
    const button=event.target.closest?.("button");
    if (!button) return;
    const clean=stripPlus(text(button.textContent));
    if (clean!==text(button.textContent)) button.textContent=clean;
    if (event.isTrusted && /^(读取|重新读取|查询|同步)/.test(clean) && button.closest("#tenant-content")) forceAccount(currentAccountId());
    if (button.id==="backup-verify-all") { event.preventDefault(); verifyBackups(button); return; }
    if (clean.includes("用户") || clean.includes("密码策略") || clean.includes("读取") || clean.includes("查询")) settle();
  },true);


  /* R2.3 SQLITE PAGE HYDRATION */
  const ntR23HydrationPending = new Set();

  function ntR23IamHasRenderedData(panel) {
    if (!panel) return false;
    const rows = qa(panel, ".nt-a53-iam-table tbody tr, table tbody tr");
    const body = text(panel.textContent);
    return rows.length > 0 && (
      body.includes("IAM 用户") ||
      body.includes("IAM 用户组") ||
      body.includes("成员关系")
    );
  }

  function ntR23FindIamReadButton(panel) {
    if (!panel) return null;
    return qa(panel,"button").find(button => (
      !button.closest(".nt-a55-domain-policy") &&
      !button.closest(".nt-a53-policy-editor") &&
      ["读取","重新读取"].includes(text(button.textContent))
    )) || null;
  }

  async function ntR23HydrateAccountSettings(accountId) {
    const id = Number(accountId || 0);
    if (!id || ntR23HydrationPending.has(id)) return;

    const scope = document.getElementById("tenant-content");
    if (!scope || !findHeading(scope,"OCI IAM")) return;

    ntR23HydrationPending.add(id);
    let clickedIam = false;
    try {
      const domainEntry = await cacheGet(
        id,
        "identity-domain-password-policy",
        "iam/identity-domain/password-policies",
      );
      if (domainEntry) {
        await mirrorIdentityDomain(id);
        try {
          if (typeof window.ntA55DecorateAccountSettings === "function") {
            window.ntA55DecorateAccountSettings();
          }
        } catch (_) {}
      }

      const panel = q(scope, ".nt-a53-iam-panel") || findIamPanel(scope);
      if (panel && !ntR23IamHasRenderedData(panel)) {
        const iamEntry = await cacheGet(id, "iam", "iam");
        if (iamEntry) {
          const readButton = ntR23FindIamReadButton(panel);
          if (readButton && !readButton.disabled) {
            panel.dataset.ntR24Hydrating = "1";
            readButton.click();
            clickedIam = true;
          }
        }
      }

      [0,80,180,360,700,1300].forEach(ms => setTimeout(() => {
        const currentScope = document.getElementById("tenant-content");
        const currentPanel = currentScope
          ? (q(currentScope, ".nt-a53-iam-panel") || findIamPanel(currentScope))
          : null;

        if (currentPanel) {
          qa(currentPanel, ".nt-a55-membership-head").forEach(node => {
            if (!node.classList.contains("nt-final-membership-bar")) {
              node.classList.remove("nt-a55-membership-head");
            }
          });
          normalizeIam(currentScope, id);
          normalizePolicies(currentScope);
        }

        if (domainEntry) {
          try {
            if (typeof window.ntA55DecorateAccountSettings === "function") {
              window.ntA55DecorateAccountSettings();
            }
          } catch (_) {}
        }
      }, ms));
    } finally {
      setTimeout(() => ntR23HydrationPending.delete(id), clickedIam ? 1600 : 120);
    }
  }

  function ntR23ScheduleHydration(accountId) {
    const id = Number(accountId || 0);
    if (!id) return;
    [0,120,360,900,1800,3200].forEach(ms => {
      setTimeout(() => ntR23HydrateAccountSettings(id), ms);
    });
  }

  const previous=window.renderTenantAccountSettings;
  if (typeof previous==="function") {
    window.renderTenantAccountSettings=async function ntFinalAccountSettingsR24(...args) {
      const beforeId=currentAccountId();
      const result=await previous.apply(this,args);
      const accountId=currentAccountId() || beforeId;

      if (accountId) {
        await migrateIdentityDomainLegacy(accountId);
        await mirrorIdentityDomain(accountId);
      }

      try {
        if (accountId && typeof window.ntA55DecorateAccountSettings === "function") {
          window.ntA55DecorateAccountSettings();
        }
      } catch (_) {}

      settle();
      if (accountId) ntR23ScheduleHydration(accountId);
      return result;
    };
  }

  settle();
  setTimeout(() => {
    const accountId=currentAccountId();
    if (accountId) ntR23ScheduleHydration(accountId);
  }, 180);
  window.ntFinalStabilizationApply=apply;
  window.ntFinalStabilizationFeature=FEATURE;
})();
  /* OCI-N&T V1.0.4 1.0.4-r2.3-sqlite-cache-hydration */
  /* OCI-N&T V1.0.4 1.0.4-r2.4-cache-layout-stable */
  /* OCI-N&T V1.0.4 1.0.4-r2.4.1-domain-policy-cache-replay */
/* END OCI-N&T V1.0.4 1.0.4-final-stabilization1 */

/* BEGIN OCI-N&T V1.0.4 1.0.4-r2.1-iam-actions */
(() => {
  /* R2.2 retired legacy UI controller: R2.1 */
})();
/* END OCI-N&T V1.0.4 1.0.4-r2.1-iam-actions */

/* BEGIN OCI-N&T V1.0.4 1.0.4-adaptive-launch-c2 */
const ntSchedulerC2 = {
  settings: null,
  defaults: null,
  status: null,
  loading: false,
};

function ntSchedulerReasonLabel(reason) {
  return ({
    rate_limit: "429 降速",
    transient_error: "临时错误退避",
    proxy_pause: "代理异常暂停",
    capacity: "容量不足",
    success_recovery: "已恢复",
    other_failure: "其他错误",
  })[reason] || (reason ? reason : "正常");
}

function ntSchedulerEnsurePanel() {
  const page = $("launch-page");
  if (!page) return null;

  let panel = $("nt-scheduler-panel");
  if (panel) return panel;

  panel = document.createElement("section");
  panel.id = "nt-scheduler-panel";
  panel.className = "panel nt-scheduler-panel nt-scheduler-panel-compact";
  panel.innerHTML = `
    <div class="panel-head nt-scheduler-head nt-scheduler-head-compact">
      <div>
        <h2>智能请求调度</h2>
        <p>保存后立即生效 · SQLite 持久化 · 不影响 OCI 官方签名与正常 TLS 行为。</p>
      </div>
      <div class="page-toolbar nt-scheduler-actions-top">
        <label class="nt-scheduler-toggle-chip" title="启用或关闭智能请求调度">
          <span>启用智能调度</span>
          <input id="nt-scheduler-enabled" type="checkbox">
        </label>
        <button id="nt-scheduler-refresh" class="button" type="button">刷新</button>
        <button id="nt-scheduler-reset-top" class="button" type="button">恢复默认</button>
        <button id="nt-scheduler-save-top" class="button primary" type="button">保存设置</button>
      </div>
    </div>

    <div class="nt-scheduler-overview-strip">
      <div><strong>智能调度状态</strong><span id="nt-scheduler-isolation">读取中……</span></div>
      <small>配置保存后立即生效 · 运行统计在 API 重启后重新累计</small>
    </div>

    <div class="nt-scheduler-stat-grid">
      <article class="nt-scheduler-stat-card tone-green">
        <div><span>租户总数</span><b class="nt-stat-badge">租户</b></div>
        <strong id="nt-scheduler-total">—</strong>
        <small>全部 OCI 租户</small>
      </article>
      <article class="nt-scheduler-stat-card tone-blue">
        <div><span>已绑定代理</span><b class="nt-stat-badge">代理</b></div>
        <strong id="nt-scheduler-proxy">—</strong>
        <small>独立代理覆盖情况</small>
      </article>
      <article class="nt-scheduler-stat-card tone-purple">
        <div><span>429 限流</span><b class="nt-stat-badge">限流</b></div>
        <strong id="nt-scheduler-429">—</strong>
        <small>本次 API 进程累计</small>
      </article>
      <article class="nt-scheduler-stat-card tone-orange">
        <div><span>动态控制</span><b class="nt-stat-badge">状态</b></div>
        <strong id="nt-scheduler-active">—</strong>
        <small><span id="nt-scheduler-throttled">0</span> 降速 · <span id="nt-scheduler-paused">0</span> 代理暂停</small>
      </article>
    </div>

    <form id="nt-scheduler-form" class="nt-scheduler-form nt-scheduler-form-compact">
      <div class="nt-scheduler-basic-grid">
        <label><span>全局最大并发</span><input id="nt-scheduler-global" type="number" min="1" max="32" step="1"></label>
        <label><span>单租户最大并发</span><input id="nt-scheduler-tenant" type="number" min="1" max="8" step="1"></label>
        <label class="nt-scheduler-range"><span>启动错峰（秒）</span><div><input id="nt-scheduler-start-min" type="number" min="0" max="60" step="0.1"><em>～</em><input id="nt-scheduler-start-max" type="number" min="0" max="120" step="0.1"></div></label>
        <label class="nt-scheduler-range"><span>Jitter（秒）</span><div><input id="nt-scheduler-jitter-min" type="number" min="0" max="30" step="0.1"><em>～</em><input id="nt-scheduler-jitter-max" type="number" min="0" max="60" step="0.1"></div></label>
      </div>

      <details class="nt-scheduler-advanced">
        <summary>高级参数</summary>
        <div class="nt-scheduler-advanced-grid">
          <label><span>初始退避（秒）</span><input id="nt-scheduler-backoff-base" type="number" min="0.5" max="60" step="0.5"></label>
          <label><span>最大退避（秒）</span><input id="nt-scheduler-backoff-max" type="number" min="5" max="900" step="1"></label>
          <label><span>指数倍率</span><input id="nt-scheduler-backoff-multiplier" type="number" min="1.1" max="4" step="0.1"></label>
          <label><span>429 加权</span><input id="nt-scheduler-429-multiplier" type="number" min="1" max="5" step="0.1"></label>
          <label><span>成功恢复阈值</span><input id="nt-scheduler-recovery" type="number" min="1" max="100" step="1"></label>
          <label><span>代理异常暂停（秒）</span><input id="nt-scheduler-proxy-pause" type="number" min="15" max="1800" step="5"></label>
        </div>
      </details>

      <div class="nt-scheduler-form-footer nt-scheduler-form-footer-compact nt-scheduler-form-footer-textonly">
        <span id="nt-scheduler-save-status" class="muted">参数修改后立即作用于新请求。</span>
      </div>
    </form>

    <details class="nt-scheduler-runtime nt-scheduler-runtime-compact">
      <summary>租户运行状态 <span id="nt-scheduler-runtime-copy">暂无动态限流</span></summary>
      <div id="nt-scheduler-shared-warning" class="nt-scheduler-warning" hidden></div>
      <div class="table-wrap">
        <table class="nt-scheduler-table">
          <thead><tr><th>租户</th><th>代理</th><th>调度状态</th><th>退避</th><th>429</th><th>成功 / 失败</th></tr></thead>
          <tbody id="nt-scheduler-account-rows"><tr><td colspan="6">读取中……</td></tr></tbody>
        </table>
      </div>
    </details>
  `;

  const head = page.querySelector(".launch-page-head, .instances-page-head");
  if (head && head.parentNode === page) head.insertAdjacentElement("afterend", panel);
  else page.prepend(panel);

  $("nt-scheduler-refresh")?.addEventListener("click", () => ntSchedulerLoad(true));
  $("nt-scheduler-form")?.addEventListener("submit", ntSchedulerSave);
  $("nt-scheduler-reset-top")?.addEventListener("click", ntSchedulerReset);
  $("nt-scheduler-save-top")?.addEventListener("click", () => ntSchedulerSave({ preventDefault(){} }));
  return panel;
}

function ntSchedulerSetValue(id, value) {

  const element = $(id);
  if (element) element.value = value ?? "";
}

function ntSchedulerRenderSettings(payload) {
  ntSchedulerC2.settings = payload?.settings || {};
  ntSchedulerC2.defaults = payload?.defaults || {};
  const s = ntSchedulerC2.settings;

  if ($("nt-scheduler-enabled")) $("nt-scheduler-enabled").checked = Boolean(s.enabled);
  ntSchedulerSetValue("nt-scheduler-global", s.global_concurrency);
  ntSchedulerSetValue("nt-scheduler-tenant", s.tenant_concurrency);
  ntSchedulerSetValue("nt-scheduler-start-min", s.start_delay_min);
  ntSchedulerSetValue("nt-scheduler-start-max", s.start_delay_max);
  ntSchedulerSetValue("nt-scheduler-jitter-min", s.jitter_min);
  ntSchedulerSetValue("nt-scheduler-jitter-max", s.jitter_max);
  ntSchedulerSetValue("nt-scheduler-backoff-base", s.backoff_base);
  ntSchedulerSetValue("nt-scheduler-backoff-max", s.backoff_max);
  ntSchedulerSetValue("nt-scheduler-backoff-multiplier", s.backoff_multiplier);
  ntSchedulerSetValue("nt-scheduler-429-multiplier", s.rate_limit_multiplier);
  ntSchedulerSetValue("nt-scheduler-recovery", s.success_recovery_threshold);
  ntSchedulerSetValue("nt-scheduler-proxy-pause", s.proxy_pause_seconds);

  const toggleChip = document.querySelector(".nt-scheduler-toggle-chip");
  if (toggleChip) {
    toggleChip.classList.toggle("disabled", !s.enabled);
  }
}


function ntSchedulerRenderStatus(payload) {
  ntSchedulerC2.status = payload || {};
  const summary = payload?.summary || {};
  const totalAccounts = Number(summary.total_accounts ?? 0);
  const proxyBound = Number(summary.proxy_bound_accounts ?? 0);
  const directAccounts = Number(summary.direct_accounts ?? 0);
  const sharedGroups = Number(summary.shared_proxy_groups ?? 0);
  if ($("nt-scheduler-total")) $("nt-scheduler-total").textContent = totalAccounts;
  if ($("nt-scheduler-proxy")) $("nt-scheduler-proxy").textContent = `${proxyBound}/${totalAccounts || 0}`;
  if ($("nt-scheduler-429")) $("nt-scheduler-429").textContent = summary.total_429 ?? 0;
  if ($("nt-scheduler-throttled")) $("nt-scheduler-throttled").textContent = summary.throttled_tenants ?? 0;
  if ($("nt-scheduler-paused")) $("nt-scheduler-paused").textContent = summary.proxy_paused_tenants ?? 0;
  const schedulerActive = Number(summary.throttled_tenants || 0) + Number(summary.proxy_paused_tenants || 0);
  if ($("nt-scheduler-active")) $("nt-scheduler-active").textContent = schedulerActive;
  const isolation = $("nt-scheduler-isolation");
  if (isolation) {
    if (sharedGroups > 0) {
      isolation.textContent = `代理隔离 ${proxyBound}/${totalAccounts || 0} · 存在共享代理`;
      isolation.className = "warn";
    } else if (proxyBound > 0) {
      isolation.textContent = `已绑定代理 ${proxyBound}/${totalAccounts || 0}`;
      isolation.className = "good";
    } else if (directAccounts === totalAccounts) {
      isolation.textContent = "当前全部租户为直连";
      isolation.className = "muted";
    } else {
      isolation.textContent = "代理隔离正常";
      isolation.className = "good";
    }
  }

  const runtimeCopy = $("nt-scheduler-runtime-copy");
  if (runtimeCopy) {
    const active = Number(summary.throttled_tenants || 0) + Number(summary.proxy_paused_tenants || 0);
    runtimeCopy.textContent = active ? `${summary.total_accounts || 0} 个租户 · ${active} 个处于动态控制` : `${summary.total_accounts || 0} 个租户 · 全部正常`;
  }

  const warning = $("nt-scheduler-shared-warning");
  const shared = payload?.shared_proxies || [];
  if (warning) {
    warning.hidden = !shared.length;
    warning.innerHTML = shared.length
      ? `<strong>代理隔离提醒：</strong>发现 ${shared.length} 组代理被多个租户共用。要达到完整 C 方案，请在代理管理中改为 1 租户 = 1 代理。`
      : "";
  }

  const rows = $("nt-scheduler-account-rows");
  const accounts = payload?.accounts || [];
  if (!rows) return;
  if (!accounts.length) {
    rows.innerHTML = '<tr><td colspan="6">暂无 OCI 租户</td></tr>';
    return;
  }

  rows.innerHTML = accounts.map((item) => {
    const runtime = item.runtime || {};
    const remaining = Number(runtime.defer_remaining_seconds || 0);
    const reason = ntSchedulerReasonLabel(runtime.last_reason);
    const proxy = item.proxy_enabled
      ? (item.proxy_label || item.proxy_last_ip || `代理 #${item.proxy_profile_id || "—"}`)
      : "直连";
    const statusClass = runtime.last_reason === "proxy_pause"
      ? "bad"
      : (remaining > 0 ? "warn" : "good");
    return `<tr>
      <td><strong>${esc(item.custom_name || `#${item.account_id}`)}</strong><small>${esc(item.email || "")}</small></td>
      <td>${esc(proxy)}</td>
      <td><span class="status-pill ${statusClass}">${esc(reason)}</span></td>
      <td>${remaining > 0 ? `${remaining.toFixed(1)} 秒` : "—"}</td>
      <td>${Number(runtime.total_429 || 0)}</td>
      <td>${Number(runtime.total_success || 0)} / ${Number(runtime.total_failure || 0)}</td>
    </tr>`;
  }).join("");
}

async function ntSchedulerLoad(showToast = false) {
  ntSchedulerEnsurePanel();
  if (ntSchedulerC2.loading) return;
  ntSchedulerC2.loading = true;
  try {
    const [settingsPayload, statusPayload] = await Promise.all([
      api("/launch/scheduler/settings"),
      api("/launch/scheduler/status"),
    ]);
    ntSchedulerRenderSettings(settingsPayload);
    ntSchedulerRenderStatus(statusPayload);
    if (showToast) toast("智能调度状态已刷新", "good");
  } catch (error) {
    if (showToast) toast(error.message, "bad");
    const status = $("nt-scheduler-save-status");
    if (status) status.textContent = `读取失败：${error.message}`;
  } finally {
    ntSchedulerC2.loading = false;
  }
}

function ntSchedulerNumber(id) {
  const value = Number($(id)?.value);
  if (!Number.isFinite(value)) throw new Error("调度参数必须填写有效数字");
  return value;
}

async function ntSchedulerSave(event) {
  event.preventDefault();
  const button = $("nt-scheduler-save-top");
  if (button) button.disabled = true;
  try {
    const body = {
      enabled: Boolean($("nt-scheduler-enabled")?.checked),
      global_concurrency: ntSchedulerNumber("nt-scheduler-global"),
      tenant_concurrency: ntSchedulerNumber("nt-scheduler-tenant"),
      start_delay_min: ntSchedulerNumber("nt-scheduler-start-min"),
      start_delay_max: ntSchedulerNumber("nt-scheduler-start-max"),
      jitter_min: ntSchedulerNumber("nt-scheduler-jitter-min"),
      jitter_max: ntSchedulerNumber("nt-scheduler-jitter-max"),
      backoff_base: ntSchedulerNumber("nt-scheduler-backoff-base"),
      backoff_max: ntSchedulerNumber("nt-scheduler-backoff-max"),
      backoff_multiplier: ntSchedulerNumber("nt-scheduler-backoff-multiplier"),
      rate_limit_multiplier: ntSchedulerNumber("nt-scheduler-429-multiplier"),
      success_recovery_threshold: ntSchedulerNumber("nt-scheduler-recovery"),
      proxy_pause_seconds: ntSchedulerNumber("nt-scheduler-proxy-pause"),
    };
    const result = await api("/launch/scheduler/settings", { method: "PUT", body });
    ntSchedulerRenderSettings(result);
    await ntSchedulerLoad(false);
    toast("智能请求调度设置已保存并立即生效", "good");
    const status = $("nt-scheduler-save-status");
    if (status) status.textContent = "已保存到 SQLite；API 重启后仍保留。";
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

async function ntSchedulerReset() {
  if (!confirm("恢复智能调度默认参数吗？当前动态 429 / 代理暂停统计不会清除。")) return;
  const button = $("nt-scheduler-reset");
  if (button) button.disabled = true;
  try {
    const result = await api("/launch/scheduler/settings/reset", { method: "POST" });
    ntSchedulerRenderSettings(result);
    await ntSchedulerLoad(false);
    toast("智能调度已恢复默认参数", "good");
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    if (button) button.disabled = false;
  }
}

if (typeof showPage === "function") {
  const ntSchedulerOriginalShowPageC2 = showPage;
  showPage = function ntSchedulerShowPageC2(page) {
    const result = ntSchedulerOriginalShowPageC2.apply(this, arguments);
    if (page === "launch") {
      setTimeout(() => {
        ntSchedulerEnsurePanel();
        ntSchedulerLoad(false);
      }, 0);
    }
    return result;
  };
}
/* END OCI-N&T V1.0.4 1.0.4-adaptive-launch-c2 */

/* BEGIN OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 ACCOUNT HEALTH UI */
(() => {
  const FEATURE = "1.0.5-alert-health-taskcenter1";
  const healthFilter = () => document.getElementById("account-health-filter");

  function healthSummary() {
    const accounts = Array.isArray(state.accounts) ? state.accounts : [];
    const healthy = accounts.filter((item) => item.health_level === "HEALTHY").length;
    const warning = accounts.filter((item) => item.health_level === "WARNING").length;
    const critical = accounts.filter((item) => item.health_level === "CRITICAL").length;
    const scored = accounts.map((item) => Number(item.health_score)).filter(Number.isFinite);
    const average = scored.length ? Math.round((scored.reduce((a,b) => a + b, 0) / scored.length) * 10) / 10 : 0;
    if ($("api-summary-total")) $("api-summary-total").textContent = String(accounts.length);
    if ($("api-summary-alive")) $("api-summary-alive").textContent = String(healthy);
    if ($("api-summary-proxy")) $("api-summary-proxy").textContent = String(warning);
    if ($("api-summary-abnormal")) $("api-summary-abnormal").textContent = String(critical);
    if ($("dash-account-count")) $("dash-account-count").textContent = String(accounts.length);
    if ($("dash-alive-count")) $("dash-alive-count").textContent = String(healthy);
    if ($("dash-account-copy")) $("dash-account-copy").textContent = accounts.length ? `${warning} 警告 · ${critical} 异常` : "尚未导入 OCI API";
    if ($("dash-alive-copy")) $("dash-alive-copy").textContent = accounts.length ? `平均健康分 ${average}` : "检测后显示健康评分";
  }

  const originalRenderAccounts = renderAccounts;
  renderAccounts = function ntHealthRenderAccounts(...args) {
    const allAccounts = Array.isArray(state.accounts) ? state.accounts : [];
    const level = healthFilter()?.value || "";
    if (level) state.accounts = allAccounts.filter((item) => item.health_level === level);
    try {
      return originalRenderAccounts(...args);
    } finally {
      state.accounts = allAccounts;
      healthSummary();
      const box = $("account-list");
      const count = $("account-filter-count");
      if (box && count && level) {
        const rows = box.querySelectorAll("tbody tr").length;
        const cards = box.querySelectorAll(".api-card").length;
        count.textContent = `${rows || cards} / ${allAccounts.length} 个账户`;
      }
    }
  };

  if (typeof ui2LoadDashboard === "function") {
    const originalDashboard = ui2LoadDashboard;
    ui2LoadDashboard = async function ntHealthDashboard(...args) {
      try { return await originalDashboard(...args); }
      finally { healthSummary(); }
    };
  }

  if (typeof window.ui2AccountsChanged === "function") {
    const originalAccountsChanged = window.ui2AccountsChanged;
    window.ui2AccountsChanged = function ntHealthAccountsChanged(...args) {
      const result = originalAccountsChanged(...args);
      queueMicrotask(healthSummary);
      return result;
    };
  }

  function init() {
    const filter = healthFilter();
    if (filter && filter.dataset.ntHealthReady !== FEATURE) {
      filter.dataset.ntHealthReady = FEATURE;
      const saved = localStorage.getItem("oci_nt_filter_account_health") || "";
      if (["HEALTHY", "WARNING", "CRITICAL"].includes(saved)) filter.value = saved;
      filter.addEventListener("change", () => {
        localStorage.setItem("oci_nt_filter_account_health", filter.value || "");
        renderAccounts();
      });
      $("account-filter-reset")?.addEventListener("click", () => {
        filter.value = "";
        localStorage.removeItem("oci_nt_filter_account_health");
        queueMicrotask(() => renderAccounts());
      });
    }
    healthSummary();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once:true});
  else queueMicrotask(init);
})();
/* END OCI-N&T V1.0.4 1.0.5-alert-health-taskcenter1 ACCOUNT HEALTH UI */
