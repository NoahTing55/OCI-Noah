import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class TypographyScaleTests(unittest.TestCase):
    def test_readable_global_scale_and_hierarchy(self):
        css = (ROOT / "frontend/ui-theme.css").read_text()
        section = css.split("/* 2026-10 accessibility:", 1)[1]
        for text in (".sidebar .nav", ".nav-group-title", ".launch-section-title small", ".launch-env-chips small", ".panel-head small", ".launch-table-all-tenants th", ".badge", "@media (max-width: 760px)"):
            self.assertIn(text, section)
        self.assertIn("font-size: 14px !important", section)
        self.assertIn("font-size: 13px !important", section)
        self.assertIn("font-weight: 400 !important", section)
        self.assertIn("font-weight: 600 !important", section)

if __name__ == "__main__":
    unittest.main()
