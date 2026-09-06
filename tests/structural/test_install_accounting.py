from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install"

class InstallerAccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INSTALLER.read_text(encoding="utf-8")

    def test_idempotent_install_is_counted_as_ready(self):
        self.assertIn("already_current += 1", self.text)
        self.assertIn("ready = installed + already_current", self.text)

    def test_idempotent_message_says_already_current(self):
        self.assertIn("already current -> {target}", self.text)
        self.assertNotIn("already installed -> {target}", self.text)

    def test_summary_reports_ready_and_changed(self):
        self.assertIn('print(f"✓ {ready} {tool_word} ready ({changed} changed)")', self.text)
        self.assertNotIn("Installed {installed} tool(s).", self.text)

    def test_existing_installed_counter_semantics_are_preserved(self):
        self.assertIn("installed = 0", self.text)
        self.assertIn("changed = installed", self.text)

if __name__ == "__main__":
    unittest.main()
