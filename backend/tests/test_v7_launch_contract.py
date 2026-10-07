import base64
import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="oci-nt-v7-launch-")
_DATA_DIR = Path(_TEMP_DIR.name)
os.environ.setdefault("SECRET_KEY", "v7-launch-test")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"9" * 32).decode())
os.environ.setdefault("ADMIN_PASSWORD", "x")
os.environ["DATA_DIR"] = str(_DATA_DIR)
os.environ["DB_PATH"] = str(_DATA_DIR / "oci-nt.db")
os.environ["BACKUP_DIR"] = str(_DATA_DIR / "backups")

from app.database import init_database, database  # noqa: E402
from app.launch_repository import (  # noqa: E402
    create_catalog_snapshot, get_catalog_snapshot, save_catalog_resources,
    get_catalog_resources, create_launch_job, request_launch_cancel, get_launch_job,
)


class V7LaunchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()
        with database() as connection:
            cursor = connection.execute(
                """INSERT INTO oci_accounts (custom_name, tenancy_ocid, user_ocid, fingerprint, region, private_key_encrypted) VALUES (?, ?, ?, ?, ?, ?)""",
                ("tenant", "ocid1.tenancy.oc1..x", "ocid1.user.oc1..x", "aa", "uk-london-1", "encrypted"),
            )
            cls.account_id = int(cursor.lastrowid)

    def test_catalog_snapshot_is_account_scoped(self):
        token = create_catalog_snapshot(
            account_id=self.account_id, region="uk-london-1",
            compartment_ids=["ocid1.compartment.oc1..x"],
            availability_domains=["AD-1"],
        )
        snapshot = get_catalog_snapshot(token)
        self.assertEqual(snapshot["account_id"], self.account_id)
        self.assertEqual(snapshot["compartment_ids"], ["ocid1.compartment.oc1..x"])
        save_catalog_resources(
            token=token, compartment_id="ocid1.compartment.oc1..x",
            availability_domain="AD-1", subnet_ids=["ocid1.subnet.oc1..x"],
            image_ids=["ocid1.image.oc1..x"], shape_names=["VM.Standard.A1.Flex"],
        )
        resources = get_catalog_resources(token, "ocid1.compartment.oc1..x", "AD-1")
        self.assertIn("ocid1.subnet.oc1..x", resources["subnet_ids"])

    def test_only_one_active_launch_job_per_account(self):
        payload = {"display_name": "N&T", "region": "uk-london-1"}
        job = create_launch_job(
            account_id=self.account_id, requested_by="admin", mode="CREATE",
            request_payload=payload, requested_count=1, max_attempts=1,
            retry_interval_seconds=30, concurrency=1,
        )
        self.assertEqual(job["status"], "PENDING")
        with self.assertRaises(RuntimeError):
            create_launch_job(
                account_id=self.account_id, requested_by="admin", mode="CREATE",
                request_payload=payload, requested_count=1, max_attempts=1,
                retry_interval_seconds=30, concurrency=1,
            )
        self.assertTrue(request_launch_cancel(job["id"], self.account_id))
        self.assertTrue(get_launch_job(job["id"])["cancel_requested"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
