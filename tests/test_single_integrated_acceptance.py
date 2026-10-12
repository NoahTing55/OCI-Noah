from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"deploy/single/ci-four-single-four.sh"

class IntegratedSwitchContract(unittest.TestCase):
    def test_ci_only_and_no_existing_database(self):
        s=SCRIPT.read_text()
        self.assertIn('RUNNER_ENVIRONMENT:-',s)
        self.assertIn('GITHUB_ACTIONS:-',s)
        self.assertIn('REFUSE_EXISTING_DATABASE',s)
        self.assertIn('trap cleanup EXIT',s)
    def test_real_four_single_four_switch(self):
        s=SCRIPT.read_text()
        self.assertEqual(s.count('docker compose -p oci-nt -f docker-compose.yml up -d --no-build'),3)
        self.assertIn('docker compose -p oci-nt -f docker-compose.yml down',s)
        self.assertIn('docker run -d --name oci-nt-single-ci-switch',s)
        self.assertIn('docker stop --time 35 oci-nt-single-ci-switch',s)
        for marker in ("FOUR_CONTAINER_BASELINE_OK","FOUR_TO_SINGLE_OK","SINGLE_TO_FOUR_ROLLBACK_OK","FAILED_SINGLE_TO_FOUR_RECOVERY_OK","PRAGMA quick_check", "ci_switch_marker"):
            self.assertIn(marker,s)
    def test_served_frontend_bytes_match_before_after_switch(self):
        s=SCRIPT.read_text()
        for asset in ("/app.js", "/rc.js", "/ui2.js", "/styles.css",
                      "/ui-theme.css", "/vnc.html", "/favicon.svg",
                      "/favicon.ico", "/apple-touch-icon.png"):
            self.assertIn(asset,s)
        self.assertIn('capture_ui_hashes > "$DIR/four-ui.sha256"',s)
        self.assertIn('capture_ui_hashes > "$DIR/single-ui.sha256"',s)
        self.assertIn('capture_ui_hashes > "$DIR/restored-ui.sha256"',s)
        self.assertIn('diff -u "$DIR/four-ui.sha256" "$DIR/single-ui.sha256"',s)
        self.assertIn('diff -u "$DIR/four-ui.sha256" "$DIR/restored-ui.sha256"',s)

    def test_openapi_routes_and_schemas_match_both_directions(self):
        s=SCRIPT.read_text()
        self.assertIn("capture_api_contract()",s)
        self.assertIn('capture_api_contract > "$DIR/four-api.json"',s)
        self.assertIn('capture_api_contract > "$DIR/single-api.json"',s)
        self.assertIn('capture_api_contract > "$DIR/restored-api.json"',s)
        self.assertIn('diff -u "$DIR/four-api.json" "$DIR/single-api.json"',s)
        self.assertIn('diff -u "$DIR/four-api.json" "$DIR/restored-api.json"',s)

    def test_synthetic_encrypted_credential_survives_switch_and_rollback(self):
        s=SCRIPT.read_text()
        self.assertIn('from app.credential_crypto import encrypt_secret, decrypt_secret',s)
        self.assertIn('CREATE TABLE ci_encrypted_marker',s)
        self.assertIn('SELECT secret FROM ci_encrypted_marker',s)
        self.assertIn('Fernet(Fernet.generate_key()).decrypt(token.encode("ascii"))',s)
        self.assertIn('except InvalidToken:',s)
        self.assertIn('SINGLE_CONTAINER_SYNTHETIC_CREDENTIAL_READ_OK',s)
        self.assertIn('FOUR_CONTAINER_SYNTHETIC_CREDENTIAL_ROLLBACK_OK',s)
        self.assertNotIn('real-private-key',s)

    def test_stopped_sqlite_snapshot_is_verified_and_retained(self):
        s=SCRIPT.read_text()
        self.assertIn('STOPPED_FOUR_SQLITE_BACKUP_VERIFIED_OK',s)
        self.assertIn('STOPPED_FOUR_BACKUP_RETAINED_AFTER_ROLLBACK_OK',s)
        self.assertIn('src.backup(dst)',s)
        self.assertIn('PRAGMA integrity_check',s)
        self.assertIn('chmod 0400 "$DIR/stopped-four-backup.db"',s)
        self.assertLess(s.index('STOPPED_FOUR_SQLITE_BACKUP_VERIFIED_OK'),
                        s.index('docker run -d --name oci-nt-single-ci-switch'))
        self.assertGreater(s.index('STOPPED_FOUR_BACKUP_RETAINED_AFTER_ROLLBACK_OK'),
                           s.index('echo FOUR_CONTAINER_API_ROLLBACK_PARITY_OK'))

    def test_ci_backup_preserves_api_sqlite_writable_state(self):
        s=SCRIPT.read_text()
        self.assertIn("STOPPED_FOUR_DB_APP_UID_WRITE_PROBE_OK",s)
        self.assertIn("BEGIN IMMEDIATE",s)
        self.assertIn("chown 10001:10001 /app/data/oci-nt.db",s)
        self.assertLess(s.index("STOPPED_FOUR_DB_APP_UID_WRITE_PROBE_OK"),
                        s.index("FAILED_SINGLE_TO_FOUR_RECOVERY_OK"))

    def test_no_real_credentials_or_oci_actions(self):
        s=SCRIPT.read_text()
        self.assertNotIn('/root/oci-nt-backups',s)
        self.assertNotIn('oci launch',s)
        self.assertIn('ci-only-not-a-secret',s)
        self.assertIn('DOCKER_GUARD_HOST=127.0.0.1',s)

if __name__=="__main__":
    unittest.main()
