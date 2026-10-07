from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
checks: list[str] = []


def ok(name: str) -> None:
    checks.append(name)


def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


index = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
app_js = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
rc_js = (ROOT / "frontend/rc.js").read_text(encoding="utf-8")
ui2_js = (ROOT / "frontend/ui2.js").read_text(encoding="utf-8")
launch_service = (ROOT / "backend/app/launch_service.py").read_text(encoding="utf-8")
main_py = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
router_py = (ROOT / "backend/app/v7_rc_router.py").read_text(encoding="utf-8")
compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
compose = yaml.safe_load(compose_text)

ids = re.findall(r'\bid="([^"]+)"', index)
require(len(ids) == len(set(ids)), "frontend/index.html 存在重复 id")
if len(ids) == len(set(ids)):
    ok(f"HTML IDs unique ({len(ids)})")

for asset in ("frontend/app.js", "frontend/rc.js", "frontend/ui2.js", "frontend/styles.css", "frontend/vnc.html"):
    require((ROOT / asset).is_file(), f"缺少静态文件：{asset}")
ok("static assets present")

nav = re.findall(r'<button class="nav[^\"]*" data-page="([^"]+)"', index)
expected_nav = ["dashboard", "accounts", "proxies", "tasks", "instances", "launch", "cloudflare", "audit", "settings"]
require(nav == expected_nav, f"一级导航不正确：{nav}")
if nav == expected_nav:
    ok("grouped management navigation")

expected_tabs = [
    "instances", "create", "launch", "network", "security", "volumes",
    "vnc", "storage", "insights", "tenancy", "account",
]
tabs = re.findall(r'data-tenant-tab="([^"]+)"', index)
require(tabs == expected_tabs, f"租户标签不正确：{tabs}")
if tabs == expected_tabs:
    ok("tenant workspace tabs")

require("N&amp;T 1.0" in index, "前端产品版本不是 N&T 1.0")
require('version: str = "1.0.4"' in (ROOT / "backend/app/config.py").read_text(), "后端版本不是 1.0.4")
ok("version contract")

require("VM.Standard.A1.Flex" in launch_service, "缺少 ARM A1 白名单")
require("VM.Standard.E2.1.Micro" in launch_service, "缺少 AMD E2.1.Micro 白名单")
require("Ubuntu" in launch_service, "缺少 Ubuntu 镜像过滤")
require("SUPPORTED_LAUNCH_SHAPES" in launch_service, "缺少架构白名单映射")
require("shape_config" in launch_service, "缺少 ARM shape_config 逻辑")
ok("ARM/AMD and Ubuntu launch allowlist")

require("ensure_launch_network" in launch_service, "缺少一键自动网络服务")
require("CreateVcnDetails" in launch_service, "自动网络缺少 VCN 创建")
require("CreateInternetGatewayDetails" in launch_service, "自动网络缺少 Internet Gateway")
require("CreateSubnetDetails" in launch_service, "自动网络缺少子网创建")
require("recommended_subnet_id" in launch_service, "缺少自动推荐子网")
require("一键创建 N&T 网络" in app_js, "前端缺少自动网络入口")
require('tenant-launch-subnet" type="hidden' in app_js, "子网仍作为普通手动下拉框")
ok("automatic launch network and hidden subnet selection")

require('function openTenant(accountId, initialTab = "instances")' in app_js, "租户入口未支持目标标签")
require('selectTenantTab(initialTab, { automatic: true })' in app_js, "选择管理租户后未自动加载当前功能")
require('queueMicrotask(() => loadLaunchCatalog({ automatic: true }))' in app_js, "创建/抢机页面未自动读取租户配置")
require('openTenant(Number(accountId), tab)' in ui2_js, "快捷管理入口未把目标页面传给租户工作区")
require('重新读取' in app_js, "自动读取后缺少手动重试入口")
ok("tenant selection auto-loads active OCI data")

require('id="tenant-launch-side"' in app_js, "缺少创建/抢机侧栏")
require('data-launch-side-tab="profiles"' in app_js and 'data-launch-side-tab="jobs"' in app_js, "保存配置和任务记录未合并为标签")
require('class="launch-submit-bar"' in app_js, "缺少固定提交栏")
require('class="form-grid launch-basic-grid"' in app_js, "基础配置未使用紧凑网格")
require('launch-env-chips' in app_js and 'setLaunchEnvironmentState' in app_js, "缺少自动环境状态摘要")
require('body.tenant-mode .topbar' in (ROOT / "frontend/styles.css").read_text(), "租户工作区未精简顶栏")
ok("compact launch workspace")


oci_rc_service = (ROOT / "backend/app/oci_rc_service.py").read_text(encoding="utf-8")
require("user_id=user_id" in oci_rc_service, "IAM 成员关系仍未按 user_id 过滤")
require('"errors": errors' in oci_rc_service, "IAM 未返回部分读取错误")
require('row["service_name"] = name' in oci_rc_service, "服务配额未补全 service_name")
require('compartment_id: str | None = None' in router_py, "OCI 审计 Compartment 未支持租户根区间默认值")
require('account.get("tenancy_ocid")' in router_py, "OCI 审计未默认使用租户根区间")
require('class="rc-region-check"' in rc_js, "区域订阅未改为逐行勾选")
require('>序号<' in rc_js, "区域/IAM/配额/审计表格缺少序号列")
require('function renderLimitsRc()' in rc_js, "服务配额缺少分页渲染")
require('rc-limit-local-filter' in rc_js, "服务配额缺少本地筛选")
ok("IAM, audit and numbered tenancy tables")

require("background_queries" in main_py and "False" in main_py, "缺少后台查询关闭状态")
for forbidden in ("apscheduler", "celery", "schedule.every", "BackgroundScheduler"):
    require(forbidden.lower() not in (main_py + router_py).lower(), f"发现禁止的后台调度器：{forbidden}")
ok("no OCI background scheduler")

require("/accounts/{account_id}/" in router_py, "RC 资源路由缺少 account_id")
required_routes = [
    "/instances/{instance_id}/network",
    "/vnic-attachments/bulk-delete",
    "/security-lists/{security_list_id}",
    "/boot-volumes",
    "/images",
    "/regions",
    "/iam",
    "/limits",
    "/oci-audit",
    "/object-storage",
    "/launch/profiles",
    "/vnc/sessions",
]
for route in required_routes:
    require(route in router_py, f"缺少租户路由：{route}")
ok("tenant-scoped RC route set")

services = compose.get("services", {})
require(services.get("api", {}).get("network_mode") == "host", "API 未使用 host network")
require(services.get("web", {}).get("network_mode") == "host", "Web 未使用 host network")
require(services.get("monitor", {}).get("network_mode") == "host", "Monitor 未使用 host network")
require("9858" in compose_text and "9859" in compose_text and "9860" in compose_text, "Compose 缺少 9858/9859/9860")
require("9857" not in compose_text, "Compose 不得包含 9857")
require("--host\n      - 127.0.0.1" in compose_text, "API 未绑定 127.0.0.1")
require("listen 127.0.0.1:9859" in (ROOT / "frontend/nginx.conf").read_text(), "Web 未绑定 127.0.0.1:9859")
ok("host-network and loopback port contract")

require("rcRenderTenantTab" in app_js and "rcEnhanceLaunchWorkspace" in app_js, "主前端未加载租户工作区")
require("ui2RenderAccounts" in ui2_js and "ui2LoadGlobalInstances" in ui2_js and "ui2LoadGlobalLaunch" in ui2_js, "缺少列表管理工作区")
require("ui2.js" in index and "COPY ui2.js" in (ROOT / "frontend/Dockerfile").read_text(), "ui2.js 未正确打包")
for feature in (
    "指定网段换 IP", "IP 质量", "删除全部附属 VNIC", "安全规则", "引导卷与镜像",
    "VNC 会话", "分片上传", "区域订阅", "OCI IAM", "服务配额", "OCI 审计",
):
    require(feature in rc_js, f"前端缺少功能文案：{feature}")
ok("RC frontend workspaces")

require('name="proxy_enabled"' in index and 'name="proxy_url"' in index, "添加租户时缺少独立代理字段")
require('id="proxy-dialog"' in index, "缺少租户代理编辑对话框")
require('/proxy/test' in app_js, "缺少未保存代理测试流程")
require('proxy_enabled: bool = Form(False)' in main_py, "账户导入后端未接收代理开关")
require('proxy_url=normalized_proxy_url if proxy_enabled else None' in main_py, "OCI 导入验证未使用租户代理")
ok("tenant proxy import and management")

proxy_repository = (ROOT / "backend/app/proxy_repository.py").read_text(encoding="utf-8")
database_py = (ROOT / "backend/app/database.py").read_text(encoding="utf-8") + "\n" + (ROOT / "backend/app/schema_migrations.py").read_text(encoding="utf-8")
require("CREATE TABLE IF NOT EXISTS proxy_profiles" in database_py, "缺少全局代理库表")
require('"proxy_profile_id"' in database_py, "OCI 账户缺少代理库引用")
require("migrate_legacy_account_proxies" in main_py, "旧租户代理未迁移到代理库")
require('f"{settings.api_prefix}/proxies"' in main_py, "缺少代理库 API")
require("proxy_url_encrypted" in proxy_repository and "encrypt_secret" in proxy_repository, "代理凭据未加密入库")
require('id="proxy-profile-select"' in index, "租户代理弹窗缺少代理库选择")
require('data-config-proxy="${account.id}"' in ui2_js and "proxy_profile_name" in ui2_js, "API 清单未直接显示和选择代理")
ok("database proxy library and tenant bindings")

for field_id in (
    "import-proxy-scheme", "import-proxy-host", "import-proxy-port",
    "import-proxy-username", "import-proxy-password",
    "proxy-scheme", "proxy-host", "proxy-port", "proxy-username", "proxy-password",
):
    require(f'id="{field_id}"' in index, f"手动代理缺少字段：{field_id}")
require('function buildManualProxyUrl' in app_js, "前端缺少手动代理 URL 组装逻辑")
require('buildManualProxyUrl("import-proxy")' in app_js, "导入账户未使用手动代理字段")
require('buildManualProxyUrl("proxy")' in app_js, "租户代理新增未使用手动代理字段")
require('.manual-proxy-grid {' in (ROOT / 'frontend/styles.css').read_text() and '.proxy-current-config {' in (ROOT / 'frontend/styles.css').read_text(), "手动代理弹窗缺少新版布局")
ok("manual proxy fields and editor")

for forbidden in ("QuickDD", "SFTP", "SSH 网页终端", "AI 助手", "GCP"):
    require(forbidden not in index + app_js + rc_js + ui2_js, f"前端出现明确排除功能：{forbidden}")
ok("excluded modules absent from UI")

require("/notification-email" in router_py, "缺少 IAM 通知邮箱修改路由")
require("/reset-mfa" in router_py, "缺少 IAM MFA 重置路由")
require("iam-user-dialog" in index and "iam-email-dialog" in index, "缺少 IAM 用户管理弹窗")
require("data-rc-iam-mfa" in rc_js, "缺少前端 MFA 重置操作")
require('description=(description or "").strip()' in oci_rc_service, "IAM 用户创建未提交必需的 description")
ok("IAM user lifecycle")

require('id = "rc-iam-floating-menu"' in rc_js or 'menu.id = "rc-iam-floating-menu"' in rc_js, "IAM 管理菜单未使用浮动层")
require('document.body.appendChild(menu)' in rc_js, "IAM 浮动菜单未挂载到 body")
require('position: fixed' in (ROOT / "frontend/styles.css").read_text() and '.iam-user-floating-menu' in (ROOT / "frontend/styles.css").read_text(), "IAM 管理菜单未脱离表格滚动容器")
require('.rc-iam-user-table { min-width: 690px' in (ROOT / "frontend/styles.css").read_text(), "IAM 用户表未缩小字号和最小宽度")
ok("compact IAM table and visible floating menu")

require('id="account-type-filter"' in index, "账户类型未使用下拉选择器")
require('<option value="">全部类型</option>' in index and '<option value="PERSONAL_FREE">个人免费号</option>' in index and '<option value="PERSONAL_UPGRADED">个人升级号</option>' in index, "账户类型下拉选项不完整")
require('id="account-region-filter"' in index, "账户筛选缺少区域选择器")
require('id="account-sort-filter"' in index and 'class="sort-cycle-control"' in index, "账户排序未使用双按钮循环控件")
require('data-account-sort-key="tenancy"' in index, "缺少租户排序按钮")
require('data-account-sort-key="survival"' in index, "缺少存活时间排序按钮")
require('tenancy_asc' not in index and 'tenancy_desc' not in index, "排序界面仍暴露四个方向选项")
require('survival_asc' not in index and 'survival_desc' not in index, "排序界面仍暴露四个方向选项")
require('function ui2ToggleAccountSort' in ui2_js, "缺少点击切换正序/反序逻辑")
require('current === ascending ? descending : ascending' in ui2_js, "排序按钮没有按点击在正序与反序间切换")
require('if (!mode) return [...accounts];' in ui2_js, "未选择排序时没有保持原始顺序")
require('localStorage.removeItem("oci_nt_account_sort_rc312")' in ui2_js, "重置时没有清除排序状态")
require('registered_desc' not in index and 'registered_asc' not in index, "排序中仍保留注册日期")
require('id="account-rename-dialog"' in index and 'id="account-rename-form"' in index, "缺少自定义名称统一弹窗")
require('prompt("自定义名称"' not in ui2_js, "修改自定义名称仍使用浏览器原生 prompt")
require('function ui2SortAccounts' in ui2_js and 'function ui2PopulateAccountFilters' in ui2_js, "账户级联筛选与排序逻辑不完整")
require('"us-phoenix-1": ["凤凰城", "Phoenix"]' in ui2_js, "凤凰城区域未使用中文（英文）格式")
require('`${mapped[0]}（${mapped[1]}）`' in ui2_js, "区域显示未组合中文与英文")
require('ui2PopulateAccountFilters();\n    renderAccounts();' in ui2_js, "切换账户类型后未联动刷新区域")
require('.sort-cycle-control {' in (ROOT / "frontend/styles.css").read_text(), "循环排序控件缺少样式")
require('.iam-user-form-grid' in (ROOT / "frontend/styles.css").read_text(), "IAM 增加用户弹窗未使用自适应网格")
require('.iam-dialog { width: min(640px' in (ROOT / "frontend/styles.css").read_text(), "IAM 增加用户弹窗尺寸未修正")
ok("compact cyclic sorting and account filters")

require('id="utility-dialog"' in index, "缺少统一操作弹窗")
require('function utilityDialogField' in app_js and 'openUtilityDialog' in app_js, "统一弹窗工具未加载")
require('prompt("实例名称"' not in app_js and 'prompt("实例名称"' not in ui2_js, "实例修改仍使用浏览器原生 prompt")
require('dnsPayloadFromDialog' in app_js and 'dnsPayloadFromPrompts' not in app_js, "Cloudflare DNS 编辑仍使用原生弹窗")
require('openMessageDialog' in ui2_js, "任务尝试记录未使用统一弹窗")
require('.utility-dialog' in (ROOT / "frontend/styles.css").read_text(), "统一弹窗缺少样式")
ok("unified operation dialogs")

require('id="google-auth-form"' in index, "缺少 Gmail 设置表单")
require('/settings/google-auth' in main_py, "缺少 Gmail 设置 API")
require('oauth_login_states' in (ROOT / "backend/app/database.py").read_text(), "缺少一次性 OAuth state 表")
require('nonce' in (ROOT / "backend/app/google_oauth_service.py").read_text(), "缺少 Google nonce 校验")
ok("Gmail settings management, one-time state and nonce verification")

styles = (ROOT / "frontend/styles.css").read_text(encoding="utf-8")
require('id="audit-search"' in index and 'id="audit-count"' in index, "操作记录缺少本地搜索和数量显示")
require('function renderAudit()' in app_js and 'class="audit-table"' in app_js, "操作记录未使用紧凑可读表格")
require('class="settings-page-layout"' in index and '.settings-page-layout {' in styles, "系统设置未使用分组网格布局")
require('.settings-switch input[type="checkbox"]' in styles, "系统设置开关未使用统一样式")
require('settings-backup-panel' in index and 'settings-card-footer' in index, "系统设置卡片结构不完整")
require('accounts.length > 8 ? " is-scrollable"' in ui2_js, "API 少量账户仍会强制固定滚动高度")
require('function ui2PositionAccountMenu' in ui2_js and 'account-more-popover' in ui2_js, "API 更多操作菜单仍可能被表格裁切")
require('.audit-table { min-width: 980px; table-layout: fixed;' in styles, "操作记录列宽未固定")
ok("readable audit and unclipped API actions")

account_repository = (ROOT / "backend/app/account_repository.py").read_text(encoding="utf-8")
account_age = (ROOT / "backend/app/account_age.py").read_text(encoding="utf-8")
oci_service = (ROOT / "backend/app/oci_service.py").read_text(encoding="utf-8")
require("calculate_survival_days" in account_repository, "账户列表未使用统一存活时间计算")
require("elapsed_seconds // 86_400" in account_age and "+ 1" in account_age, "存活时间不是注册当天第 1 天、每 24 小时加 1 天")
require("earliest_registration_time" in oci_service, "升级账户未按最早订阅时间计算注册时间")
require("registration_time" in ui2_js and "subscription_time_start" in ui2_js, "注册时间未使用后端有效注册时间")
require("account.subscription_start_date || account.created_at" not in ui2_js, "注册时间仍错误回退为本地导入时间")
require('registration_time: str | None = None' in main_py, "账户 API 未返回有效注册时间")
require('settings-unified-grid' in index, "系统设置未使用统一五卡片布局")
require(".account-age-cell strong" in styles and ".api-table tbody tr:hover" in styles, "API 账户年龄与表格视觉样式未完善")
require('width: min(1120px, 100%)' in styles, "系统设置填写区域仍过宽")
require('width: 36px;' in styles and 'height: 20px;' in styles, "系统设置开关未缩小")
require('font-size: 13px;' in styles and '.sort-cycle-control button' in styles, "筛选控件字体未与侧边导航统一")
ok("registration age, compact settings and cyclic sorting")

require(index.index('id="credential-form"') < index.index('id="telegram-form"') < index.index('id="google-auth-form"') < index.index('id="check-setting-form"') < index.index('settings-backup-panel'), "系统设置卡片顺序不正确")
require('grid-template-areas:' in styles and '"login telegram"' in styles and '"gmail backup"' in styles and '"gmail check"' in styles, "系统设置网格区域不正确")
require('--font-ui: 13px;' in styles and '--font-page-title: 24px;' in styles, "全站字体变量未统一")
require('button,\ninput,\ntextarea,\nselect,' in styles, "表单与按钮未统一字号")
ok("settings order and global typography")

# OCI Config import and saved-proxy selection
require('id="import-region-detected"' in index, "添加 OCI API 缺少区域识别结果")
require('id="import-proxy-profile-select"' in index, "添加 OCI API 缺少已保存代理选择")
require('id="import-proxy-mode-existing"' in index and 'id="import-proxy-mode-new"' in index, "添加 OCI API 缺少代理来源切换")
require('proxy_profile_id: int | None = Form(None)' in main_py, "账户导入接口未接收代理库 ID")
require('decrypt_proxy_profile_url(proxy_profile_id)' in main_py, "账户导入未解密使用已保存代理")
require('configured_region=credentials["region"]' in main_py, "账户导入命名未使用 Config 区域兜底")
require('function updateImportRegionDetection' in app_js, "前端缺少 OCI Config 区域识别")
require('function populateImportProxyProfileSelect' in app_js, "前端缺少导入代理库选择逻辑")
require('/proxies/${profileId}/test' in app_js, "添加 OCI API 未支持测试已保存代理")
ok("account import region and saved proxy selection")


require('id="tasks-page"' in index and 'data-page="tasks"' in index, "缺少任务中心页面或导航")
require('/tasks/proxy-health' in main_py and '/tasks/{{task_id}}/retry' in main_py, "缺少任务中心 API")
require('manual_tasks' in database_py and 'schema_migrations' in (ROOT / "backend/app/schema_migrations.py").read_text(), "缺少数据库版本管理或任务表")
require('id="proxy-test-all"' in index and 'startProxyHealthAll' in app_js, "缺少代理批量健康检测")
require('last_latency_ms' in proxy_repository and 'proxy_health_history' in proxy_repository, "缺少代理健康数据持久化")
ok("V1.0.1 migration, task center and proxy health")

require('name: "scheme", label: "协议"' in app_js and 'name: "host", label: "服务器"' in app_js, "编辑代理未提供协议和服务器字段")
require('name: "port", label: "端口"' in app_js and 'name: "username", label: "用户名（可选）"' in app_js, "编辑代理未提供端口和用户名字段")
require('build_proxy_url_update' in main_py and 'public_profile_for_edit' in proxy_repository, "后端未支持安全的分项代理编辑")
require('data-ui2-account-menu' in ui2_js and 'account-more-popover-global' in styles, "API 更多操作未使用全局浮层")
require('flex-wrap: nowrap;' in styles and '.proxy-library-command .proxy-page-toolbar' in styles, "代理顶部操作按钮仍可能换行")
ok("proxy editor fields, single-line toolbar and stable API menu")


require('/accounts/import-preview' in main_py, "缺少 OCI 导入预检 API")
require('accountImportPreviewMessage' in app_js and 'confirmAccountImportPreview' in app_js, "前端未在写库前展示 OCI 导入预览")
require('update_existing' in main_py and 'formData.set("update_existing"' in app_js, "重复 OCI API 未支持确认后更新")
require('fingerprint_matches' in main_py and 'compute_private_key_fingerprint' in main_py, "导入预检未校验私钥指纹")
ok("OCI import preview, fingerprint validation and duplicate update")

instance_batch_service = (ROOT / "backend/app/instance_batch_service.py").read_text(encoding="utf-8")
require('/tasks/instance-batch' in main_py, "缺少实例批量任务 API")
require('INSTANCE_BATCH' in instance_batch_service and 'concurrency": 1' in instance_batch_service, "实例批量任务未使用串行低并发")
require('id="instance-batch-toolbar"' in index and 'data-instance-batch-operation="START"' in index, "实例页面缺少批量操作栏")
require('data-instance-select' in ui2_js and 'instance-select-all' in ui2_js, "实例列表缺少复选框和全选")
require('ui2StartInstanceBatch' in ui2_js and 'showPage("tasks")' in ui2_js, "实例批量操作未接入任务中心")
ok("persistent serial instance batch operations")

settings_repository = (ROOT / "backend/app/settings_repository.py").read_text(encoding="utf-8")
telegram_service = (ROOT / "backend/app/telegram_service.py").read_text(encoding="utf-8")
for category in ("account_check", "instance_operation", "launch_task", "proxy_alert", "system_backup"):
    require(category in settings_repository, f"Telegram 缺少通知分类：{category}")
require('id="telegram-instance-operation"' in index and 'id="telegram-system-backup"' in index, "Telegram 分类开关未加入设置页")
require('format_task_summary_message' in telegram_service and 'settings.display_version' in telegram_service, "批量任务 Telegram 汇总或版本文案不正确")
ok("Telegram category switches and single task summaries")

require('ui2-global-action-tooltip' in ui2_js and 'document.body.appendChild(tooltip)' in ui2_js, "API 操作提示未使用页面级浮层")
require('.ui2-global-action-tooltip' in styles and 'z-index: 40000' in styles, "页面级操作提示缺少样式")
ok("unclipped keyboard-stable API action tooltips")



launch_profile_service = (ROOT / "backend/app/launch_profile_service.py").read_text(encoding="utf-8")
require("TENANT_BOUND_PROFILE_FIELDS" in launch_profile_service, "跨租户配置复制未清理租户专属资源")
require("preflight_launch_payload" in launch_profile_service, "缺少开机配置资源预检服务")
require("load_launch_catalog" in launch_profile_service and "load_launch_resources" in launch_profile_service, "开机预检未读取当前租户资源")
require('/launch/profiles/{profile_id}/preflight' in router_py, "缺少开机配置预检路由")
require('/launch/profiles/{profile_id}/copy' in router_py, "缺少跨租户配置复制路由")
require('/launch/jobs/{job_id}/retry-failed' in router_py, "缺少只重试失败配置路由")
require('复制到其他租户' in ui2_js and 'data-ui2-profile-preflight' in ui2_js, "开机管理缺少跨租户复制或预检入口")
require('只重试失败' in ui2_js and 'data-launch-retry-failed' in app_js, "开机任务缺少只重试失败项入口")
ok("launch profile preflight, cross-tenant copy and failed-only retry")

require('id="account-page-size"' in index and 'id="instance-page-size"' in index, "API/实例表格缺少每页数量设置")
require('id="account-columns"' in index and 'id="instance-columns"' in index, "API/实例表格缺少列设置")
require('function ui2ConfigureColumns' in ui2_js and 'function ui2PageWindow' in ui2_js, "表格列设置或本地分页逻辑缺失")
require('data-copy-text' in ui2_js and 'function ui2CopyText' in ui2_js, "OCID/IP 缺少快捷复制")
require('oci_nt_filter_instance_status' in ui2_js and 'oci_nt_filter_proxy_status' in app_js, "筛选条件未写入本地记忆")
require('.local-pagination {' in styles and '.copy-inline-button {' in styles, "分页或复制按钮缺少样式")
ok("table columns, page size, filter memory and OCID/IP copy")



require('id="task-cleanup"' in index and '/tasks/cleanup' in main_py, "任务中心缺少历史清理")
require('interrupted' in (ROOT / "backend/app/task_repository.py").read_text(), "任务中心缺少中断项目计数")
require('data-backup-verify' in app_js and '/system/backups/{{backup_name}}/verify' in main_py, "本地备份缺少完整性验证")
require('id="database-maintain"' in index and '/system/database/maintain' in main_py, "缺少 SQLite 手动检查与优化")
ok("task recovery, history cleanup and verified backups")

backup_service = (ROOT / "backend/app/backup_service.py").read_text(encoding="utf-8")
task_repository = (ROOT / "backend/app/task_repository.py").read_text(encoding="utf-8")
require('id="task-export-csv"' in index and 'id="task-export-json"' in index, "任务详情缺少 CSV/JSON 导出")
require('function exportTaskCsv' in app_js and 'function exportTaskJson' in app_js, "任务导出逻辑未加载")
require('/system/diagnostics/export' in main_py and 'id="system-status-export"' in index, "缺少脱敏诊断导出")
require('load_backup_policy' in settings_repository and 'cleanup_old_backups' in backup_service, "缺少可配置备份保留策略")
require('id="backup-policy-form"' in index and 'id="backup-cleanup"' in index, "系统设置缺少备份策略或清理入口")
require('find_active_account_conflicts' in task_repository, "缺少 OCI 租户级任务冲突检测")
require('find_active_account_conflicts' in instance_batch_service and 'find_active_account_conflicts' in (ROOT / "backend/app/task_service.py").read_text(encoding="utf-8"), "账户检测与实例操作未接入冲突保护")
ok("task export, diagnostics bundle, backup policy and OCI conflict guard")


# V1.0.2 core stability retained
oci_resilience = (ROOT / "backend/app/oci_resilience.py").read_text(encoding="utf-8")
backup_schedule_service = (ROOT / "backend/app/backup_schedule_service.py").read_text(encoding="utf-8")
backup_restore_service = (ROOT / "backend/app/backup_restore_service.py").read_text(encoding="utf-8")
release_service = (ROOT / "backend/app/release_service.py").read_text(encoding="utf-8")
schema_migrations = (ROOT / "backend/app/schema_migrations.py").read_text(encoding="utf-8")
require('idempotency_key' in task_repository and 'request_fingerprint' in task_repository, "任务缺少幂等键或请求指纹")
require('find_duplicate_task' in task_repository and 'deduplicated' in task_repository, "任务缺少重复提交识别")
require('classify_oci_error' in oci_resilience and 'adaptive_oci_call' in oci_resilience, "缺少 OCI 错误分类或自适应退避")
for category in ("RATE_LIMIT", "AUTHENTICATION", "PERMISSION", "CAPACITY", "NETWORK", "SERVICE"):
    require(category in oci_resilience, f"OCI 错误分类缺少：{category}")
require('"REPLACE_PUBLIC_IP": 1' in instance_batch_service, "换公网 IP 未保留禁止自动重试保护")
require('id="backup-schedule-form"' in index and '/system/backups/schedule' in main_py, "缺少自动本地备份设置")
require('backup_scheduler_loop' in main_py and 'OCI has no background scheduler' in main_py, "本地备份调度器或禁止后台 OCI 说明缺失")
require('run_backup_restore_drill' in backup_restore_service and '/restore-drill' in main_py, "缺少备份恢复演练")
require('data-backup-drill' in app_js and 'function restoreDrill' in app_js, "恢复演练未接入前端")
require('system_upgrade_history' in schema_migrations and 'backup_restore_drills' in schema_migrations, "schema 4 缺少升级或恢复演练记录")
require('id="release-current"' in index and '/system/release/history' in main_py, "缺少版本与升级记录页面")
require('current_release_info' in release_service and 'record_release_event' in release_service, "版本记录服务不完整")
manifest = json.loads((ROOT / "backend/app/release_manifest.json").read_text(encoding="utf-8"))
require(manifest.get("version") == "1.0.4", "发布清单版本不正确")
require(bool(re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("source_fingerprint") or ""))), "发布清单源码校验值无效")
require('source_fingerprint.py' in (ROOT / "deploy/atomic-upgrade-v1.0.4.sh").read_text(encoding="utf-8"), "原子升级器未重新计算源码指纹")
require('int(row[0]) < 6' in (ROOT / "deploy/atomic-upgrade-v1.0.4.sh").read_text(encoding="utf-8"), "原子升级器未校验 schema 6")
require('--network none' in (ROOT / "deploy/atomic-upgrade-v1.0.4.sh").read_text(encoding="utf-8"), "Web 候选检查未保留无 veth 修复")
ok("V1.0.2 capabilities retained in V1.0.4")



# V1.0.3 final stability: low-memory upgrades, formal restore, safe recovery,
# login sessions and system resource protection.
auth_sessions_service = (ROOT / "backend/app/auth_session_service.py").read_text(encoding="utf-8")
database_restore_service = (ROOT / "backend/app/database_restore_service.py").read_text(encoding="utf-8")
task_recovery_service = (ROOT / "backend/app/task_recovery_service.py").read_text(encoding="utf-8")
resource_service = (ROOT / "backend/app/system_resource_service.py").read_text(encoding="utf-8")
maintenance_state = (ROOT / "backend/app/maintenance_state.py").read_text(encoding="utf-8")
atomic_v104 = (ROOT / "deploy/atomic-upgrade-v1.0.4.sh").read_text(encoding="utf-8")
require("CREATE TABLE IF NOT EXISTS auth_sessions" in schema_migrations, "schema 5 缺少登录会话表")
require("CREATE TABLE IF NOT EXISTS login_attempts" in schema_migrations, "schema 5 缺少登录尝试表")
require("CREATE TABLE IF NOT EXISTS database_restore_history" in schema_migrations, "schema 5 缺少正式恢复历史")
for column in ("token_version", "failed_login_count", "locked_until"):
    require(column in schema_migrations, f"用户安全字段缺少：{column}")
for column in ("recovery_state", "recovery_note", "observed_state"):
    require(column in schema_migrations, f"中断任务恢复字段缺少：{column}")
require("LOCK_THRESHOLD = 5" in auth_sessions_service and "validate_access_token" in auth_sessions_service, "登录锁定或会话验证不完整")
require('/auth/sessions' in main_py and '/auth/login-attempts' in main_py and '/auth/logout' in main_py, "会话管理 API 不完整")
require('id="session-list"' in index and 'id="session-logout-others"' in index, "系统设置缺少登录会话管理")
require('data-session-revoke' in app_js and 'loadAuthSessions' in app_js, "前端会话注销逻辑不完整")
require("restore_database_backup" in database_restore_service and "automatic_rollback" in database_restore_service, "正式数据库恢复或自动回滚缺失")
require('/system/backups/{{backup_name}}/restore' in main_py and '/system/database/restores' in main_py, "正式恢复 API 不完整")
require('data-backup-restore' in app_js and 'function formalRestoreBackup' in app_js, "正式恢复未接入前端")
require("preview_safe_resume" in task_recovery_service and "resume_task_safely" in task_recovery_service, "中断任务安全恢复服务不完整")
require('REPLACE_PUBLIC_IP' in task_recovery_service and 'MANUAL_ONLY' in task_recovery_service, "换公网 IP 中断任务未强制人工处理")
require('/safe-resume-preview' in main_py and '/safe-resume' in main_py, "中断任务安全恢复 API 不完整")
require('id="task-safe-resume"' in index and 'safeResumeCurrentTask' in app_js, "安全恢复未接入任务中心")
require("assert_task_resources" in resource_service and "assert_backup_resources" in resource_service, "系统资源保护服务不完整")
require('/system/resources' in main_py and '/system/resources/limits' in main_py, "系统资源保护 API 不完整")
require('id="resource-limit-form"' in index and 'id="resource-status"' in index, "系统设置缺少资源保护面板")
require("DATABASE_RESTORE" in maintenance_state or "begin(operation" in maintenance_state, "数据库维护状态保护缺失")
require("--low-memory" in atomic_v104 and "--normal-memory" in atomic_v104, "原子升级器缺少低内存模式")
require("LOW_MEMORY_MODE" in atomic_v104 and "cleanup_stale_v104_candidates" in atomic_v104, "低内存资源判断或候选清理缺失")
require("--network none" in atomic_v104 and "--network host" in atomic_v104, "候选验证未避免 bridge/veth")
require("DATABASE_SCHEMA_OK version=6" in atomic_v104 and "[1, 2, 3, 4, 5, 6]" in atomic_v104, "原子升级器未验证 schema 6")
ok("V1.0.3 low-memory upgrade, formal restore, safe task recovery, sessions and resource protection")


# V1.0.4 local system monitoring: host metrics, traffic history, charts,
# isolated read-only container health and transition-only alerts.
monitor_service = (ROOT / "backend/app/system_monitor_service.py").read_text(encoding="utf-8")
monitor_agent = (ROOT / "backend/app/monitor_agent.py").read_text(encoding="utf-8")
atomic_v104 = (ROOT / "deploy/atomic-upgrade-v1.0.4.sh").read_text(encoding="utf-8")
for table in ("system_metrics", "system_alert_states", "system_alert_events"):
    require(f"CREATE TABLE IF NOT EXISTS {table}" in schema_migrations, f"schema 6 缺少监控表：{table}")
for element_id in (
    "dashboard-monitor-title", "monitor-cpu", "monitor-memory", "monitor-disk",
    "monitor-network", "monitor-month-traffic", "monitor-chart-cpu",
    "monitor-chart-memory", "monitor-chart-network", "monitor-container-list",
    "monitor-alert-list", "monitor-settings-form", "monitor-interface-select",
    "telegram-system-resource",
):
    require(f'id="{element_id}"' in index, f"运行监控页面缺少：{element_id}")
for route in (
    "/system/monitor/current", "/system/monitor/history", "/system/monitor/history.csv",
    "/system/monitor/settings", "/system/monitor/traffic/reset",
    "/system/monitor/alerts", "/system/monitor/history/cleanup",
):
    require(route in main_py, f"运行监控 API 缺少：{route}")
require("monitor_scheduler_loop" in main_py and "if not settings.candidate_mode" in main_py, "本地指标调度器未受候选模式保护")
require("HOST_PROC" in monitor_service and "cpuinfo" in monitor_service and "meminfo" in monitor_service and "net/dev" in monitor_service, "宿主机资源采集不完整")
require("collect_metric" in monitor_service and "def history(" in monitor_service and "def export_history_csv(" in monitor_service, "指标采集、历史或 CSV 导出不完整")
require("lo" in monitor_service and "veth" in monitor_service and "docker" in monitor_service, "虚拟网卡排除规则不完整")
require("system_resource" in (ROOT / "backend/app/settings_repository.py").read_text(encoding="utf-8"), "Telegram 缺少系统资源分类")
require("ALERT" in monitor_service and "RECOVERED" in monitor_service, "资源告警缺少异常/恢复状态")
require("do_POST" in monitor_agent and '405' in monitor_agent and 'read only' in monitor_agent, "监控代理未限制为只读")
require("/var/run/docker.sock:/var/run/docker.sock:ro" in compose_text, "Docker Socket 未只读挂载")
require("/proc:/host/proc:ro" in compose_text and "/sys:/host/sys:ro" in compose_text, "宿主机 Proc/Sys 未只读挂载")
require(services.get("monitor", {}).get("container_name") == "oci-nt-monitor-agent", "监控代理容器名称不正确")
require("CANDIDATE_MODE=1" in atomic_v104, "候选 API 未关闭本地监控调度")
require("docker compose stop web api" in atomic_v104 and "docker rm -f oci-nt-monitor-agent" in atomic_v104, "切换时未兼容停止监控代理")
require("wait_monitor" in atomic_v104 and "DOCKER_GID" in atomic_v104, "升级器未验证监控代理或 Docker Socket 组")
require("DATABASE_SCHEMA_OK version=6" in atomic_v104 and "[1, 2, 3, 4, 5, 6]" in atomic_v104, "升级器未验证 schema 6")
require("startDashboardMonitor" in app_js and "renderMonitorHistory" in app_js and "exportMonitorCsv" in app_js, "仪表盘实时刷新、趋势或导出不完整")
require("state.monitorTimer = setInterval" in app_js and "}, 10000);" in app_js, "实时监控刷新间隔不是 10 秒")
require("state.monitorHistoryTimer = setInterval" in app_js and "}, 60000);" in app_js, "历史趋势刷新间隔不是 60 秒")
ok("V1.0.4 system cards, persistent traffic, trends, container health, alerts and CSV export")

if errors:
    print("STATIC_CONTRACTS_FAILED")
    for item in errors:
        print(f"- {item}")
    raise SystemExit(1)

print(f"STATIC_CONTRACTS_OK {len(checks)}")
for item in checks:
    print(f"- {item}")

