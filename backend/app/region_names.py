REGION_DISPLAY_NAMES = {
    "ap-singapore-1": "新加坡",
    "ap-singapore-2": "新加坡西部",
    "ap-tokyo-1": "日本东京",
    "ap-osaka-1": "日本大阪",
    "ap-seoul-1": "韩国首尔",
    "ap-chuncheon-1": "韩国春川",
    "ap-sydney-1": "澳大利亚悉尼",
    "ap-melbourne-1": "澳大利亚墨尔本",
    "ap-mumbai-1": "印度孟买",
    "ap-hyderabad-1": "印度海得拉巴",
    "ap-delhi-1": "印度德里",
    "ap-batam-1": "印度尼西亚巴淡岛",
    "ap-kulai-1": "马来西亚古来",
    "ap-kulai-2": "马来西亚古来 2",
    "us-ashburn-1": "美国阿什本",
    "us-phoenix-1": "美国凤凰城",
    "us-sanjose-1": "美国圣何塞",
    "us-chicago-1": "美国芝加哥",
    "ca-toronto-1": "加拿大多伦多",
    "ca-montreal-1": "加拿大蒙特利尔",
    "uk-london-1": "英国伦敦",
    "uk-cardiff-1": "英国卡迪夫",
    "uk-newport-1": "英国纽波特",
    "eu-frankfurt-1": "德国法兰克福",
    "eu-zurich-1": "瑞士苏黎世",
    "eu-amsterdam-1": "荷兰阿姆斯特丹",
    "eu-marseille-1": "法国马赛",
    "eu-milan-1": "意大利米兰",
    "eu-turin-1": "意大利都灵",
    "eu-madrid-1": "西班牙马德里",
    "eu-stockholm-1": "瑞典斯德哥尔摩",
    "eu-paris-1": "法国巴黎",
    "me-jeddah-1": "沙特阿拉伯吉达",
    "me-dubai-1": "阿联酋迪拜",
    "me-abudhabi-1": "阿联酋阿布扎比",
    "sa-saopaulo-1": "巴西圣保罗",
    "sa-vinhedo-1": "巴西维涅杜",
    "sa-santiago-1": "智利圣地亚哥",
    "sa-bogota-1": "哥伦比亚波哥大",
    "mx-queretaro-1": "墨西哥克雷塔罗",
    "mx-monterrey-1": "墨西哥蒙特雷",
    "af-johannesburg-1": "南非约翰内斯堡",
    "il-jerusalem-1": "以色列耶路撒冷",
}

# OCI list_region_subscriptions may provide a short region_key (for example PHX)
# while the user's Config provides the canonical region code. Keep both forms
# recognizable so account names never fall back to an “unrecognized region”.
REGION_KEY_TO_CODE = {
    "AMS": "eu-amsterdam-1",
    "ARN": "eu-stockholm-1",
    "AUH": "me-abudhabi-1",
    "BBI": "ap-batam-1",
    "BOG": "sa-bogota-1",
    "BOM": "ap-mumbai-1",
    "CWL": "uk-cardiff-1",
    "DXB": "me-dubai-1",
    "FRA": "eu-frankfurt-1",
    "GRU": "sa-saopaulo-1",
    "HYD": "ap-hyderabad-1",
    "IAD": "us-ashburn-1",
    "ICN": "ap-seoul-1",
    "JED": "me-jeddah-1",
    "JNB": "af-johannesburg-1",
    "KIX": "ap-osaka-1",
    "KUL": "ap-kulai-1",
    "LHR": "uk-london-1",
    "MAD": "eu-madrid-1",
    "MEL": "ap-melbourne-1",
    "MRS": "eu-marseille-1",
    "MTY": "mx-monterrey-1",
    "NRT": "ap-tokyo-1",
    "ORD": "us-chicago-1",
    "PHX": "us-phoenix-1",
    "QRO": "mx-queretaro-1",
    "SCL": "sa-santiago-1",
    "SIN": "ap-singapore-1",
    "SJC": "us-sanjose-1",
    "SYD": "ap-sydney-1",
    "VCP": "sa-vinhedo-1",
    "XSP": "ap-singapore-2",
    "ZRH": "eu-zurich-1",
}

REGION_NAME_ALIASES = {
    "phoenix": "美国凤凰城",
    "ashburn": "美国阿什本",
    "san jose": "美国圣何塞",
    "chicago": "美国芝加哥",
    "london": "英国伦敦",
    "cardiff": "英国卡迪夫",
    "newport": "英国纽波特",
    "singapore": "新加坡",
    "tokyo": "日本东京",
    "osaka": "日本大阪",
    "seoul": "韩国首尔",
    "sydney": "澳大利亚悉尼",
    "melbourne": "澳大利亚墨尔本",
    "frankfurt": "德国法兰克福",
    "amsterdam": "荷兰阿姆斯特丹",
    "zurich": "瑞士苏黎世",
    "madrid": "西班牙马德里",
    "paris": "法国巴黎",
}


def _display_for_candidate(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.lower()
    if normalized in REGION_DISPLAY_NAMES:
        return REGION_DISPLAY_NAMES[normalized]
    key_code = REGION_KEY_TO_CODE.get(text.upper())
    if key_code:
        return REGION_DISPLAY_NAMES.get(key_code, key_code)
    alias = REGION_NAME_ALIASES.get(normalized)
    if alias:
        return alias
    return None


def get_region_display_name(
    region_name: str | None,
    region_key: str | None = None,
    *,
    configured_region: str | None = None,
) -> str:
    for candidate in (region_name, configured_region, region_key):
        display = _display_for_candidate(candidate)
        if display:
            return display

    # A syntactically valid canonical code is still better than a generic or
    # misleading “unrecognized region” label. This also keeps new OCI regions
    # usable before their Chinese mapping is added.
    configured = str(configured_region or region_name or "").strip().lower()
    if configured and configured.count("-") >= 2:
        return configured

    key = str(region_key or "").strip().upper()
    if key:
        return key
    return "OCI账户"
