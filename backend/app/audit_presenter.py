import ipaddress
import re


ACTION_LABELS = {
    "LOGIN_SUCCESS": "登录成功",
    "LOGIN_FAILED": "登录失败",
    "PASSWORD_CHANGED": "修改管理员密码",
    "OCI_ACCOUNT_CREATED": "添加 OCI 账户",
    "OCI_ACCOUNT_DELETED": "删除 OCI 账户",
    "OCI_ACCOUNT_CHECKED": "检测 OCI 账户",
    "OCI_ACCOUNTS_BULK_CHECKED": "批量检测 OCI 账户",
    "OCI_ACCOUNTS_SCHEDULED_CHECKED": "定时检测 OCI 账户",
    "OCI_ACCOUNTS_SCHEDULE_FAILED": "OCI 定时检测失败",
    "OCI_ACCOUNT_IMPORT_FAILED": "OCI 账户导入失败",
    "OCI_ACCOUNT_PROXY_UPDATED": "更新账户代理",
    "OCI_ACCOUNT_PROXY_TESTED": "测试账户代理",
    "OCI_INSTANCE_ACTION": "操作 OCI 实例",
    "OCI_REGION_SUBSCRIBED": "订阅 OCI 区域",
    "OCI_PUBLIC_IP_REPLACED": "更换临时公网 IP",
    "OCI_VNIC_UPDATED": "更新 VNIC 配置",
    "TELEGRAM_SETTINGS_UPDATED": "更新 Telegram 设置",
    "TELEGRAM_TEST_SENT": "发送 Telegram 测试",
    "MONITOR_SETTINGS_UPDATED": "更新自动检测设置",
    "CLOUDFLARE_ACCOUNT_CREATED": "添加 Cloudflare 账户",
    "CLOUDFLARE_ACCOUNT_IMPORT_FAILED": "Cloudflare 账户添加失败",
    "CLOUDFLARE_ACCOUNT_TESTED": "测试 Cloudflare 账户",
    "CLOUDFLARE_ACCOUNT_DELETED": "删除 Cloudflare 账户",
    "CLOUDFLARE_ZONES_SYNCED": "同步 Cloudflare 域名",
    "CLOUDFLARE_DNS_CREATED": "新增 DNS 记录",
    "CLOUDFLARE_DNS_UPDATED": "更新 DNS 记录",
    "CLOUDFLARE_DNS_DELETED": "删除 DNS 记录",
    "SYSTEM_BACKUP_CREATED": "创建系统备份",
    "SYSTEM_BACKUP_VERIFIED": "验证系统备份",
    "SYSTEM_BACKUP_DELETED": "删除系统备份",
}

RESOURCE_LABELS = {
    "USER": "管理员",
    "OCI_ACCOUNT": "OCI 账户",
    "OCI_INSTANCE": "OCI 实例",
    "OCI_REGION": "OCI 区域",
    "OCI_PUBLIC_IP": "OCI 公网 IP",
    "OCI_VNIC": "OCI VNIC",
    "SYSTEM_SETTING": "系统设置",
    "CLOUDFLARE_ACCOUNT": "Cloudflare 账户",
    "CLOUDFLARE_ZONE": "Cloudflare 域名",
    "CLOUDFLARE_DNS": "DNS 记录",
    "SYSTEM_BACKUP": "系统备份",
    "SYSTEM_MONITOR": "自动检测",
}


def _format_bulk_detail(detail: str) -> str | None:
    matches = dict(re.findall(r"(total|alive|abnormal|unknown)=([^,]+)", detail))
    if not matches:
        return None
    return (
        f"共 {matches.get('total', '0')} 个，有效 {matches.get('alive', '0')} 个，"
        f"异常 {matches.get('abnormal', '0')} 个，待检测 {matches.get('unknown', '0')} 个"
    )


def format_detail(action: str, detail: str | None) -> str:
    value = str(detail or "").strip()
    if not value:
        return "—"

    if action in ("OCI_ACCOUNTS_BULK_CHECKED", "OCI_ACCOUNTS_SCHEDULED_CHECKED"):
        return _format_bulk_detail(value) or value

    if action == "MONITOR_SETTINGS_UPDATED":
        fields = dict(re.findall(r"(enabled|interval|notify)=([^,]+)", value))
        if fields:
            enabled = "已启用" if fields.get("enabled") == "True" else "已停用"
            return (
                f"{enabled}，间隔 {fields.get('interval', '未知')} 分钟，"
                f"通知模式：{fields.get('notify', '未知')}"
            )

    if action == "TELEGRAM_SETTINGS_UPDATED":
        return "通知已启用" if "enabled=True" in value else "通知已停用"

    if action == "OCI_ACCOUNT_PROXY_UPDATED":
        enabled = "已启用" if "enabled=True" in value else "已停用"
        label_match = re.search(r"label=([^,]*)", value)
        label = label_match.group(1).strip() if label_match else ""
        return f"{enabled}" + (f"，备注：{label}" if label else "")

    if action == "OCI_ACCOUNT_PROXY_TESTED" and value.startswith("ip="):
        return f"出口 IP：{value[3:]}"

    if action == "OCI_REGION_SUBSCRIBED":
        fields = dict(re.findall(r"(region|status)=([^,]+)", value))
        if fields:
            return (
                f"区域：{fields.get('region', '未知')}，"
                f"状态：{fields.get('status', '未知')}"
            )

    if action == "OCI_PUBLIC_IP_REPLACED":
        fields = dict(re.findall(r"(old|new)=([^,]+)", value))
        if fields:
            return (
                f"原 IP：{fields.get('old', '无')}，"
                f"新 IP：{fields.get('new', '处理中')}"
            )

    if action == "OCI_VNIC_UPDATED":
        fields = dict(
            re.findall(
                r"(name|hostname|skip_source_dest_check)=([^,]*)",
                value,
            )
        )
        if fields:
            check = (
                "已跳过源/目标检查"
                if fields.get("skip_source_dest_check") == "True"
                else "已启用源/目标检查"
            )
            hostname = fields.get("hostname") or "未修改"
            return (
                f"名称：{fields.get('name') or '未修改'}，"
                f"主机标签：{hostname}，{check}"
            )

    if action == "CLOUDFLARE_ZONES_SYNCED" and value.startswith("count="):
        return f"已同步 {value[6:]} 个域名"

    if action == "OCI_ACCOUNT_IMPORT_FAILED" and (
        "NotAuthenticated" in value or value.startswith("401 ")
    ):
        return "OCI API 验证失败：凭据、指纹或私钥签名不正确"

    if len(value) > 320:
        return value[:317] + "..."

    return value


def format_ip(ip_address: str | None) -> str:
    value = str(ip_address or "").strip()
    if not value or value.upper() == "LOCAL":
        return "本机访问"

    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return value

    if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
        return "本机/内网"
    return parsed.compressed


def present_audit_log(row: dict) -> dict:
    result = dict(row)
    action = str(result.get("action") or "")
    resource_type = str(result.get("resource_type") or "")
    resource_id = result.get("resource_id")
    resource_label = RESOURCE_LABELS.get(resource_type, resource_type or "—")

    if resource_id:
        resource_label += f" #{resource_id}"

    result["action_label"] = ACTION_LABELS.get(action, action or "未知操作")
    result["resource_label"] = resource_label
    result["detail_label"] = format_detail(action, result.get("detail"))
    result["ip_label"] = format_ip(result.get("ip_address"))
    return result
