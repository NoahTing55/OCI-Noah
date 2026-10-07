import re
from dataclasses import dataclass


class OciConfigParseError(ValueError):
    pass


@dataclass(slots=True)
class ParsedOciConfig:
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    region: str
    private_key_pem: str | None = None
    private_key_passphrase: str | None = None
    key_file_hint: str | None = None


PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?-----END \1-----",
    re.DOTALL,
)


def parse_oci_api_config(raw_text: str) -> ParsedOciConfig:
    text = raw_text.replace("\ufeff", "").strip()
    if not text:
        raise OciConfigParseError("OCI API 配置不能为空")

    private_key_pem = None
    key_match = PRIVATE_KEY_PATTERN.search(text)
    if key_match:
        private_key_pem = key_match.group(0).strip() + "\n"
        text = text[: key_match.start()] + "\n" + text[key_match.end() :]

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        for marker in (" #", " ;"):
            if marker in value:
                value = value.split(marker, 1)[0].strip()
        values[key] = value.strip('"').strip("'")

    tenancy_ocid = values.get("tenancy", "")
    user_ocid = values.get("user", "")
    fingerprint = values.get("fingerprint", "")
    region = values.get("region", "")
    missing = []
    if not tenancy_ocid:
        missing.append("tenancy")
    if not user_ocid:
        missing.append("user")
    if not fingerprint:
        missing.append("fingerprint")
    if not region:
        missing.append("region")
    if missing:
        raise OciConfigParseError("OCI 配置缺少字段：" + "、".join(missing))

    return ParsedOciConfig(
        tenancy_ocid=tenancy_ocid,
        user_ocid=user_ocid,
        fingerprint=fingerprint,
        region=region,
        private_key_pem=private_key_pem,
        private_key_passphrase=values.get("pass_phrase") or values.get("passphrase") or None,
        key_file_hint=values.get("key_file"),
    )
