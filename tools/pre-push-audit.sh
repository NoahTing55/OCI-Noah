#!/usr/bin/env bash
set -Eeuo pipefail
cd /opt/oci-nt

FAIL=0
bad(){ echo "BLOCK: $*"; FAIL=1; }

for p in .env data logs backups backup; do
  if git ls-files "$p" "$p/*" 2>/dev/null | grep -q .; then
    bad "$p 被 Git 跟踪"
  fi
done

tracked_bad="$(git ls-files | grep -Ei '(^|/)(id_rsa|id_ed25519|.*private.*key.*|oci.*key.*)$|\.(pem|p12|pfx|jks|db|sqlite|sqlite3)$' || true)"
if [[ -n "$tracked_bad" ]]; then
  echo "$tracked_bad"
  bad "发现不应跟踪的密钥/数据库文件"
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo "PRE_PUSH_AUDIT_OK"
else
  echo "PRE_PUSH_AUDIT_BLOCKED"
  exit 1
fi
