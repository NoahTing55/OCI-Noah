import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class LaunchNetworkUxTests(unittest.TestCase):
    def test_network_creation_only_after_explicit_submit(self):
        code = (ROOT / "frontend/app.js").read_text()
        submit = code.split("async function submitLaunchJob(event) {", 1)[1].split("async function loadLaunchJobs(", 1)[0]
        self.assertIn('launch/network/ensure', submit)
        self.assertIn('preferredSubnet: ensuredSubnet, forceRefresh: true', submit)
        self.assertIn('if (!ensuredSubnet) throw new Error', submit)
        self.assertLess(submit.index('launch/network/ensure'), submit.index('launch/jobs'))
        ready = code.split("function updateLaunchSubmitState() {", 1)[1].split("function renderAutoNetwork", 1)[0]
        self.assertNotIn('"tenant-launch-subnet")?.value', ready)
        load = code.split("async function loadLaunchResources(options = {}) {", 1)[1].split("async function ensureLaunchNetwork()", 1)[0]
        self.assertNotIn('method: "POST"', load)

    def test_typography_keeps_text_readable(self):
        css = (ROOT / "frontend/ui-theme.css").read_text()
        self.assertIn('readable typography', css)
        self.assertIn('th { font-weight: 500 !important; }', css)
        self.assertIn('td strong,td b) { font-weight: 400 !important; }', css)

if __name__ == "__main__":
    unittest.main()
