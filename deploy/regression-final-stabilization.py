#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

root=Path(sys.argv[1] if len(sys.argv)>1 else "/opt/oci-nt")
front=root/"frontend"; back=root/"backend/app"
required=[
 root/"docker-compose.yml",back/"main.py",back/"database.py",
 back/"account_read_cache_service.py",back/"backup_integrity_service.py",
 back/"launch_service.py",back/"launch_task_service.py",back/"database_restore_service.py",back/"audit_presenter.py",
 front/"index.html",front/"app.js",front/"ui2.js",front/"styles.css",
]
missing=[str(p.relative_to(root)) for p in required if not p.exists()]
if missing: raise SystemExit("MISSING_FILES: "+", ".join(missing))

main=(back/"main.py").read_text(encoding="utf-8")
cache=(back/"account_read_cache_service.py").read_text(encoding="utf-8")
verify=(back/"backup_integrity_service.py").read_text(encoding="utf-8")
launch=(back/"launch_service.py").read_text(encoding="utf-8")
task=(back/"launch_task_service.py").read_text(encoding="utf-8")
restore=(back/"database_restore_service.py").read_text(encoding="utf-8")
audit_presenter=(back/"audit_presenter.py").read_text(encoding="utf-8")
index=(front/"index.html").read_text(encoding="utf-8")
app=(front/"app.js").read_text(encoding="utf-8")
ui2=(front/"ui2.js").read_text(encoding="utf-8")
css=(front/"styles.css").read_text(encoding="utf-8")
rc=(front/"rc.js").read_text(encoding="utf-8") if (front/"rc.js").exists() else ""

for p in [back/"main.py",back/"account_read_cache_service.py",back/"backup_integrity_service.py"]:
    ast.parse(p.read_text(encoding="utf-8"),filename=str(p))

checks={
 "final frontend marker":"1.0.4-final-stabilization1" in index and "1.0.4-final-stabilization1" in ui2 and "1.0.4-final-stabilization1" in css,
 "SQLite cache API":"/read-cache/{{category}}" in main and "oci_account_read_cache" in cache,
 "cache categories":all(x in cache for x in ("iam","password-policy","identity-domain-password-policy","regions","limits","audit","network","security","boot-volumes","images","vnc","object-storage","metrics","costs","launch-catalog")),
 "cache request key":"cache_key" in cache and "PRIMARY KEY (account_id, category, cache_key)" in cache,
 "backup verify API":"/system/backups/{{backup_name}}/verify" in main and "PRAGMA quick_check" in verify,
 "restore safety retained":"_active_task_count" in restore or "运行中的任务" in restore,
 "Ubuntu launch retained":"Canonical Ubuntu without Shape" in launch and "def _image_matches_architecture" in launch,
 "public IP resolver":"def resolve_instance_public_ip" in launch,
 "SSH key one-time download":"modulusLength: 3072" in app and "ntLaunchDownloadPrivateKey" in app,
 "private key not Telegram":"创建配置时下载的私钥.pem" in task,
 "Telegram user":"用户：ubuntu" in task,
 "Identity Domains retained":"nt-a55-domain-policy" in ui2 and "identity-domain/password-policies" in main,
 "backup verify UI":'id="backup-verify-all"' in index,
 "audit verify label":"SYSTEM_BACKUP_VERIFIED" in audit_presenter and "验证系统备份" in audit_presenter,
}
for name,ok in checks.items(): print("PASS" if ok else "FAIL",name)
bad=[name for name,ok in checks.items() if not ok]
if bad: raise SystemExit("CONTRACT_FAILED: "+", ".join(bad))

ids=re.findall(r'\bid=["\']([^"\']+)["\']',index)
seen=set(); dups=set()
for value in ids:
    if value in seen: dups.add(value)
    seen.add(value)
if dups: raise SystemExit("DUPLICATE_HTML_IDS: "+", ".join(sorted(dups)[:30]))
print("PASS unique HTML ids",len(ids))

retired=["A5.4 PERSISTENT READ CACHE / IAM LAYOUT","1.0.4-iam-layout-policy-a5.5.2","1.0.4-iam-toolbar-membership-a5.5.3","1.0.4-iam-header-final-a5.5.4"]
for marker in retired:
    if marker in ui2 or marker in css: raise SystemExit("RETIRED_MARKER_PRESENT: "+marker)
print("PASS retired A5.4/A5.5.2-A5.5.4 overrides removed")

plus=re.compile(r'[+＋]\s*(?=(?:添加|增加|新增|加入|新建))')
for name,value in [("index.html",index),("app.js",app),("ui2.js",ui2),("rc.js",rc)]:
    if plus.search(value): raise SystemExit("LEADING_PLUS_REMAINS: "+name)
print("PASS leading plus removed from add/create/join labels")

plain=re.sub(r'/\*.*?\*/','',css,flags=re.S)
if plain.count('{')!=plain.count('}'): raise SystemExit("CSS_BRACE_MISMATCH")
print("PASS CSS brace balance")

for needle in ['id="dashboard-page"','id="accounts-page"','id="proxies-page"','id="tasks-page"','id="instances-page"','id="launch-page"','id="cloudflare-page"','id="audit-page"','id="settings-page"','id="tenant-content"']:
    if needle not in index: raise SystemExit("PAGE_CONTRACT_MISSING: "+needle)
print("PASS core page contracts")

final=ui2[ui2.index("/* BEGIN OCI-N&T V1.0.4 1.0.4-final-stabilization1 */"):]
if "setInterval(" in final: raise SystemExit("BACKGROUND_INTERVAL_FORBIDDEN")
print("PASS no background interval")
print("FINAL_STABILIZATION_REGRESSION_OK")
