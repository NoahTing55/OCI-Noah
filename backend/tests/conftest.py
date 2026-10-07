from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="oci-nt-v103-pytest-"))
os.environ.setdefault("SECRET_KEY", "oci-nt-v103-test-secret-key-32bytes")
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"v" * 32).decode("ascii"),
)
os.environ.setdefault("ADMIN_USERNAME", "Noah")
os.environ.setdefault("ADMIN_PASSWORD", "x")
os.environ.setdefault("DATA_DIR", str(_TEST_ROOT))
os.environ.setdefault("DB_PATH", str(_TEST_ROOT / "oci-nt.db"))
os.environ.setdefault("BACKUP_DIR", str(_TEST_ROOT / "backups"))
