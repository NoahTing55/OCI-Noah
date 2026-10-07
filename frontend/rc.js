"use strict";

// OCI-N&T v7 RC tenant workspaces. Rendering a workspace never queries OCI.
// OCI is queried only after the user clicks an explicit action button.
state.rc = {
  network: null,
  security: null,
  bootVolumes: [],
  images: [],
  vncSessions: [],
  storage: null,
  objects: null,
  iam: null,
  regions: null,
  limits: null,
  limitPage: 1,
  limitPageSize: 100,
  limitFilter: "",
  launchProfiles: [],
};

function rcAccount() {
  return selectedTenant();
}

function rcRegion(instance = null) {
  const account = rcAccount();
  return instance?.region || account?.home_region_name || account?.region || account?.home_region_key || "";
}

function rcInstanceById(id) {
  return state.instances.find((item) => String(item.id) === String(id)) || null;
}

function rcInstanceOptions(selected = "") {
  if (!state.instances.length) return '<option value="">请先在实例页手动同步</option>';
  return state.instances.map((item) => `<option value="${esc(item.id)}" ${String(item.id) === String(selected) ? "selected" : ""}>${esc(item.display_name || "N&T")} · ${esc(item.lifecycle_state || "UNKNOWN")}</option>`).join("");
}

function rcCompactId(value) {
  const text = String(value || "—");
  if (text.length <= 34) return esc(text);
  return `${esc(text.slice(0, 18))}…${esc(text.slice(-10))}`;
}

function rcJson(value) {
  return esc(JSON.stringify(value ?? {}, null, 2));
}

function rcQuery(params) {
  return new URLSearchParams(Object.entries(params).filter(([, value]) => value !== null && value !== undefined && value !== "")).toString();
}

async function rcApiBlob(path) {
  const headers = new Headers();
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(`${API}${path}`, { headers });
  if (!response.ok) {
    let detail = "下载失败";
    try { detail = (await response.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return { blob: await response.blob(), filename: response.headers.get("content-disposition") || "" };
}

function rcDownloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "download";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function rcWorkspaceHeader(title, copy, actions = "") {
  return `<div class="tenant-toolbar"><div><h2>${esc(title)}</h2><p>${esc(copy)}</p></div><div class="inline-actions">${actions}</div></div>`;
}

window.rcRenderTenantTab = function rcRenderTenantTab(tab) {
  if (tab === "network") renderNetworkWorkspace();
  else if (tab === "security") renderSecurityWorkspace();
  else if (tab === "volumes") renderVolumesWorkspace();
  else if (tab === "vnc") renderVncWorkspace();
  else if (tab === "storage") renderStorageWorkspace();
  else if (tab === "insights") renderInsightsWorkspace();
  else if (tab === "tenancy") renderTenancyWorkspace();
};

/* ------------------------------ Network ------------------------------ */

function renderNetworkWorkspace() {
  const selected = state.rc.network?.instance_id || state.instances[0]?.id || "";
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("IP 与 VNIC", "默认读取本地实例缓存；点击读取网络后才访问当前租户 OCI。")}
    <section class="panel rc-control-grid">
      <label><span>实例</span><select id="rc-network-instance">${rcInstanceOptions(selected)}</select></label>
      <div class="inline-actions rc-actions-bottom">
        <button id="rc-network-load" class="button primary" type="button">读取当前实例 VNIC</button>
        <button id="rc-network-history" class="button" type="button">IP 历史</button>
        <button id="rc-network-forward-on" class="button" type="button">启用 VNIC 转发</button>
        <button id="rc-network-forward-off" class="button" type="button">关闭 VNIC 转发</button>
        <button id="rc-network-delete-all" class="button danger" type="button">删除全部附属 VNIC</button>
        <button id="rc-network-recover" class="button danger" type="button">恢复实例网络</button>
      </div>
    </section>
    <section class="panel rc-section">
      <div class="panel-head"><div><h2>附属 VNIC</h2><p>可一次创建 1–8 个附属 VNIC。</p></div></div>
      <form id="rc-vnic-create" class="form-grid four-col">
        <label><span>子网 OCID</span><input id="rc-vnic-subnet" placeholder="读取网络后自动填充" required></label>
        <label><span>数量</span><input id="rc-vnic-count" type="number" min="1" max="8" value="1" required></label>
        <label><span>每个 VNIC 的 IPv6</span><input id="rc-vnic-ipv6" type="number" min="0" max="8" value="0"></label>
        <label><span>名称</span><input id="rc-vnic-name" value="N&amp;T-VNIC" required></label>
        <label class="toggle"><span>分配公网 IPv4</span><input id="rc-vnic-public" type="checkbox"></label>
        <label class="toggle"><span>跳过源/目标检查</span><input id="rc-vnic-skip" type="checkbox"></label>
        <button class="button" type="submit">创建附属 VNIC</button>
      </form>
    </section>
    <div id="rc-network-result" class="rc-stack">${state.rc.network ? renderNetworkInventory(state.rc.network) : '<section class="panel"><div class="empty">尚未读取 VNIC。</div></section>'}</div>
    <section id="rc-ip-history-result" class="panel rc-section" hidden></section>`;

  $("rc-network-load").addEventListener("click", loadNetworkInventory);
  $("rc-network-history").addEventListener("click", loadIpHistory);
  $("rc-network-forward-on").addEventListener("click", () => configureForwardingRc(true));
  $("rc-network-forward-off").addEventListener("click", () => configureForwardingRc(false));
  $("rc-network-delete-all").addEventListener("click", deleteAllSecondaryVnicsRc);
  $("rc-network-recover").addEventListener("click", recoverNetwork);
  $("rc-vnic-create").addEventListener("submit", createSecondaryVnics);
  bindNetworkActions();
}

async function loadNetworkInventory() {
  const button = $("rc-network-load");
  const instanceSelect = $("rc-network-instance");
  const account = rcAccount();

  if (!instanceSelect || !account) return;

  const instance = rcInstanceById(instanceSelect.value);
  const requestedTenantId = Number(account.id);

  if (!instance) return toast("请先同步并选择实例", "bad");
  if (!instance.compartment_id) return toast("实例缓存缺少 Compartment，请重新同步实例", "bad");
  setBusy(button, true, "读取中……");
  try {
    const query = rcQuery({ region: rcRegion(instance), compartment_id: instance.compartment_id, refresh: true });
    const result = await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/network?${query}`);

    if (
      Number(state.tenantId) !== requestedTenantId ||
      state.tenantTab !== "network"
    ) {
      return;
    }

    const subnetInput = $("rc-vnic-subnet");
    const resultBox = $("rc-network-result");

    if (!subnetInput || !resultBox) return;

    state.rc.network = {
      ...result,
      instance_id: instance.id,
      region: rcRegion(instance),
      compartment_id: instance.compartment_id
    };

    const primary =
      result.items?.find(
        (item) => item.is_primary === true || item.attachment?.is_primary === true
      ) ||
      result.items?.[0];

    if (primary?.subnet?.id) subnetInput.value = primary.subnet.id;

    resultBox.innerHTML = renderNetworkInventory(state.rc.network);
    bindNetworkActions();
    toast(`已读取 ${result.items?.length || 0} 个 VNIC`, "good");
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

function renderNetworkInventory(result) {
  const items = result.items || [];
  if (!items.length) return '<section class="panel"><div class="empty">没有 VNIC 数据。</div></section>';
  const explicitPrimaryIndex = items.findIndex(
    (item) =>
      item.is_primary === true ||
      item.vnic?.is_primary === true ||
      item.attachment?.is_primary === true
  );

  const primaryIndex = explicitPrimaryIndex >= 0 ? explicitPrimaryIndex : 0;

  return items.map((item, index) => {
    const isPrimary = index === primaryIndex;
    const vnic = item.vnic || {};
    const attachment = item.attachment || {};
    const privateIps = item.private_ips || [];
    const ipv6s = item.ipv6s || [];
    const primaryPrivate = privateIps.find((ip) => ip.is_primary) || privateIps[0] || {};
    const publicIp = primaryPrivate.public_ip || {};
    return `<article class="panel rc-resource-card">
      <div class="rc-resource-head"><div><h3>${esc(vnic.display_name || (isPrimary ? "主 VNIC" : "附属 VNIC"))}</h3><p>${isPrimary ? "主网卡" : "附属网卡"} · ${rcCompactId(vnic.id)}</p></div><span class="badge ${isPrimary ? "good" : "muted"}">${isPrimary ? "PRIMARY" : esc(attachment.lifecycle_state || "ATTACHED")}</span></div>
      <div class="rc-kv-grid">
        <div><span>私网 IP</span><strong>${esc(primaryPrivate.ip_address || "—")}</strong></div>
        <div><span>公网 IP</span><strong>${esc(publicIp.ip_address || "无")}</strong></div>
        <div><span>子网</span><strong>${esc(item.subnet?.display_name || "—")}</strong><small>${esc(item.subnet?.cidr_block || "")}</small></div>
        <div><span>IPv6</span><strong>${ipv6s.length}</strong></div>
        <div><span>源/目标检查</span><strong>${vnic.skip_source_dest_check ? "已跳过" : "已启用"}</strong></div>
        <div><span>MAC</span><strong>${esc(vnic.mac_address || "—")}</strong></div>
      </div>
      <div class="inline-actions">
        <button class="button" data-rc-vnic-edit="${esc(vnic.id)}" type="button">修改 VNIC</button>
        <button class="button" data-rc-ipv6-add="${esc(vnic.id)}" type="button">添加 IPv6</button>
        ${primaryPrivate.id ? `<button class="button" data-rc-cidr="${esc(primaryPrivate.id)}" data-public-ip="${esc(publicIp.ip_address || "")}" type="button">指定网段换 IP</button>` : ""}
        ${primaryPrivate.id && publicIp.ip_address ? `<button class="button" data-rc-quality="${esc(primaryPrivate.id)}" data-public-ip="${esc(publicIp.ip_address)}" type="button">IP 质量</button><button class="button" data-rc-quality-rotate="${esc(primaryPrivate.id)}" type="button">优选换 IP</button>` : ""}
        ${!isPrimary && attachment.id ? `<button class="button danger" data-rc-vnic-delete="${esc(attachment.id)}" type="button">删除 VNIC</button>` : ""}
      </div>
      ${ipv6s.length ? `<div class="rc-chip-list">${ipv6s.map((ip) => `<span>${esc(ip.ip_address || "IPv6")}<button data-rc-ipv6-delete="${esc(ip.id)}" type="button" title="删除">×</button></span>`).join("")}</div>` : ""}
    </article>`;
  }).join("");
}

function bindNetworkActions() {
  document.querySelectorAll("[data-rc-vnic-edit]").forEach((button) => button.addEventListener("click", () => editVnic(button)));
  document.querySelectorAll("[data-rc-ipv6-add]").forEach((button) => button.addEventListener("click", () => addIpv6(button)));
  document.querySelectorAll("[data-rc-ipv6-delete]").forEach((button) => button.addEventListener("click", () => deleteIpv6(button)));
  document.querySelectorAll("[data-rc-vnic-delete]").forEach((button) => button.addEventListener("click", () => deleteVnic(button)));
  document.querySelectorAll("[data-rc-cidr]").forEach((button) => button.addEventListener("click", () => rotateCidr(button)));
  document.querySelectorAll("[data-rc-quality]").forEach((button) => button.addEventListener("click", () => checkIpQuality(button)));
  document.querySelectorAll("[data-rc-quality-rotate]").forEach((button) => button.addEventListener("click", () => qualityRotate(button)));
}

async function createSecondaryVnics(event) {
  event.preventDefault();
  const account = rcAccount();
  const instance = rcInstanceById($("rc-network-instance").value);
  if (!account || !instance) return;
  const submit = event.submitter;
  setBusy(submit, true, "创建中……");
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/vnics`, { method: "POST", body: {
      region: rcRegion(instance), compartment_id: instance.compartment_id, subnet_id: $("rc-vnic-subnet").value.trim(),
      count: Number($("rc-vnic-count").value), display_name: $("rc-vnic-name").value.trim(),
      assign_public_ip: $("rc-vnic-public").checked, ipv6_count: Number($("rc-vnic-ipv6").value),
      skip_source_dest_check: $("rc-vnic-skip").checked,
    }});
    toast("附属 VNIC 创建请求已提交", "good");
    await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(submit, false); }
}

async function editVnic(button) {
  const account = rcAccount();
  const name = prompt("VNIC 名称（留空不修改）", "");
  if (name === null || !account) return;
  const hostname = prompt("主机标签（留空不修改）", "");
  if (hostname === null) return;
  const skip = confirm("确定：跳过源/目标检查；取消：启用源/目标检查。");
  try {
    await api(`/accounts/${account.id}/vnics/${encodeURIComponent(button.dataset.rcVnicEdit)}`, { method: "PUT", body: {
      region: state.rc.network.region, display_name: name.trim() || null, hostname_label: hostname.trim() || null,
      skip_source_dest_check: skip,
    }});
    await loadNetworkInventory(); toast("VNIC 已更新", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function addIpv6(button) {
  const count = Number(prompt("添加 IPv6 数量（1–8）", "1"));
  const account = rcAccount();
  if (!account || !Number.isInteger(count) || count < 1 || count > 8) return;
  try {
    await api(`/accounts/${account.id}/vnics/${encodeURIComponent(button.dataset.rcIpv6Add)}/ipv6`, { method: "POST", body: { region: state.rc.network.region, count } });
    await loadNetworkInventory(); toast("IPv6 已添加", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteIpv6(button) {
  const account = rcAccount();
  if (!account || !confirm("确定删除该 IPv6 地址吗？")) return;
  try {
    await api(`/accounts/${account.id}/ipv6/${encodeURIComponent(button.dataset.rcIpv6Delete)}?${rcQuery({ region: state.rc.network.region })}`, { method: "DELETE" });
    await loadNetworkInventory(); toast("IPv6 已删除");
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteVnic(button) {
  const account = rcAccount();
  if (!account || prompt("请输入 DELETE VNIC 确认删除附属 VNIC：") !== "DELETE VNIC") return;
  try {
    const query = rcQuery({ region: state.rc.network.region, confirmation: "DELETE VNIC" });
    await api(`/accounts/${account.id}/vnic-attachments/${encodeURIComponent(button.dataset.rcVnicDelete)}?${query}`, { method: "DELETE" });
    await loadNetworkInventory(); toast("附属 VNIC 已删除");
  } catch (error) { toast(error.message, "bad"); }
}

async function rotateCidr(button) {
  const cidr = prompt("目标 OCI IPv4 CIDR，例如 129.146.0.0/16：", "");
  if (!cidr) return;
  const max = Number(prompt("最大更换次数（1–100）", "20"));
  const account = rcAccount();
  if (!account) return;
  try {
    const result = await api(`/accounts/${account.id}/private-ips/${encodeURIComponent(button.dataset.rcCidr)}/cidr-rotate`, { method: "POST", body: {
      region: state.rc.network.region, instance_id: state.rc.network.instance_id, cidr, max_rotations: max,
    }});
    toast(result.matched ? `已获得 ${result.new_ip}` : `未匹配，当前 IP ${result.new_ip || "未知"}`, result.matched ? "good" : "bad");
    await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
}

async function checkIpQuality(button) {
  const account = rcAccount();
  if (!account || !button.dataset.publicIp) return;
  try {
    const result = await api(`/accounts/${account.id}/ip-quality/check`, { method: "POST", body: {
      region: state.rc.network.region, instance_id: state.rc.network.instance_id,
      private_ip_id: button.dataset.rcQuality, public_ip: button.dataset.publicIp,
    }});
    alert(`IP：${result.ip}\n评分：${result.score}\n国家/地区：${result.country || "—"}\nASN：${result.asn || "—"}\n风险：${result.risk || "—"}`);
  } catch (error) { toast(error.message, "bad"); }
}

async function qualityRotate(button) {
  const cidr = prompt("目标 OCI IPv4 CIDR：", "");
  if (!cidr) return;
  const minimum = Number(prompt("最低质量分（0–100）", "80"));
  const max = Number(prompt("最大更换次数（1–100）", "10"));
  const account = rcAccount();
  if (!account) return;
  try {
    const result = await api(`/accounts/${account.id}/private-ips/${encodeURIComponent(button.dataset.rcQualityRotate)}/quality-rotate`, { method: "POST", body: {
      region: state.rc.network.region, instance_id: state.rc.network.instance_id, cidr,
      max_rotations: max, minimum_score: minimum,
    }});
    toast(result.matched ? "已获得符合网段和质量要求的 IP" : "未找到符合条件的 IP", result.matched ? "good" : "bad");
    await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
}

async function loadIpHistory() {
  const account = rcAccount();
  const instance = rcInstanceById($("rc-network-instance").value);
  if (!account) return;
  try {
    const rows = await api(`/accounts/${account.id}/ip-quality/history?${rcQuery({ instance_id: instance?.id, limit: 200 })}`);
    const box = $("rc-ip-history-result");
    box.hidden = false;
    box.innerHTML = `<div class="panel-head"><div><h2>IP 历史</h2><p>本地记录，不请求 OCI。</p></div></div>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>IP</th><th>动作</th><th>评分</th><th>区域</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${fmtDate(row.created_at)}</td><td>${esc(row.public_ip || "—")}</td><td>${esc(row.action)}</td><td>${row.score ?? "—"}</td><td>${esc(row.region || "—")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无记录</div>'}`;
  } catch (error) { toast(error.message, "bad"); }
}


async function configureForwardingRc(enabled) {
  const account = rcAccount();
  const ids = (state.rc.network?.items || []).map((item) => item.vnic?.id).filter(Boolean);
  if (!account || !ids.length) return toast("请先读取 VNIC", "bad");
  if (!confirm(`${enabled ? "启用" : "关闭"}当前实例全部 VNIC 的转发设置？`)) return;
  try {
    await api(`/accounts/${account.id}/vnics/forwarding`, { method: "POST", body: { region: state.rc.network.region, vnic_ids: ids, enabled } });
    toast(enabled ? "VNIC 转发已启用" : "VNIC 转发已关闭", "good");
    await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteAllSecondaryVnicsRc() {
  const account = rcAccount();
  const ids = (state.rc.network?.items || []).filter((item) => !item.is_primary).map((item) => item.attachment?.id).filter(Boolean);
  if (!account || !ids.length) return toast("当前实例没有附属 VNIC", "bad");
  if (prompt(`将删除 ${ids.length} 个附属 VNIC。请输入 DELETE ALL VNICS：`) !== "DELETE ALL VNICS") return;
  try {
    const result = await api(`/accounts/${account.id}/vnic-attachments/bulk-delete`, { method: "POST", body: { region: state.rc.network.region, attachment_ids: ids, confirmation: "DELETE ALL VNICS" } });
    toast(result.errors?.length ? `已删除 ${result.detached.length} 个，${result.errors.length} 个失败` : `已删除 ${result.detached.length} 个附属 VNIC`, result.errors?.length ? "bad" : "good");
    await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
}

async function recoverNetwork() {
  const account = rcAccount();
  const instance = rcInstanceById($("rc-network-instance").value);
  if (!account || !instance || !confirm("网络恢复会修改 VNIC 源/目标检查设置，确定继续吗？")) return;
  try {
    await api(`/accounts/${account.id}/instances/${encodeURIComponent(instance.id)}/network/recover`, { method: "POST", body: { region: rcRegion(instance), compartment_id: instance.compartment_id } });
    toast("网络恢复操作完成", "good"); await loadNetworkInventory();
  } catch (error) { toast(error.message, "bad"); }
}

/* ------------------------------ Security ------------------------------ */

function knownSecurityLists() {
  const ids = new Set();
  (state.rc.network?.items || []).forEach((item) => {
    (item.subnet?.security_list_ids || []).forEach((id) => ids.add(id));
  });
  return [...ids];
}

function securityProtocolLabel(protocol) {
  const value = String(protocol ?? "all").toLowerCase();
  return ({
    "6": "TCP",
    "17": "UDP",
    "1": "ICMP",
    "58": "ICMPv6",
    "all": "全部",
  })[value] || value.toUpperCase();
}

function securityRuleAddress(rule, direction) {
  return direction === "ingress"
    ? String(rule.source || "—")
    : String(rule.destination || "—");
}

function securityRulePorts(rule) {
  const protocol = String(rule.protocol ?? "all").toLowerCase();

  if (protocol === "6") {
    const range = rule.tcp_options?.destination_port_range;
    if (!range) return "全部";
    return Number(range.min) === Number(range.max)
      ? String(range.min)
      : `${range.min}–${range.max}`;
  }

  if (protocol === "17") {
    const range = rule.udp_options?.destination_port_range;
    if (!range) return "全部";
    return Number(range.min) === Number(range.max)
      ? String(range.min)
      : `${range.min}–${range.max}`;
  }

  if (protocol === "1" || protocol === "58") {
    const options = rule.icmp_options;
    if (!options) return "全部";
    const type = options.type ?? "全部";
    const code = options.code;
    return code === null || code === undefined
      ? `类型 ${type}`
      : `类型 ${type} / 代码 ${code}`;
  }

  return "—";
}

function securityRuleTypeText(rule) {
  return rule.is_stateless ? "无状态" : "有状态";
}

function renderSecurityRules(direction) {
  const result = state.rc.security || {};
  const rules = direction === "ingress"
    ? (result.ingress_security_rules || [])
    : (result.egress_security_rules || []);

  const addressTitle = direction === "ingress" ? "源地址" : "目标地址";
  const typeText = direction === "ingress" ? "入站" : "出站";

  if (!rules.length) {
    return `<div class="empty security-empty">暂无${typeText}规则</div>`;
  }

  return `<div class="table-wrap security-rule-table-wrap">
    <table class="security-rule-table">
      <thead>
        <tr>
          <th class="security-sequence">序号</th>
          <th>规则类型</th>
          <th>协议</th>
          <th>${addressTitle}</th>
          <th>端口范围</th>
          <th>状态</th>
          <th>说明</th>
          <th class="security-actions-column">操作</th>
        </tr>
      </thead>
      <tbody>
        ${rules.map((rule, index) => `
          <tr>
            <td class="security-sequence">${index + 1}</td>
            <td><span class="badge ${direction === "ingress" ? "good" : "muted"}">${typeText}</span></td>
            <td><strong>${esc(securityProtocolLabel(rule.protocol))}</strong></td>
            <td class="mono">${esc(securityRuleAddress(rule, direction))}</td>
            <td>${esc(securityRulePorts(rule))}</td>
            <td>${esc(securityRuleTypeText(rule))}</td>
            <td>${esc(rule.description || "—")}</td>
            <td>
              <div class="inline-actions compact security-row-actions">
                <button
                  class="button small"
                  data-security-edit="${index}"
                  data-security-direction="${direction}"
                  type="button"
                >编辑</button>
                <button
                  class="button small danger"
                  data-security-delete="${index}"
                  data-security-direction="${direction}"
                  type="button"
                >删除</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  </div>`;
}

function renderSecurityRulePanel() {
  const host = $("rc-security-rules");
  if (!host) return;

  const direction = state.rc.securityDirection || "ingress";

  host.innerHTML = `
    <div class="security-tabs">
      <button
        class="${direction === "ingress" ? "active" : ""}"
        data-security-tab="ingress"
        type="button"
      >入站规则</button>
      <button
        class="${direction === "egress" ? "active" : ""}"
        data-security-tab="egress"
        type="button"
      >出站规则</button>
    </div>

    <div class="security-rule-toolbar">
      <div>
        <strong>${direction === "ingress" ? "入站规则" : "出站规则"}</strong>
        <small>
          共 ${
            direction === "ingress"
              ? (state.rc.security?.ingress_security_rules || []).length
              : (state.rc.security?.egress_security_rules || []).length
          } 条
        </small>
      </div>
      <button
        id="rc-security-add"
        class="button primary"
        type="button"
      >＋ 添加规则</button>
    </div>

    ${renderSecurityRules(direction)}
  `;

  host.querySelectorAll("[data-security-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.rc.securityDirection = button.dataset.securityTab;
      renderSecurityRulePanel();
    });
  });

  $("rc-security-add")?.addEventListener(
    "click",
    () => editSecurityRule(direction, null),
  );

  host.querySelectorAll("[data-security-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      editSecurityRule(
        button.dataset.securityDirection,
        Number(button.dataset.securityEdit),
      );
    });
  });

  host.querySelectorAll("[data-security-delete]").forEach((button) => {
    button.addEventListener("click", () => {
      deleteSecurityRule(
        button.dataset.securityDirection,
        Number(button.dataset.securityDelete),
      );
    });
  });
}

function renderSecurityWorkspace() {
  state.rc.securityDirection = state.rc.securityDirection || "ingress";

  $("tenant-content").innerHTML = `
    ${rcWorkspaceHeader(
      "安全规则",
      "进入页面后自动读取当前实例关联的 OCI 安全列表。",
      '<button id="rc-security-refresh" class="button" type="button">重新读取</button>',
    )}

    <section class="panel security-summary-panel">
      <div class="security-summary-grid">
        <label>
          <span>区域</span>
          <input
            id="rc-security-region"
            value="${esc(state.rc.network?.region || rcRegion())}"
            readonly
          >
        </label>

        <label>
          <span>安全列表</span>
          <select id="rc-security-id">
            <option value="">正在自动读取……</option>
          </select>
        </label>

        <label>
          <span>显示名称</span>
          <input id="rc-security-name" readonly value="">
        </label>
      </div>

      <div class="security-summary-actions">
        <button
          id="rc-security-save"
          class="button primary"
          type="button"
          disabled
        >保存修改</button>

        <button
          id="rc-security-open-all"
          class="button danger"
          type="button"
          disabled
        >开放全部协议</button>
      </div>
    </section>

    <section id="rc-security-rules" class="panel rc-section">
      <div class="empty">正在读取安全规则……</div>
    </section>
  `;

  $("rc-security-refresh").addEventListener(
    "click",
    () => autoLoadSecurityWorkspace(true),
  );
  $("rc-security-save").addEventListener("click", saveSecurityList);
  $("rc-security-open-all").addEventListener("click", openAllSecurity);

  $("rc-security-id").addEventListener("change", loadSecurityList);

  queueMicrotask(() => autoLoadSecurityWorkspace(false));
}

async function autoLoadSecurityWorkspace(forceRefresh = false) {
  const account = rcAccount();
  const firstInstance = state.instances?.[0] || null;
  const host = $("rc-security-rules");

  if (!account) return;

  try {
    let ids = knownSecurityLists();

    if (!ids.length) {
      if (!firstInstance) {
        throw new Error("没有可用实例，请先在实例页面同步实例");
      }
      if (!firstInstance.compartment_id) {
        throw new Error("实例缓存缺少资源区间，请重新同步实例");
      }

      if (host) {
        host.innerHTML = '<div class="empty">正在读取实例网络和安全列表……</div>';
      }

      const query = rcQuery({
        region: rcRegion(firstInstance),
        compartment_id: firstInstance.compartment_id,
        refresh: forceRefresh || true,
      });

      const network = await api(
        `/accounts/${account.id}/instances/${encodeURIComponent(firstInstance.id)}/network?${query}`,
      );

      state.rc.network = {
        ...network,
        instance_id: firstInstance.id,
        region: rcRegion(firstInstance),
        compartment_id: firstInstance.compartment_id,
      };

      ids = knownSecurityLists();
    }

    if (!ids.length) {
      throw new Error("当前实例子网没有关联安全列表");
    }

    const select = $("rc-security-id");
    select.innerHTML = ids.map((id, index) => `
      <option value="${esc(id)}">
        安全列表 ${index + 1} · ${esc(rcCompactId(id).replace(/<[^>]*>/g, ""))}
      </option>
    `).join("");

    await loadSecurityList();
  } catch (error) {
    if (host) {
      host.innerHTML = `
        <div class="empty security-empty">
          <strong>安全规则读取失败</strong>
          <span>${esc(error.message)}</span>
        </div>
      `;
    }
    toast(error.message, "bad");
  }
}

async function loadSecurityList() {
  const account = rcAccount();
  const id = $("rc-security-id")?.value.trim();
  const region = $("rc-security-region")?.value.trim();

  if (!account || !id) return;

  const host = $("rc-security-rules");
  if (host) {
    host.innerHTML = '<div class="empty">正在读取安全规则……</div>';
  }

  try {
    const result = await api(
      `/accounts/${account.id}/security-lists/${encodeURIComponent(id)}?${rcQuery({ region })}`,
    );

    state.rc.security = {
      ...result,
      ingress_security_rules: Array.isArray(result.ingress_security_rules)
        ? result.ingress_security_rules
        : [],
      egress_security_rules: Array.isArray(result.egress_security_rules)
        ? result.egress_security_rules
        : [],
    };

    $("rc-security-name").value = result.display_name || "默认安全列表";
    $("rc-security-save").disabled = false;
    $("rc-security-open-all").disabled = false;

    renderSecurityRulePanel();
  } catch (error) {
    if (host) {
      host.innerHTML = `
        <div class="empty security-empty">
          <strong>读取失败</strong>
          <span>${esc(error.message)}</span>
        </div>
      `;
    }
    toast(error.message, "bad");
  }
}

async function editSecurityRule(direction, index) {
  if (!state.rc.security) return;

  const key = direction === "ingress"
    ? "ingress_security_rules"
    : "egress_security_rules";

  const rules = state.rc.security[key] || [];
  const existing = index === null ? null : rules[index];
  const protocol = String(existing?.protocol ?? "6");

  const portRange =
    existing?.tcp_options?.destination_port_range ||
    existing?.udp_options?.destination_port_range ||
    null;

  const values = await openUtilityDialog({
    title: existing
      ? `编辑${direction === "ingress" ? "入站" : "出站"}规则`
      : `添加${direction === "ingress" ? "入站" : "出站"}规则`,
    fields: [
      {
        name: "protocol",
        label: "协议",
        type: "select",
        value: protocol,
        options: [
          { value: "6", label: "TCP" },
          { value: "17", label: "UDP" },
          { value: "1", label: "ICMP" },
          { value: "58", label: "ICMPv6" },
          { value: "all", label: "全部协议" },
        ],
      },
      {
        name: "address",
        label: direction === "ingress" ? "源地址" : "目标地址",
        value: securityRuleAddress(existing || {}, direction) === "—"
          ? (direction === "ingress" ? "0.0.0.0/0" : "0.0.0.0/0")
          : securityRuleAddress(existing, direction),
        required: true,
      },
      {
        name: "port_min",
        label: "起始端口",
        type: "number",
        value: portRange?.min ?? "",
        min: 0,
        max: 65535,
      },
      {
        name: "port_max",
        label: "结束端口",
        type: "number",
        value: portRange?.max ?? "",
        min: 0,
        max: 65535,
      },
      {
        name: "stateless",
        label: "无状态规则",
        type: "checkbox",
        value: Boolean(existing?.is_stateless),
      },
      {
        name: "description",
        label: "说明",
        value: existing?.description || "",
        full: true,
      },
    ],
    submitText: existing ? "保存规则" : "添加规则",
  });

  if (!values) return;

  const newRule = existing
    ? structuredClone(existing)
    : {
        is_stateless: false,
        protocol: "6",
        description: null,
      };

  newRule.protocol = String(values.protocol);
  newRule.is_stateless = Boolean(values.stateless);
  newRule.description = String(values.description || "").trim() || null;

  if (direction === "ingress") {
    newRule.source = String(values.address).trim();
    newRule.source_type = "CIDR_BLOCK";
    delete newRule.destination;
    delete newRule.destination_type;
  } else {
    newRule.destination = String(values.address).trim();
    newRule.destination_type = "CIDR_BLOCK";
    delete newRule.source;
    delete newRule.source_type;
  }

  delete newRule.tcp_options;
  delete newRule.udp_options;
  delete newRule.icmp_options;

  const min = values.port_min === "" || values.port_min === null
    ? null
    : Number(values.port_min);
  const max = values.port_max === "" || values.port_max === null
    ? min
    : Number(values.port_max);

  if (newRule.protocol === "6" && min !== null) {
    newRule.tcp_options = {
      destination_port_range: {
        min,
        max: max ?? min,
      },
      source_port_range: null,
    };
  }

  if (newRule.protocol === "17" && min !== null) {
    newRule.udp_options = {
      destination_port_range: {
        min,
        max: max ?? min,
      },
      source_port_range: null,
    };
  }

  if (index === null) rules.push(newRule);
  else rules[index] = newRule;

  state.rc.security[key] = rules;
  renderSecurityRulePanel();
  toast("规则已修改，请点击“保存修改”提交到 OCI", "good");
}

function deleteSecurityRule(direction, index) {
  if (!state.rc.security) return;

  const key = direction === "ingress"
    ? "ingress_security_rules"
    : "egress_security_rules";

  const rules = state.rc.security[key] || [];
  const rule = rules[index];

  if (!rule) return;

  const message =
    `确定删除这条${direction === "ingress" ? "入站" : "出站"}规则？\n` +
    `${securityProtocolLabel(rule.protocol)} · ` +
    `${securityRuleAddress(rule, direction)} · ` +
    `${securityRulePorts(rule)}`;

  if (!confirm(message)) return;

  rules.splice(index, 1);
  state.rc.security[key] = rules;
  renderSecurityRulePanel();
  toast("规则已删除，请点击“保存修改”提交到 OCI", "good");
}

async function saveSecurityList() {
  const account = rcAccount();
  const security = state.rc.security;
  const button = $("rc-security-save");

  if (!account || !security) return;

  setBusy(button, true, "保存中……");

  try {
    await api(
      `/accounts/${account.id}/security-lists/${encodeURIComponent($("rc-security-id").value.trim())}`,
      {
        method: "PUT",
        body: {
          region: $("rc-security-region").value.trim(),
          display_name: security.display_name || null,
          ingress_rules: security.ingress_security_rules || [],
          egress_rules: security.egress_security_rules || [],
        },
      },
    );

    toast("安全规则已保存", "good");
    await loadSecurityList();
  } catch (error) {
    toast(error.message, "bad");
  } finally {
    setBusy(button, false);
  }
}

async function openAllSecurity() {
  const account = rcAccount();
  const id = $("rc-security-id")?.value.trim();
  const region = $("rc-security-region")?.value.trim();

  if (!account || !id) return;

  const confirmed = await openConfirmDialog({
    title: "开放全部协议",
    message:
      "这会允许所有 IPv4 入站和出站流量，存在较高安全风险。确定继续吗？",
    submitText: "确认开放",
    danger: true,
  });

  if (!confirmed) return;

  try {
    await api(
      `/accounts/${account.id}/security-lists/${encodeURIComponent(id)}/open-all?${rcQuery({
        region,
        confirmation: "OPEN ALL",
      })}`,
      { method: "POST" },
    );

    toast("已开放全部协议", "good");
    await loadSecurityList();
  } catch (error) {
    toast(error.message, "bad");
  }
}

/* -------------------------- Volumes / Images -------------------------- */

function renderVolumesWorkspace() {
  const instance = state.instances[0] || null;
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("引导卷与镜像", "只有点击读取、更新、备份、删除或救援时访问 OCI。")}
    <section class="panel rc-control-grid">
      <label><span>实例</span><select id="rc-volume-instance">${rcInstanceOptions(instance?.id || "")}</select></label>
      <label><span>区域</span><input id="rc-volume-region" value="${esc(rcRegion(instance))}"></label>
      <label><span>Compartment OCID</span><input id="rc-volume-compartment" value="${esc(instance?.compartment_id || "")}"></label>
      <div class="inline-actions rc-actions-bottom"><button id="rc-volumes-load" class="button primary" type="button">读取引导卷</button><button id="rc-images-load" class="button" type="button">读取自定义镜像</button><button id="rc-image-create" class="button" type="button">备份实例镜像</button><button id="rc-rescue" class="button danger" type="button">进入救援模式</button></div>
    </section>
    <div class="rc-two-columns"><section class="panel"><div class="panel-head"><div><h2>引导卷</h2><p id="rc-volume-copy">尚未读取</p></div></div><div id="rc-volumes-result" class="rc-stack"><div class="empty">尚未读取</div></div></section><section class="panel"><div class="panel-head"><div><h2>自定义镜像</h2><p id="rc-image-copy">尚未读取</p></div></div><div id="rc-images-result" class="rc-stack"><div class="empty">尚未读取</div></div></section></div>
    <section id="rc-backups-panel" class="panel rc-section" hidden><div class="panel-head"><div><h2>引导卷备份</h2><p>当前选择引导卷的备份列表。</p></div></div><div id="rc-backups-result"></div></section>`;
  $("rc-volume-instance").addEventListener("change", () => {
    const selected = rcInstanceById($("rc-volume-instance").value);
    $("rc-volume-region").value = rcRegion(selected);
    $("rc-volume-compartment").value = selected?.compartment_id || "";
  });
  $("rc-volumes-load").addEventListener("click", loadBootVolumes);
  $("rc-images-load").addEventListener("click", loadCustomImages);
  $("rc-image-create").addEventListener("click", createInstanceImage);
  $("rc-rescue").addEventListener("click", startRescueMode);
}

async function loadBootVolumes() {
  const account = rcAccount();
  const query = rcQuery({ region: $("rc-volume-region").value.trim(), compartment_id: $("rc-volume-compartment").value.trim(), refresh: true });
  try {
    const result = await api(`/accounts/${account.id}/boot-volumes?${query}`);
    state.rc.bootVolumes = result.items || [];
    $("rc-volume-copy").textContent = `${state.rc.bootVolumes.length} 个 · ${fmtDate(result.synced_at)}`;
    $("rc-volumes-result").innerHTML = state.rc.bootVolumes.length ? state.rc.bootVolumes.map((v) => `<article class="rc-mini-card"><div><strong>${esc(v.display_name || "引导卷")}</strong><small>${rcCompactId(v.id)}</small></div><div><span>${v.size_in_gbs ?? "?"} GB · ${v.vpus_per_gb ?? "?"} VPU/GB</span><small>${esc(v.lifecycle_state || "UNKNOWN")}</small></div><div class="inline-actions"><button class="button" data-rc-volume-edit="${esc(v.id)}" type="button">修改</button><button class="button" data-rc-volume-backup="${esc(v.id)}" type="button">备份</button><button class="button" data-rc-volume-backups="${esc(v.id)}" type="button">备份列表</button><button class="button danger" data-rc-volume-delete="${esc(v.id)}" type="button">删除</button></div></article>`).join("") : '<div class="empty">没有引导卷</div>';
    bindVolumeActions();
  } catch (error) { toast(error.message, "bad"); }
}

function bindVolumeActions() {
  document.querySelectorAll("[data-rc-volume-edit]").forEach((b) => b.addEventListener("click", () => editBootVolume(b)));
  document.querySelectorAll("[data-rc-volume-backup]").forEach((b) => b.addEventListener("click", () => backupBootVolume(b)));
  document.querySelectorAll("[data-rc-volume-backups]").forEach((b) => b.addEventListener("click", () => listBootBackups(b)));
  document.querySelectorAll("[data-rc-volume-delete]").forEach((b) => b.addEventListener("click", () => deleteBootVolume(b)));
}

async function editBootVolume(button) {
  const volume = state.rc.bootVolumes.find((v) => String(v.id) === String(button.dataset.rcVolumeEdit));
  const name = prompt("引导卷名称", volume?.display_name || ""); if (name === null) return;
  const size = prompt("容量 GB（只能扩容；留空不修改）", String(volume?.size_in_gbs || "")); if (size === null) return;
  const vpu = prompt("VPU/GB（0–120；留空不修改）", String(volume?.vpus_per_gb ?? "")); if (vpu === null) return;
  const auto = confirm("确定：启用自动调优；取消：停用自动调优。");
  try {
    await api(`/accounts/${rcAccount().id}/boot-volumes/${encodeURIComponent(volume.id)}/details`, { method: "PUT", body: {
      region: $("rc-volume-region").value.trim(), display_name: name.trim() || null,
      size_in_gbs: size.trim() ? Number(size) : null, vpus_per_gb: vpu.trim() ? Number(vpu) : null, is_auto_tune_enabled: auto,
    }});
    toast("引导卷更新已提交", "good"); await loadBootVolumes();
  } catch (error) { toast(error.message, "bad"); }
}

async function backupBootVolume(button) {
  const name = prompt("备份名称", `N&T-Backup-${Date.now()}`); if (!name) return;
  try {
    await api(`/accounts/${rcAccount().id}/boot-volumes/${encodeURIComponent(button.dataset.rcVolumeBackup)}/backups`, { method: "POST", body: { region: $("rc-volume-region").value.trim(), display_name: name, type: "INCREMENTAL" } });
    toast("引导卷备份已创建", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function listBootBackups(button) {
  try {
    const query = rcQuery({ region: $("rc-volume-region").value.trim(), compartment_id: $("rc-volume-compartment").value.trim() });
    const rows = await api(`/accounts/${rcAccount().id}/boot-volumes/${encodeURIComponent(button.dataset.rcVolumeBackups)}/backups?${query}`);
    state.rc.bootBackups = rows;
    $("rc-backups-panel").hidden = false;
    $("rc-backups-result").innerHTML = rows.length ? rows.map((r) => `<article class="rc-mini-card"><div><strong>${esc(r.display_name || "引导卷备份")}</strong><small>${rcCompactId(r.id)}</small></div><div><span>${r.size_in_gbs ?? "?"} GB</span><small>${esc(r.lifecycle_state || "UNKNOWN")}</small></div><button class="button danger" data-rc-backup-delete="${esc(r.id)}" type="button">删除备份</button></article>`).join("") : '<div class="empty">暂无备份</div>';
    document.querySelectorAll("[data-rc-backup-delete]").forEach((b) => b.addEventListener("click", () => deleteBootBackupRc(b, button)));
  } catch (error) { toast(error.message, "bad"); }
}


async function deleteBootBackupRc(button, sourceButton) {
  if (prompt("请输入 DELETE BACKUP 确认删除备份：") !== "DELETE BACKUP") return;
  try {
    const query = rcQuery({ region: $("rc-volume-region").value.trim(), confirmation: "DELETE BACKUP" });
    await api(`/accounts/${rcAccount().id}/boot-volume-backups/${encodeURIComponent(button.dataset.rcBackupDelete)}?${query}`, { method: "DELETE" });
    toast("引导卷备份删除请求已提交", "good");
    await listBootBackups(sourceButton);
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteBootVolume(button) {
  if (prompt("请输入 DELETE VOLUME 确认永久删除引导卷：") !== "DELETE VOLUME") return;
  try {
    const query = rcQuery({ region: $("rc-volume-region").value.trim(), confirmation: "DELETE VOLUME" });
    await api(`/accounts/${rcAccount().id}/boot-volumes/${encodeURIComponent(button.dataset.rcVolumeDelete)}/permanent?${query}`, { method: "DELETE" });
    toast("引导卷删除请求已提交"); await loadBootVolumes();
  } catch (error) { toast(error.message, "bad"); }
}

async function loadCustomImages() {
  try {
    const query = rcQuery({ region: $("rc-volume-region").value.trim(), compartment_id: $("rc-volume-compartment").value.trim(), refresh: true });
    const result = await api(`/accounts/${rcAccount().id}/images?${query}`);
    state.rc.images = result.items || [];
    $("rc-image-copy").textContent = `${state.rc.images.length} 个 · ${fmtDate(result.synced_at)}`;
    $("rc-images-result").innerHTML = state.rc.images.length ? state.rc.images.map((img) => `<article class="rc-mini-card"><div><strong>${esc(img.display_name || "自定义镜像")}</strong><small>${rcCompactId(img.id)}</small></div><div><span>${esc(img.operating_system || "Custom")} ${esc(img.operating_system_version || "")}</span><small>${esc(img.lifecycle_state || "UNKNOWN")}</small></div><button class="button danger" data-rc-image-delete="${esc(img.id)}" type="button">删除</button></article>`).join("") : '<div class="empty">没有自定义镜像</div>';
    document.querySelectorAll("[data-rc-image-delete]").forEach((b) => b.addEventListener("click", () => deleteCustomImage(b)));
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteCustomImage(button) {
  if (prompt("请输入 DELETE IMAGE 确认：") !== "DELETE IMAGE") return;
  try {
    const query = rcQuery({ region: $("rc-volume-region").value.trim(), confirmation: "DELETE IMAGE" });
    await api(`/accounts/${rcAccount().id}/images/${encodeURIComponent(button.dataset.rcImageDelete)}?${query}`, { method: "DELETE" });
    toast("镜像删除请求已提交"); await loadCustomImages();
  } catch (error) { toast(error.message, "bad"); }
}

async function createInstanceImage() {
  const instance = rcInstanceById($("rc-volume-instance").value);
  const name = prompt("镜像名称", `N&T-Image-${Date.now()}`);
  if (!instance || !name) return;
  try {
    await api(`/accounts/${rcAccount().id}/instances/${encodeURIComponent(instance.id)}/images`, { method: "POST", body: { region: rcRegion(instance), compartment_id: instance.compartment_id, display_name: name } });
    toast("实例镜像创建请求已提交", "good");
  } catch (error) { toast(error.message, "bad"); }
}

async function startRescueMode() {
  const instance = rcInstanceById($("rc-volume-instance").value);
  if (!instance) return;
  const publicKey = prompt("救援控制台使用的 RSA/SSH 公钥：", "");
  if (!publicKey || prompt("请输入 RESCUE 确认：") !== "RESCUE") return;
  try {
    const result = await api(`/accounts/${rcAccount().id}/instances/${encodeURIComponent(instance.id)}/rescue`, { method: "POST", body: { region: rcRegion(instance), public_key: publicKey, confirmation: "RESCUE" } });
    alert(`救援流程已启动。\nConsole Connection：${result.console?.id || "已创建"}`);
  } catch (error) { toast(error.message, "bad"); }
}

/* ------------------------------- VNC -------------------------------- */

function renderVncWorkspace() {
  const selected = state.instances[0]?.id || "";
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("浏览器 VNC", "创建临时 OCI Console Connection，通过加密短时 Token 在浏览器 noVNC 中连接。")}
    <section class="panel rc-control-grid"><label><span>实例</span><select id="rc-vnc-instance">${rcInstanceOptions(selected)}</select></label><label><span>会话时长（分钟）</span><input id="rc-vnc-duration" type="number" min="5" max="120" value="30"></label><div class="inline-actions rc-actions-bottom"><button id="rc-vnc-start" class="button primary" type="button">启动 VNC</button><button id="rc-vnc-refresh" class="button" type="button">刷新本地会话</button></div></section>
    <section class="panel rc-section"><div class="panel-head"><div><h2>VNC 会话</h2><p>列表来自本地 SQLite；服务重启后不会自动恢复连接。</p></div></div><div id="rc-vnc-list"><div class="empty">正在读取本地会话……</div></div></section>`;
  $("rc-vnc-start").addEventListener("click", startVnc);
  $("rc-vnc-refresh").addEventListener("click", loadVncSessions);
  loadVncSessions();
}

async function loadVncSessions() {
  try {
    state.rc.vncSessions = await api(`/accounts/${rcAccount().id}/vnc/sessions`);
    $("rc-vnc-list").innerHTML = state.rc.vncSessions.length ? state.rc.vncSessions.map((s) => `<div class="simple-row"><div><strong>#${s.id} · ${esc(s.status)}</strong><small>${rcCompactId(s.instance_id)} · 到期 ${fmtDate(s.expires_at)}</small>${s.error ? `<small class="bad-text">${esc(s.error)}</small>` : ""}</div><div class="inline-actions">${s.status === "ACTIVE" ? `<button class="button" data-rc-vnc-open="${s.id}" type="button" disabled title="Token 只在创建时返回">需重新创建后打开</button>` : ""}<button class="button danger" data-rc-vnc-stop="${s.id}" type="button">停止/清理</button></div></div>`).join("") : '<div class="empty">暂无 VNC 会话</div>';
    document.querySelectorAll("[data-rc-vnc-stop]").forEach((b) => b.addEventListener("click", () => stopVnc(b)));
  } catch (error) { toast(error.message, "bad"); }
}

async function startVnc() {
  const instance = rcInstanceById($("rc-vnc-instance").value);
  const button = $("rc-vnc-start");
  if (!instance) return;
  setBusy(button, true, "创建中……");
  try {
    const result = await api(`/accounts/${rcAccount().id}/instances/${encodeURIComponent(instance.id)}/vnc/sessions`, { method: "POST", body: { region: rcRegion(instance), duration_minutes: Number($("rc-vnc-duration").value) } });
    window.open(result.viewer_url, "_blank", "noopener,noreferrer");
    toast("VNC 会话已创建，新窗口正在连接", "good");
    await loadVncSessions();
  } catch (error) { toast(error.message, "bad"); }
  finally { setBusy(button, false); }
}

async function stopVnc(button) {
  try {
    await api(`/accounts/${rcAccount().id}/vnc/sessions/${button.dataset.rcVncStop}`, { method: "DELETE" });
    toast("VNC 会话已停止"); await loadVncSessions();
  } catch (error) { toast(error.message, "bad"); }
}

/* --------------------------- Object Storage -------------------------- */

function renderStorageWorkspace() {
  const instance = state.instances[0] || null;
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("对象存储", "所有 Bucket 与对象操作都需要手动触发；支持直接上传与浏览器分片上传。")}
    <section class="panel rc-control-grid"><label><span>区域</span><input id="rc-storage-region" value="${esc(rcRegion(instance))}"></label><label><span>Compartment OCID</span><input id="rc-storage-compartment" value="${esc(instance?.compartment_id || "")}"></label><div class="inline-actions rc-actions-bottom"><button id="rc-storage-load" class="button primary" type="button">读取对象存储</button><button id="rc-storage-sessions" class="button" type="button">分片会话</button></div></section>
    <section id="rc-storage-buckets" class="panel rc-section"><div class="empty">尚未读取 Bucket。</div></section>
    <section id="rc-storage-objects" class="panel rc-section" hidden></section>
    <section id="rc-storage-multipart" class="panel rc-section" hidden></section>`;
  $("rc-storage-load").addEventListener("click", loadStorageOverview);
  $("rc-storage-sessions").addEventListener("click", listMultipartSessions);
}

async function loadStorageOverview() {
  const account = rcAccount();
  const query = rcQuery({ region: $("rc-storage-region").value.trim(), compartment_id: $("rc-storage-compartment").value.trim(), refresh: true });
  try {
    state.rc.storage = await api(`/accounts/${account.id}/object-storage?${query}`);
    renderStorageBuckets();
    toast(`已读取 ${state.rc.storage.buckets?.length || 0} 个 Bucket`, "good");
  } catch (error) { toast(error.message, "bad"); }
}

function renderStorageBuckets() {
  const data = state.rc.storage || {};
  const buckets = data.buckets || [];
  $("rc-storage-buckets").innerHTML = `<div class="panel-head"><div><h2>Bucket</h2><p>Namespace：${esc(data.namespace || "—")} · ${fmtDate(data.synced_at)}</p></div><button id="rc-bucket-create" class="button" type="button">新建 Bucket</button></div>${buckets.length ? `<div class="rc-stack">${buckets.map((b) => `<article class="rc-mini-card"><div><strong>${esc(b.name)}</strong><small>${esc(b.public_access_type || "NoPublicAccess")} · ${esc(b.storage_tier || "Standard")}</small></div><div class="inline-actions"><button class="button" data-rc-bucket-open="${esc(b.name)}" type="button">对象</button><button class="button" data-rc-bucket-access="${esc(b.name)}" type="button">访问类型</button><button class="button danger" data-rc-bucket-delete="${esc(b.name)}" type="button">删除</button></div></article>`).join("")}</div>` : '<div class="empty">暂无 Bucket</div>'}`;
  $("rc-bucket-create").addEventListener("click", createBucketUi);
  document.querySelectorAll("[data-rc-bucket-open]").forEach((b) => b.addEventListener("click", () => openBucket(b.dataset.rcBucketOpen)));
  document.querySelectorAll("[data-rc-bucket-access]").forEach((b) => b.addEventListener("click", () => updateBucketAccessUi(b.dataset.rcBucketAccess)));
  document.querySelectorAll("[data-rc-bucket-delete]").forEach((b) => b.addEventListener("click", () => deleteBucketUi(b.dataset.rcBucketDelete)));
}

async function createBucketUi() {
  const name = prompt("Bucket 名称（全局唯一规则由 OCI 校验）", ""); if (!name) return;
  const access = prompt("访问类型：NoPublicAccess / ObjectRead / ObjectReadWithoutList", "NoPublicAccess") || "NoPublicAccess";
  try {
    await api(`/accounts/${rcAccount().id}/object-storage/buckets`, { method: "POST", body: {
      region: $("rc-storage-region").value.trim(), compartment_id: $("rc-storage-compartment").value.trim(), namespace: state.rc.storage.namespace,
      name, public_access_type: access, storage_tier: "Standard",
    }});
    toast("Bucket 已创建", "good"); await loadStorageOverview();
  } catch (error) { toast(error.message, "bad"); }
}

async function updateBucketAccessUi(name) {
  const access = prompt("访问类型：NoPublicAccess / ObjectRead / ObjectReadWithoutList", "NoPublicAccess"); if (!access) return;
  try {
    await api(`/accounts/${rcAccount().id}/object-storage/buckets/${encodeURIComponent(name)}`, { method: "PUT", body: { region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, public_access_type: access } });
    toast("Bucket 访问类型已更新", "good"); await loadStorageOverview();
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteBucketUi(name) {
  if (prompt("Bucket 必须为空。请输入 DELETE BUCKET 确认：") !== "DELETE BUCKET") return;
  try {
    const query = rcQuery({ region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, confirmation: "DELETE BUCKET" });
    await api(`/accounts/${rcAccount().id}/object-storage/buckets/${encodeURIComponent(name)}?${query}`, { method: "DELETE" });
    toast("Bucket 已删除"); await loadStorageOverview();
  } catch (error) { toast(error.message, "bad"); }
}

function storageObjectPath(bucket, suffix = "") {
  return `/accounts/${rcAccount().id}/object-storage/buckets/${encodeURIComponent(bucket)}/objects${suffix}`;
}

async function openBucket(bucket, prefix = "") {
  try {
    const query = rcQuery({ region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, prefix, delimiter: "/", limit: 200 });
    const result = await api(`${storageObjectPath(bucket)}?${query}`);
    state.rc.objects = { ...result, bucket, prefix };
    renderStorageObjects();
  } catch (error) { toast(error.message, "bad"); }
}

function renderStorageObjects() {
  const data = state.rc.objects || {};
  const objects = data.objects || [];
  const prefixes = data.prefixes || [];
  const box = $("rc-storage-objects"); box.hidden = false;
  box.innerHTML = `<div class="panel-head"><div><h2>${esc(data.bucket || "Bucket")}</h2><p>目录：/${esc(data.prefix || "")} · ${objects.length} 个对象</p></div><div class="inline-actions"><button id="rc-object-upload" class="button" type="button">上传文件</button><button id="rc-object-multipart-upload" class="button" type="button">分片上传</button><button id="rc-object-refresh" class="button" type="button">刷新</button></div></div>
    <input id="rc-object-file" type="file" hidden><input id="rc-object-multipart-file" type="file" hidden>
    ${prefixes.length ? `<div class="rc-chip-list">${prefixes.map((p) => `<button class="button" data-rc-prefix="${esc(p)}" type="button">📁 ${esc(p)}</button>`).join("")}</div>` : ""}
    ${objects.length ? `<div class="table-wrap"><table><thead><tr><th>对象</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${objects.map((o) => `<tr><td>${esc(o.name)}</td><td>${o.size ?? "—"}</td><td>${fmtDate(o.time_created)}</td><td><div class="inline-actions"><button class="button" data-rc-object-preview="${esc(o.name)}" type="button">预览</button><button class="button" data-rc-object-download="${esc(o.name)}" type="button">下载</button><button class="button" data-rc-object-par="${esc(o.name)}" type="button">预签名</button><button class="button danger" data-rc-object-delete="${esc(o.name)}" type="button">删除</button></div></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">当前目录没有对象</div>'}`;
  $("rc-object-upload").addEventListener("click", () => $("rc-object-file").click());
  $("rc-object-file").addEventListener("change", directUploadObject);
  $("rc-object-multipart-upload").addEventListener("click", () => $("rc-object-multipart-file").click());
  $("rc-object-multipart-file").addEventListener("change", multipartUploadObject);
  $("rc-object-refresh").addEventListener("click", () => openBucket(data.bucket, data.prefix));
  document.querySelectorAll("[data-rc-prefix]").forEach((b) => b.addEventListener("click", () => openBucket(data.bucket, b.dataset.rcPrefix)));
  document.querySelectorAll("[data-rc-object-preview]").forEach((b) => b.addEventListener("click", () => fetchObjectContent(b.dataset.rcObjectPreview, true)));
  document.querySelectorAll("[data-rc-object-download]").forEach((b) => b.addEventListener("click", () => fetchObjectContent(b.dataset.rcObjectDownload, false)));
  document.querySelectorAll("[data-rc-object-par]").forEach((b) => b.addEventListener("click", () => createObjectPar(b.dataset.rcObjectPar)));
  document.querySelectorAll("[data-rc-object-delete]").forEach((b) => b.addEventListener("click", () => deleteObjectUi(b.dataset.rcObjectDelete)));
}

async function directUploadObject(event) {
  const file = event.target.files[0]; if (!file) return;
  const objectName = prompt("对象名称", `${state.rc.objects.prefix || ""}${file.name}`); if (!objectName) return;
  const form = new FormData();
  form.set("region", $("rc-storage-region").value.trim()); form.set("namespace", state.rc.storage.namespace); form.set("object_name", objectName); form.set("file", file);
  try {
    await api(storageObjectPath(state.rc.objects.bucket), { method: "POST", body: form });
    toast("对象上传完成", "good"); await openBucket(state.rc.objects.bucket, state.rc.objects.prefix);
  } catch (error) { toast(error.message, "bad"); }
  finally { event.target.value = ""; }
}

async function multipartUploadObject(event) {
  const file = event.target.files[0]; if (!file) return;
  const objectName = prompt("对象名称", `${state.rc.objects.prefix || ""}${file.name}`); if (!objectName) return;
  const statusBox = $("rc-storage-multipart"); statusBox.hidden = false; statusBox.innerHTML = '<div class="empty">正在初始化分片上传……</div>';
  try {
    let session = await api(`/accounts/${rcAccount().id}/object-storage/multipart`, { method: "POST", body: {
      region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, bucket_name: state.rc.objects.bucket,
      object_name: objectName, content_type: file.type || null,
    }});
    const partSize = 16 * 1024 * 1024;
    const count = Math.ceil(file.size / partSize);
    for (let index = 0; index < count; index += 1) {
      const part = file.slice(index * partSize, Math.min(file.size, (index + 1) * partSize));
      const form = new FormData(); form.set("file", part, `${file.name}.part${index + 1}`);
      statusBox.innerHTML = `<div class="empty">上传分片 ${index + 1}/${count}……</div>`;
      session = await api(`/accounts/${rcAccount().id}/object-storage/multipart/${session.id}/parts/${index + 1}`, { method: "POST", body: form });
    }
    await api(`/accounts/${rcAccount().id}/object-storage/multipart/${session.id}/commit`, { method: "POST" });
    statusBox.innerHTML = '<div class="empty">分片上传已完成。</div>';
    toast("分片上传完成", "good"); await openBucket(state.rc.objects.bucket, state.rc.objects.prefix);
  } catch (error) { statusBox.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; toast(error.message, "bad"); }
  finally { event.target.value = ""; }
}

async function fetchObjectContent(name, inline) {
  try {
    const query = rcQuery({ region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, object_name: name, disposition: inline ? "inline" : "attachment" });
    const result = await rcApiBlob(`${storageObjectPath(state.rc.objects.bucket, "/content")}?${query}`);
    if (inline) {
      const url = URL.createObjectURL(result.blob); window.open(url, "_blank", "noopener,noreferrer"); setTimeout(() => URL.revokeObjectURL(url), 120000);
    } else rcDownloadBlob(result.blob, name.split("/").pop());
  } catch (error) { toast(error.message, "bad"); }
}

async function deleteObjectUi(name) {
  if (prompt("请输入 DELETE OBJECT 确认删除对象：") !== "DELETE OBJECT") return;
  try {
    const query = rcQuery({ region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, object_name: name, confirmation: "DELETE OBJECT" });
    await api(`${storageObjectPath(state.rc.objects.bucket)}?${query}`, { method: "DELETE" });
    toast("对象已删除"); await openBucket(state.rc.objects.bucket, state.rc.objects.prefix);
  } catch (error) { toast(error.message, "bad"); }
}

async function createObjectPar(name) {
  const hours = Number(prompt("有效小时数", "24")); if (!hours) return;
  const expires = new Date(Date.now() + hours * 3600000).toISOString();
  try {
    const result = await api(`/accounts/${rcAccount().id}/object-storage/buckets/${encodeURIComponent(state.rc.objects.bucket)}/preauth`, { method: "POST", body: {
      region: $("rc-storage-region").value.trim(), namespace: state.rc.storage.namespace, object_name: name,
      name: `N&T-${Date.now()}`, access_type: "ObjectRead", expires_at: expires,
    }});
    prompt("预签名访问路径（仅显示一次，请复制）", result.access_uri || JSON.stringify(result));
  } catch (error) { toast(error.message, "bad"); }
}

async function listMultipartSessions() {
  try {
    const rows = await api(`/accounts/${rcAccount().id}/object-storage/multipart`);
    const box = $("rc-storage-multipart"); box.hidden = false;
    box.innerHTML = `<div class="panel-head"><div><h2>分片上传会话</h2><p>本地 SQLite 记录。</p></div></div>${rows.length ? rows.map((r) => `<div class="simple-row"><div><strong>#${r.id} · ${esc(r.object_name)}</strong><small>${esc(r.status)} · ${r.parts?.length || 0} 个分片</small></div>${r.status === "ACTIVE" ? `<button class="button danger" data-rc-multipart-abort="${r.id}" type="button">取消</button>` : ""}</div>`).join("") : '<div class="empty">暂无分片会话</div>'}`;
    document.querySelectorAll("[data-rc-multipart-abort]").forEach((b) => b.addEventListener("click", () => abortMultipart(b)));
  } catch (error) { toast(error.message, "bad"); }
}

async function abortMultipart(button) {
  try { await api(`/accounts/${rcAccount().id}/object-storage/multipart/${button.dataset.rcMultipartAbort}/abort`, { method: "POST" }); toast("分片会话已取消"); await listMultipartSessions(); }
  catch (error) { toast(error.message, "bad"); }
}

/* -------------------------- Metrics and Cost -------------------------- */

function renderInsightsWorkspace() {
  const instance = state.instances[0] || null;
  const end = new Date(); const start = new Date(end.getTime() - 30 * 86400000);
  const dateValue = (d) => d.toISOString().slice(0, 10);
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("流量与费用", "只在点击查询时访问 OCI Monitoring 或 Usage API；没有后台采集。")}
    <div class="rc-two-columns"><section class="panel form-panel"><div class="panel-head"><div><h2>实例指标</h2><p>CPU、网络流量与内存指标。</p></div></div><label><span>实例</span><select id="rc-metric-instance">${rcInstanceOptions(instance?.id || "")}</select></label><label><span>时间范围（小时）</span><input id="rc-metric-hours" type="number" min="1" max="744" value="24"></label><button id="rc-metric-load" class="button primary" type="button">查询指标</button><pre id="rc-metric-result" class="rc-json">尚未查询</pre></section>
    <section class="panel form-panel"><div class="panel-head"><div><h2>费用</h2><p>按服务和区域查询每日成本。</p></div></div><label><span>开始日期</span><input id="rc-cost-start" type="date" value="${dateValue(start)}"></label><label><span>结束日期</span><input id="rc-cost-end" type="date" value="${dateValue(end)}"></label><button id="rc-cost-load" class="button primary" type="button">查询费用</button><pre id="rc-cost-result" class="rc-json">尚未查询</pre></section></div>`;
  $("rc-metric-load").addEventListener("click", loadMetrics);
  $("rc-cost-load").addEventListener("click", loadCosts);
}

async function loadMetrics() {
  const instance = rcInstanceById($("rc-metric-instance").value); if (!instance) return;
  try {
    const query = rcQuery({ region: rcRegion(instance), compartment_id: instance.compartment_id, hours: Number($("rc-metric-hours").value), direct: true });
    const result = await api(`/accounts/${rcAccount().id}/instances/${encodeURIComponent(instance.id)}/metrics?${query}`);
    $("rc-metric-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) { toast(error.message, "bad"); }
}

async function loadCosts() {
  try {
    const start = new Date(`${$("rc-cost-start").value}T00:00:00Z`).toISOString();
    const end = new Date(`${$("rc-cost-end").value}T23:59:59Z`).toISOString();
    const result = await api(`/accounts/${rcAccount().id}/costs`, { method: "POST", body: { start_date: start, end_date: end } });
    $("rc-cost-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) { toast(error.message, "bad"); }
}

/* ---------------------- Region, IAM, Limits, Audit --------------------- */

function rcPanelError(message, hint = "") {
  return `<div class="rc-query-error"><strong>读取失败</strong><span>${esc(message || "未知错误")}</span>${hint ? `<small>${esc(hint)}</small>` : ""}</div>`;
}

function rcScopeLabel(value) {
  const key = String(value || "").toUpperCase();
  return ({ GLOBAL: "全局", REGION: "区域", AD: "可用域" })[key] || value || "—";
}

function rcRegionText(row) {
  return row.region_name || row.name || row.region_key || row.key || "—";
}

function renderTenancyWorkspace() {
  const account = rcAccount();
  const defaultRegion = account?.home_region_key || account?.region || account?.home_region_name || "";
  const rootCompartment = account?.tenancy_ocid || "";
  $("tenant-content").innerHTML = `${rcWorkspaceHeader("区域、IAM、配额与审计", "所有读取均由当前租户手动触发，并使用当前租户的独立代理。")}
    <div class="rc-dashboard-grid rc-tenancy-grid">
      <section class="panel"><div class="panel-head"><div><h2>区域订阅</h2><p>按序号逐行显示已订阅与可订阅区域。</p></div><button id="rc-regions-load" class="button" type="button">读取</button></div><div id="rc-regions-result"><div class="empty">尚未读取</div></div></section>
      <section class="panel"><div class="panel-head"><div><h2>OCI IAM</h2><p>用户、组、成员关系与密码策略。</p></div><button id="rc-iam-load" class="button" type="button">读取</button></div><div id="rc-iam-result"><div class="empty">尚未读取</div></div></section>
      <section class="panel"><div class="panel-head"><div><h2>服务配额</h2><p>按序号逐行显示，支持服务端与本地筛选。</p></div><button id="rc-limits-load" class="button" type="button">读取</button></div><label><span>OCI 服务名称（可选）</span><input id="rc-limit-service" placeholder="compute、block-storage…"></label><div id="rc-limits-result"><div class="empty">尚未读取</div></div></section>
      <section class="panel"><div class="panel-head"><div><h2>OCI 审计</h2><p>默认读取当前租户根区间；也可改为其他有权限的 Compartment。</p></div><button id="rc-oci-audit-load" class="button" type="button">读取</button></div><div class="form-grid two-col"><label><span>区域</span><input id="rc-audit-region" value="${esc(defaultRegion)}"></label><label><span>Compartment OCID</span><input id="rc-audit-compartment" value="${esc(rootCompartment)}"><small>留空时后端也会自动使用租户根区间。</small></label><label><span>小时</span><input id="rc-audit-hours" type="number" min="1" max="2160" value="24"></label></div><div id="rc-oci-audit-result"><div class="empty">尚未读取</div></div></section>
    </div>`;
  $("rc-regions-load").addEventListener("click", loadRegionsRc);
  $("rc-iam-load").addEventListener("click", loadIamRc);
  $("rc-limits-load").addEventListener("click", loadLimitsRc);
  $("rc-oci-audit-load").addEventListener("click", loadOciAuditRc);
}

async function loadRegionsRc() {
  const host = $("rc-regions-result");
  host.innerHTML = '<div class="empty">正在读取区域订阅……</div>';
  try {
    state.rc.regions = await api(`/accounts/${rcAccount().id}/regions?refresh=true`);
    renderRegionsRc();
  } catch (error) {
    host.innerHTML = rcPanelError(error.message);
    toast(error.message, "bad");
  }
}

function renderRegionsRc() {
  const data = state.rc.regions || {};
  const subscribed = data.subscribed || [];
  const available = data.available || [];
  const rows = [
    ...subscribed.map((row) => ({ ...row, _status: "subscribed" })),
    ...available.map((row) => ({ ...row, _status: "available" })),
  ].sort((a, b) => rcRegionText(a).localeCompare(rcRegionText(b), "zh-CN"));
  const homeKey = String(rcAccount()?.home_region_key || "").toUpperCase();
  $("rc-regions-result").innerHTML = `<div class="rc-table-summary"><strong>已订阅 ${subscribed.length}</strong><span>可订阅 ${available.length}</span><span>共 ${rows.length} 个区域</span></div>
    ${rows.length ? `<div class="table-wrap rc-numbered-wrap"><table class="dense-table rc-numbered-table"><thead><tr><th class="rc-index-col">序号</th><th>区域</th><th>Region Key</th><th>状态</th><th class="rc-check-col">选择</th></tr></thead><tbody>${rows.map((row, index) => {
      const key = String(row.region_key || row.key || "");
      const subscribedRow = row._status === "subscribed";
      const isHome = key.toUpperCase() === homeKey || Boolean(row.is_home_region);
      return `<tr><td class="rc-index-col">${index + 1}</td><td><strong>${esc(rcRegionText(row))}</strong>${isHome ? '<small class="rc-home-mark">主区域</small>' : ""}</td><td><code>${esc(key || "—")}</code></td><td><span class="badge ${subscribedRow ? "good" : "muted"}">${subscribedRow ? "已订阅" : "可订阅"}</span></td><td class="rc-check-col">${subscribedRow ? "—" : `<input class="rc-region-check" type="checkbox" value="${esc(key)}" aria-label="选择 ${esc(rcRegionText(row))}">`}</td></tr>`;
    }).join("")}</tbody></table></div>` : '<div class="empty">没有区域数据</div>'}
    ${available.length ? '<div class="rc-table-actions"><button id="rc-region-subscribe" class="button primary" type="button">订阅已勾选区域</button></div>' : ""}`;
  $("rc-region-subscribe")?.addEventListener("click", subscribeRegionsRc);
}

async function subscribeRegionsRc() {
  const keys = [...document.querySelectorAll(".rc-region-check:checked")].map((input) => input.value);
  if (!keys.length) return toast("请勾选至少一个区域", "bad");
  try {
    await api(`/accounts/${rcAccount().id}/regions/subscribe`, { method: "POST", body: { region_keys: keys } });
    toast("区域订阅请求已提交", "good");
    await loadRegionsRc();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function loadIamRc() {
  const host = $("rc-iam-result");
  host.innerHTML = '<div class="empty">正在读取 OCI IAM……</div>';
  try {
    state.rc.iam = await api(`/accounts/${rcAccount().id}/iam?refresh=true`);
    renderIamRc();
  } catch (error) {
    host.innerHTML = rcPanelError(error.message, "请确认 API 用户拥有读取 users、groups 与成员关系的权限。");
    toast(error.message, "bad");
  }
}

function renderIamRc() {
  const data = state.rc.iam || {};
  const users = data.users || [];
  const groups = data.groups || [];
  const memberships = data.memberships || [];
  const errors = data.errors || [];
  const userNames = Object.fromEntries(users.map((row) => [String(row.id), row.name || row.id]));
  const groupNames = Object.fromEntries(groups.map((row) => [String(row.id), row.name || row.id]));
  const warnings = errors.length ? `<div class="rc-query-warning"><strong>部分 IAM 数据读取失败</strong>${errors.map((row, index) => `<span>${index + 1}. ${esc(row.scope || "IAM")}：${esc(row.resource_name ? `${row.resource_name} · ${row.message}` : row.message)}</span>`).join("")}</div>` : "";
  $("rc-iam-result").innerHTML = `${warnings}<div class="inline-actions rc-section-actions"><button id="rc-iam-create" class="button primary" type="button">增加用户</button><button id="rc-policy-load" class="button" type="button">密码策略</button></div>
    <h3 class="rc-subtitle">IAM 用户（${users.length}）</h3>${users.length ? `<div class="table-wrap rc-numbered-wrap"><table class="dense-table rc-numbered-table rc-iam-user-table"><thead><tr><th class="rc-index-col">序号</th><th>用户名</th><th>通知邮箱</th><th>MFA</th><th>状态</th><th>创建时间</th><th class="rc-action-col">操作</th></tr></thead><tbody>${users.map((user, index) => `<tr><td class="rc-index-col">${index + 1}</td><td><strong>${esc(user.name || "—")}</strong><small title="${esc(user.id || "")}">${rcCompactId(user.id)}</small></td><td class="rc-email-cell">${esc(user.email || "—")}</td><td><span class="badge ${user.is_mfa_activated ? "good" : "muted"}">${user.is_mfa_activated ? "已启用" : "未启用"}</span></td><td>${esc(user.lifecycle_state || "—")}</td><td>${fmtDate(user.time_created)}</td><td class="rc-action-col"><button class="button small iam-manage-button" data-rc-iam-menu data-rc-iam-user="${esc(user.id)}" data-rc-iam-name="${esc(user.name || "")}" data-rc-iam-current-email="${esc(user.email || "")}" type="button" aria-haspopup="menu" aria-expanded="false">管理 ▾</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty compact">暂无 IAM 用户</div>'}
    <h3 class="rc-subtitle">IAM 用户组（${groups.length}）</h3>${groups.length ? `<div class="table-wrap rc-numbered-wrap rc-short-table"><table class="dense-table rc-numbered-table"><thead><tr><th class="rc-index-col">序号</th><th>用户组</th><th>描述</th><th>状态</th></tr></thead><tbody>${groups.map((group, index) => `<tr><td class="rc-index-col">${index + 1}</td><td><strong>${esc(group.name || "—")}</strong><small title="${esc(group.id || "")}">${rcCompactId(group.id)}</small></td><td>${esc(group.description || "—")}</td><td>${esc(group.lifecycle_state || "—")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty compact">暂无 IAM 用户组</div>'}
    ${groups.length && users.length ? `<div class="form-grid two-col rc-membership-form"><label><span>用户</span><select id="rc-membership-user">${users.map((user) => `<option value="${esc(user.id)}">${esc(user.name)}</option>`).join("")}</select></label><label><span>用户组</span><select id="rc-membership-group">${groups.map((group) => `<option value="${esc(group.id)}">${esc(group.name)}</option>`).join("")}</select></label><button id="rc-membership-add" class="button" type="button">加入用户组</button></div>` : ""}
    <h3 class="rc-subtitle">成员关系（${memberships.length}）</h3>${memberships.length ? `<div class="table-wrap rc-numbered-wrap rc-short-table"><table class="dense-table rc-numbered-table"><thead><tr><th class="rc-index-col">序号</th><th>用户</th><th>用户组</th><th>状态</th><th>操作</th></tr></thead><tbody>${memberships.map((membership, index) => `<tr><td class="rc-index-col">${index + 1}</td><td>${esc(userNames[String(membership.user_id)] || membership.user_id || "—")}</td><td>${esc(groupNames[String(membership.group_id)] || membership.group_id || "—")}</td><td>${esc(membership.lifecycle_state || "—")}</td><td><button class="text-button" data-rc-membership-delete="${esc(membership.id)}" type="button">移除</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty compact">暂无成员关系</div>'}<div id="rc-policy-result"></div>`;
  $("rc-iam-create").addEventListener("click", openIamUserDialogRc);
  $("rc-policy-load").addEventListener("click", loadPolicyRc);
  $("rc-membership-add")?.addEventListener("click", addMembershipRc);
  ensureIamFloatingMenuRc();
  document.querySelectorAll("[data-rc-iam-menu]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    openIamFloatingMenuRc(button);
  }));
  document.querySelectorAll("[data-rc-membership-delete]").forEach((button) => button.addEventListener("click", () => removeMembershipRc(button)));
  ensureIamDialogBindingsRc();
}


function closeIamFloatingMenuRc() {
  const menu = document.getElementById("rc-iam-floating-menu");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  menu.removeAttribute("style");
  document.querySelectorAll("[data-rc-iam-menu][aria-expanded=\"true\"]").forEach((button) => {
    button.setAttribute("aria-expanded", "false");
  });
}

function ensureIamFloatingMenuRc() {
  let menu = document.getElementById("rc-iam-floating-menu");
  if (menu) return menu;
  menu = document.createElement("div");
  menu.id = "rc-iam-floating-menu";
  menu.className = "more-popover iam-user-floating-menu";
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  document.body.appendChild(menu);
  menu.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    closeIamFloatingMenuRc();
    if (button.dataset.rcIamEmail) openIamEmailDialogRc(button);
    else if (button.dataset.rcIamReset) resetIamPasswordRc(button);
    else if (button.dataset.rcIamMfa) resetIamMfaRc(button);
    else if (button.dataset.rcIamDelete) deleteIamUserRc(button);
  });
  document.addEventListener("click", (event) => {
    if (menu.hidden) return;
    if (!menu.contains(event.target) && !event.target.closest("[data-rc-iam-menu]")) closeIamFloatingMenuRc();
  });
  document.addEventListener("scroll", closeIamFloatingMenuRc, true);
  window.addEventListener("resize", closeIamFloatingMenuRc);
  window.addEventListener("blur", closeIamFloatingMenuRc);
  return menu;
}

function openIamFloatingMenuRc(trigger) {
  const menu = ensureIamFloatingMenuRc();
  const userId = trigger.dataset.rcIamUser || "";
  const userName = trigger.dataset.rcIamName || "";
  const currentEmail = trigger.dataset.rcIamCurrentEmail || "";
  document.querySelectorAll("[data-rc-iam-menu][aria-expanded=\"true\"]").forEach((button) => {
    if (button !== trigger) button.setAttribute("aria-expanded", "false");
  });
  menu.innerHTML = `<button data-rc-iam-email="${esc(userId)}" data-rc-iam-name="${esc(userName)}" data-rc-iam-current-email="${esc(currentEmail)}" type="button" role="menuitem">修改通知邮箱</button><button data-rc-iam-reset="${esc(userId)}" type="button" role="menuitem">重置控制台密码</button><button data-rc-iam-mfa="${esc(userId)}" data-rc-iam-name="${esc(userName)}" class="danger-text" type="button" role="menuitem">重置 MFA</button><button data-rc-iam-delete="${esc(userId)}" class="danger-text" type="button" role="menuitem">删除用户</button>`;
  menu.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  const rect = trigger.getBoundingClientRect();
  const viewportGap = 10;
  const width = Math.max(176, menu.offsetWidth || 176);
  const height = menu.offsetHeight || 176;
  let left = Math.min(window.innerWidth - width - viewportGap, Math.max(viewportGap, rect.right - width));
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - viewportGap) top = Math.max(viewportGap, rect.top - height - 6);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
  menu.style.right = "auto";
  menu.style.bottom = "auto";
}

function ensureIamDialogBindingsRc() {
  const userForm = $("iam-user-form");
  if (userForm && !userForm.dataset.bound) {
    userForm.dataset.bound = "1";
    userForm.addEventListener("submit", submitIamUserRc);
    $("iam-email-form").addEventListener("submit", submitIamEmailRc);
    $("iam-secret-copy").addEventListener("click", async () => {
      const value = $("iam-secret-value").textContent;
      try { await navigator.clipboard.writeText(value); toast("临时密码已复制", "good"); }
      catch { toast("浏览器禁止自动复制，请手动复制", "bad"); }
    });
  }
}

function openIamUserDialogRc() {
  const groups = state.rc.iam?.groups || [];
  $("iam-user-form").reset();
  $("iam-user-password").checked = true;
  $("iam-user-error").hidden = true;
  $("iam-user-group").innerHTML = `<option value="">创建后暂不分配权限</option>${groups.map((group) => `<option value="${esc(group.id)}">${esc(group.name || group.id)}</option>`).join("")}`;
  $("iam-user-dialog").showModal();
  $("iam-user-name").focus();
}

async function submitIamUserRc(event) {
  event.preventDefault();
  const button = $("iam-user-submit");
  const errorBox = $("iam-user-error");
  errorBox.hidden = true;
  setBusy(button, true, "创建中……");
  try {
    const result = await api(`/accounts/${rcAccount().id}/iam/users`, { method: "POST", body: {
      name: $("iam-user-name").value.trim(),
      email: $("iam-user-email").value.trim(),
      description: $("iam-user-description").value.trim(),
      group_id: $("iam-user-group").value || null,
      create_console_password: $("iam-user-password").checked,
    }});
    $("iam-user-dialog").close();
    const warnings = result.warnings || [];
    if (result.temporary_password) showIamSecretRc(result.temporary_password, warnings);
    else toast(warnings.length ? `用户已创建；${warnings.join("；")}` : "IAM 用户已创建", warnings.length ? "bad" : "good");
    await loadIamRc();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally { setBusy(button, false); }
}

function openIamEmailDialogRc(button) {
  $("iam-email-user-id").value = button.dataset.rcIamEmail;
  $("iam-email-value").value = button.dataset.rcIamCurrentEmail || "";
  $("iam-email-copy").textContent = `更新 ${button.dataset.rcIamName || "当前用户"} 的通知邮箱。`;
  $("iam-email-error").hidden = true;
  $("iam-email-dialog").showModal();
  $("iam-email-value").focus();
}

async function submitIamEmailRc(event) {
  event.preventDefault();
  const button = $("iam-email-submit");
  const errorBox = $("iam-email-error");
  errorBox.hidden = true;
  setBusy(button, true, "保存中……");
  try {
    await api(`/accounts/${rcAccount().id}/iam/users/${encodeURIComponent($("iam-email-user-id").value)}/notification-email`, { method: "PUT", body: { email: $("iam-email-value").value.trim() } });
    $("iam-email-dialog").close();
    toast("通知邮箱已更新", "good");
    await loadIamRc();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally { setBusy(button, false); }
}

function showIamSecretRc(password, warnings = []) {
  $("iam-secret-value").textContent = password;
  const box = $("iam-secret-warnings");
  box.hidden = !warnings.length;
  box.innerHTML = warnings.length ? `<strong>用户已创建，但部分后续操作失败</strong>${warnings.map((item, index) => `<span>${index + 1}. ${esc(item)}</span>`).join("")}` : "";
  $("iam-secret-dialog").showModal();
}

async function deleteIamUserRc(button) {
  if (prompt("请输入 DELETE USER 确认：") !== "DELETE USER") return;
  try { await api(`/accounts/${rcAccount().id}/iam/users/${encodeURIComponent(button.dataset.rcIamDelete)}?confirmation=DELETE%20USER`, { method: "DELETE" }); toast("IAM 用户已删除"); await loadIamRc(); }
  catch (error) { toast(error.message, "bad"); }
}

async function resetIamPasswordRc(button) {
  if (!confirm("重置后的临时密码只显示一次，确定继续吗？")) return;
  try {
    const result = await api(`/accounts/${rcAccount().id}/iam/users/${encodeURIComponent(button.dataset.rcIamReset)}/reset-password`, { method: "POST" });
    showIamSecretRc(result.password || JSON.stringify(result));
  } catch (error) { toast(error.message, "bad"); }
}

async function resetIamMfaRc(button) {
  const name = button.dataset.rcIamName || "该用户";
  if (!confirm(`确定重置 ${name} 的 MFA 吗？现有 TOTP 设备会被删除，用户下次登录需要重新绑定。`)) return;
  try {
    const result = await api(`/accounts/${rcAccount().id}/iam/users/${encodeURIComponent(button.dataset.rcIamMfa)}/reset-mfa`, { method: "POST", body: { confirmation: "RESET MFA" } });
    toast(result.deleted_count ? `已删除 ${result.deleted_count} 个 MFA 设备` : "当前用户没有可删除的 MFA 设备", "good");
    await loadIamRc();
  } catch (error) { toast(error.message, "bad"); }
}

async function addMembershipRc() {
  try { await api(`/accounts/${rcAccount().id}/iam/memberships`, { method: "POST", body: { user_id: $("rc-membership-user").value, group_id: $("rc-membership-group").value } }); toast("用户组已分配", "good"); await loadIamRc(); }
  catch (error) { toast(error.message, "bad"); }
}

async function removeMembershipRc(button) {
  if (!confirm("确定移除该成员关系吗？")) return;
  try { await api(`/accounts/${rcAccount().id}/iam/memberships/${encodeURIComponent(button.dataset.rcMembershipDelete)}`, { method: "DELETE" }); await loadIamRc(); }
  catch (error) { toast(error.message, "bad"); }
}

async function loadPolicyRc() {
  try {
    const policy = await api(`/accounts/${rcAccount().id}/iam/password-policy`);
    $("rc-policy-result").innerHTML = `<label><span>密码策略 JSON</span><textarea id="rc-policy-json" rows="12">${rcJson(policy.password_policy || policy)}</textarea></label><button id="rc-policy-save" class="button primary" type="button">保存密码策略</button>`;
    $("rc-policy-save").addEventListener("click", savePolicyRc);
  } catch (error) { toast(error.message, "bad"); }
}

async function savePolicyRc() {
  try { const body = JSON.parse($("rc-policy-json").value); await api(`/accounts/${rcAccount().id}/iam/password-policy`, { method: "PUT", body }); toast("OCI 密码策略已保存", "good"); }
  catch (error) { toast(error instanceof SyntaxError ? "密码策略 JSON 格式错误" : error.message, "bad"); }
}

async function loadLimitsRc() {
  const host = $("rc-limits-result");
  host.innerHTML = '<div class="empty">正在读取 OCI 服务配额……</div>';
  try {
    const query = rcQuery({ service_name: $("rc-limit-service").value.trim(), refresh: true });
    state.rc.limits = await api(`/accounts/${rcAccount().id}/limits?${query}`);
    state.rc.limitPage = 1;
    state.rc.limitFilter = "";
    renderLimitsRc();
  } catch (error) {
    host.innerHTML = rcPanelError(error.message);
    toast(error.message, "bad");
  }
}

function renderLimitsRc() {
  const data = state.rc.limits || {};
  const allValues = data.values || [];
  const filter = String(state.rc.limitFilter || "").trim().toLowerCase();
  const values = allValues.filter((row) => !filter || [row.service_name, row.name, row.limit_name, row.scope_type, row.availability_domain].filter(Boolean).join(" ").toLowerCase().includes(filter));
  const pageSize = state.rc.limitPageSize || 100;
  const pageCount = Math.max(1, Math.ceil(values.length / pageSize));
  state.rc.limitPage = Math.min(Math.max(1, state.rc.limitPage || 1), pageCount);
  const start = (state.rc.limitPage - 1) * pageSize;
  const pageRows = values.slice(start, start + pageSize);
  $("rc-limits-result").innerHTML = `<div class="rc-table-summary"><strong>${data.services?.length || 0} 个服务</strong><span>${allValues.length} 条配额</span><span>筛选后 ${values.length} 条</span></div><div class="rc-table-filter"><input id="rc-limit-local-filter" type="search" value="${esc(state.rc.limitFilter || "")}" placeholder="筛选服务、配额名称、作用域或可用域"><span>第 ${state.rc.limitPage} / ${pageCount} 页</span></div>
    ${pageRows.length ? `<div class="table-wrap rc-numbered-wrap rc-limit-table"><table class="dense-table rc-numbered-table"><thead><tr><th class="rc-index-col">序号</th><th>服务</th><th>配额名称</th><th>作用域</th><th>可用域</th><th>值</th></tr></thead><tbody>${pageRows.map((row, index) => `<tr class="${row.error ? "rc-error-row" : ""}"><td class="rc-index-col">${start + index + 1}</td><td>${esc(row.service_name || "—")}</td><td><strong>${esc(row.name || row.limit_name || "—")}</strong></td><td>${esc(rcScopeLabel(row.scope_type))}</td><td>${esc(row.availability_domain || "—")}</td><td>${esc(row.value ?? row.error ?? "—")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">没有符合条件的配额数据</div>'}
    <div class="rc-pagination"><button id="rc-limit-prev" class="button small" type="button" ${state.rc.limitPage <= 1 ? "disabled" : ""}>上一页</button><span>${values.length ? `${start + 1}–${Math.min(start + pageSize, values.length)} / ${values.length}` : "0 / 0"}</span><button id="rc-limit-next" class="button small" type="button" ${state.rc.limitPage >= pageCount ? "disabled" : ""}>下一页</button></div>`;
  $("rc-limit-local-filter").addEventListener("input", (event) => {
    state.rc.limitFilter = event.target.value;
    state.rc.limitPage = 1;
    renderLimitsRc();
    requestAnimationFrame(() => { const input = $("rc-limit-local-filter"); input?.focus(); input?.setSelectionRange(input.value.length, input.value.length); });
  });
  $("rc-limit-prev").addEventListener("click", () => { state.rc.limitPage -= 1; renderLimitsRc(); });
  $("rc-limit-next").addEventListener("click", () => { state.rc.limitPage += 1; renderLimitsRc(); });
}

async function loadOciAuditRc() {
  const host = $("rc-oci-audit-result");
  host.innerHTML = '<div class="empty">正在读取 OCI 审计事件……</div>';
  try {
    const query = rcQuery({ region: $("rc-audit-region").value.trim(), compartment_id: $("rc-audit-compartment").value.trim(), hours: Number($("rc-audit-hours").value), limit: 200 });
    const rows = await api(`/accounts/${rcAccount().id}/oci-audit?${query}`);
    host.innerHTML = rows.length ? `<div class="rc-table-summary"><strong>${rows.length} 条审计事件</strong><span>最近 ${Number($("rc-audit-hours").value)} 小时</span></div><div class="table-wrap rc-numbered-wrap rc-audit-table"><table class="dense-table rc-numbered-table"><thead><tr><th class="rc-index-col">序号</th><th>时间</th><th>事件</th><th>用户</th><th>资源</th></tr></thead><tbody>${rows.map((event, index) => `<tr><td class="rc-index-col">${index + 1}</td><td>${fmtDate(event.event_time || event.datetime)}</td><td><strong>${esc(event.event_name || event.event_type || "—")}</strong></td><td>${esc(event.identity?.principal_name || event.identity?.caller_name || "—")}</td><td title="${esc(event.data?.resource_id || "")}">${esc(event.data?.resource_name || event.data?.resource_id || "—")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">该时间范围内没有审计事件</div>';
  } catch (error) {
    host.innerHTML = rcPanelError(error.message, "请确认 API 用户拥有 AUDIT_EVENT_READ 权限；未填写 Compartment 时会自动使用租户根区间。 ");
    toast(error.message, "bad");
  }
}

/* ------------------------- Launch profiles RC ------------------------- */

window.rcEnhanceLaunchWorkspace = function rcEnhanceLaunchWorkspace() {
  const jobs = $("tenant-launch-jobs");
  const host = $("tenant-launch-profile-host");
  if (!jobs || !host || $("tenant-launch-profile-panel")) return;
  host.innerHTML = "";
  const panel = document.createElement("section");
  panel.id = "tenant-launch-profile-panel";
  panel.className = "launch-profile-content";
  panel.innerHTML = `<div class="launch-profile-toolbar"><button id="rc-profile-save" class="button primary small" type="button">保存当前表单</button><div class="more-menu"><button class="button small" data-profile-bulk-menu type="button">批量操作</button><div class="more-popover profile-bulk-popover" data-profile-bulk-box hidden><button id="rc-profiles-enable" type="button">全部启用</button><button id="rc-profiles-disable" type="button">全部停用</button><button id="rc-jobs-cancel-active" class="danger-text" type="button">取消当前任务</button><button id="rc-jobs-reset-history" type="button">重置全部统计</button></div></div></div><div id="rc-profile-list"><div class="empty compact">正在读取保存配置……</div></div>`;
  host.appendChild(panel);
  $("rc-profile-save").addEventListener("click", saveLaunchProfileRc);
  $("rc-profiles-enable").addEventListener("click", () => bulkProfilesRc(true));
  $("rc-profiles-disable").addEventListener("click", () => bulkProfilesRc(false));
  $("rc-jobs-cancel-active").addEventListener("click", cancelActiveJobsRc);
  $("rc-jobs-reset-history").addEventListener("click", resetAllJobHistoryRc);
  const bulkMenu = panel.querySelector("[data-profile-bulk-menu]");
  const bulkBox = panel.querySelector("[data-profile-bulk-box]");
  bulkMenu.addEventListener("click", (event) => {
    event.stopPropagation();
    bulkBox.hidden = !bulkBox.hidden;
  });
  bulkBox.addEventListener("click", () => { bulkBox.hidden = true; });
  loadLaunchProfilesRc();
};

function currentLaunchProfilePayload() {
  if (!state.launchCatalog || !state.launchResources) throw new Error("请先读取当前租户配置和 Ubuntu 镜像");
  const architecture = $("tenant-launch-architecture").value;
  return {
    mode: state.tenantTab === "launch" ? "CAPACITY_RETRY" : "CREATE",
    requested_count: Number($("tenant-launch-count").value), max_attempts: Number($("tenant-launch-attempts").value),
    retry_interval_seconds: Number($("tenant-launch-interval").value), concurrency: Number($("tenant-launch-concurrency").value),
    catalog_token: state.launchCatalog.catalog_token, region: state.launchCatalog.region,
    compartment_id: $("tenant-launch-compartment").value, availability_domain: $("tenant-launch-ad").value,
    subnet_id: $("tenant-launch-subnet").value, image_id: $("tenant-launch-image").value,
    architecture, shape: architecture === "ARM" ? "VM.Standard.A1.Flex" : "VM.Standard.E2.1.Micro",
    display_name: $("tenant-launch-name").value.trim() || "N&T", ssh_public_key: $("tenant-launch-ssh").value.trim() || null,
    assign_public_ip: $("tenant-launch-public").checked, assign_ipv6_ip: $("tenant-launch-ipv6").checked,
    ocpus: architecture === "ARM" ? optionalNumber("tenant-launch-ocpus") : null,
    memory_in_gbs: architecture === "ARM" ? optionalNumber("tenant-launch-memory") : null,
    boot_volume_size_in_gbs: optionalNumber("tenant-launch-boot"),
  };
}

async function saveLaunchProfileRc() {
  try {
    const name = prompt("配置名称", `${$("tenant-launch-architecture").value} · ${$("tenant-launch-name").value}`); if (!name) return;
    await api(`/accounts/${rcAccount().id}/launch/profiles`, { method: "POST", body: { name, payload: currentLaunchProfilePayload(), enabled: true } });
    toast("抢机配置已保存", "good"); await loadLaunchProfilesRc();
  } catch (error) { toast(error.message, "bad"); }
}

async function loadLaunchProfilesRc() {
  try {
    state.rc.launchProfiles = await api(`/accounts/${rcAccount().id}/launch/profiles`);

    const sidePanel = $("tenant-launch-side");
    if (sidePanel) {
      sidePanel.classList.toggle(
        "launch-side-empty",
        state.rc.launchProfiles.length === 0 &&
          (!Array.isArray(state.launchJobs) || state.launchJobs.length === 0),
      );
    }

    const box = $("rc-profile-list");
    const count = $("tenant-launch-profile-count");
    if (count) count.textContent = String(state.rc.launchProfiles.length);
    if (!box) return;
    box.innerHTML = state.rc.launchProfiles.length ? `<div class="launch-profile-list">${state.rc.launchProfiles.map((p) => `<article class="launch-profile-card"><div class="launch-profile-title"><div><strong>#${p.id} · ${esc(p.name)}</strong><small>${esc(p.payload?.architecture || "—")} · ${esc(p.payload?.shape || "—")} · ${p.payload?.requested_count || 1} 台</small></div><span class="badge ${p.enabled ? "good" : "muted"}">${p.enabled ? "启用" : "停用"}</span></div><div class="launch-profile-meta"><span>${p.payload?.max_attempts || 1} 轮</span><span>${p.payload?.retry_interval_seconds || 30} 秒</span><span>并发 ${p.payload?.concurrency || 1}</span></div><div class="inline-actions compact"><button class="button small primary" data-rc-profile-run="${p.id}" type="button" ${p.enabled ? "" : "disabled"}>运行</button><button class="button small" data-rc-profile-once="${p.id}" type="button">单次</button><button class="button small" data-rc-profile-edit="${p.id}" type="button">编辑</button><button class="button small" data-rc-profile-clone="${p.id}" type="button">复制</button><button class="button small" data-rc-profile-toggle="${p.id}" data-enabled="${p.enabled ? "1" : "0"}" type="button">${p.enabled ? "停用" : "启用"}</button><button class="button small danger" data-rc-profile-delete="${p.id}" type="button">删除</button></div></article>`).join("")}</div>` : '<div class="empty compact"><strong>暂无保存配置</strong><span>配置就绪后点击“保存当前表单”，下次可直接运行。</span></div>';
    document.querySelectorAll("[data-rc-profile-run]").forEach((b) => b.addEventListener("click", () => runProfileRc(b)));
    document.querySelectorAll("[data-rc-profile-once]").forEach((b) => b.addEventListener("click", () => runProfileOnceRc(b)));
    document.querySelectorAll("[data-rc-profile-edit]").forEach((b) => b.addEventListener("click", () => editProfileRc(b)));
    document.querySelectorAll("[data-rc-profile-clone]").forEach((b) => b.addEventListener("click", () => cloneProfileRc(b)));
    document.querySelectorAll("[data-rc-profile-toggle]").forEach((b) => b.addEventListener("click", () => toggleProfileRc(b)));
    document.querySelectorAll("[data-rc-profile-delete]").forEach((b) => b.addEventListener("click", () => deleteProfileRc(b)));
  } catch (error) { toast(error.message, "bad"); }
}

async function runProfileRc(button) {
  try { await api(`/accounts/${rcAccount().id}/launch/profiles/${button.dataset.rcProfileRun}/run`, { method: "POST" }); toast("抢机配置已启动", "good"); loadLaunchJobs(); }
  catch (error) { toast(error.message, "bad"); }
}
async function runProfileOnceRc(button) {
  try {
    await api(`/accounts/${rcAccount().id}/launch/profiles/${button.dataset.rcProfileOnce}/run-once`, { method: "POST" });
    toast("抢机配置已单次执行", "good");
    loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function editProfileRc(button) {
  const profile = state.rc.launchProfiles.find((item) => String(item.id) === String(button.dataset.rcProfileEdit));
  if (!profile) return;
  const payload = { ...(profile.payload || {}) };
  const name = prompt("配置名称", profile.name); if (!name) return;
  const requested = prompt("创建数量（1–20）", String(payload.requested_count || 1)); if (requested === null) return;
  const attempts = prompt("最大尝试轮数", String(payload.max_attempts || 1)); if (attempts === null) return;
  const interval = prompt("重试间隔秒（15–86400）", String(payload.retry_interval_seconds || 30)); if (interval === null) return;
  const concurrency = prompt("并发数（1–5）", String(payload.concurrency || 1)); if (concurrency === null) return;
  payload.requested_count = Number(requested); payload.max_attempts = Number(attempts); payload.retry_interval_seconds = Number(interval); payload.concurrency = Number(concurrency);
  try {
    await api(`/accounts/${rcAccount().id}/launch/profiles/${profile.id}`, { method: "PUT", body: { name, payload } });
    toast("抢机配置已更新", "good"); await loadLaunchProfilesRc();
  } catch (error) { toast(error.message, "bad"); }
}

async function cancelActiveJobsRc() {
  if (!confirm("确定取消当前租户所有运行中的创建/抢机任务吗？")) return;
  try {
    const result = await api(`/accounts/${rcAccount().id}/launch/actions/cancel-active`, { method: "POST" });
    toast(`已提交取消 ${result.count} 个任务`, "good"); loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function resetAllJobHistoryRc() {
  if (!confirm("确定重置当前租户全部已结束任务的尝试和失败统计吗？")) return;
  try {
    const result = await api(`/accounts/${rcAccount().id}/launch/actions/reset-history`, { method: "POST" });
    toast(`已重置 ${result.count} 个任务`, "good"); loadLaunchJobs();
  } catch (error) { toast(error.message, "bad"); }
}

async function cloneProfileRc(button) { try { await api(`/accounts/${rcAccount().id}/launch/profiles/${button.dataset.rcProfileClone}/clone`, { method: "POST" }); await loadLaunchProfilesRc(); } catch (error) { toast(error.message, "bad"); } }
async function toggleProfileRc(button) { try { await api(`/accounts/${rcAccount().id}/launch/profiles/${button.dataset.rcProfileToggle}`, { method: "PUT", body: { enabled: button.dataset.enabled !== "1" } }); await loadLaunchProfilesRc(); } catch (error) { toast(error.message, "bad"); } }
async function deleteProfileRc(button) { if (!confirm("确定删除该抢机配置吗？")) return; try { await api(`/accounts/${rcAccount().id}/launch/profiles/${button.dataset.rcProfileDelete}`, { method: "DELETE" }); await loadLaunchProfilesRc(); } catch (error) { toast(error.message, "bad"); } }
async function bulkProfilesRc(enabled) { try { await api(`/accounts/${rcAccount().id}/launch/profiles`, { method: "PUT", body: { enabled } }); await loadLaunchProfilesRc(); toast(enabled ? "配置已全部启用" : "配置已全部停用", "good"); } catch (error) { toast(error.message, "bad"); } }
