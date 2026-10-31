import io
import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY

from collector import key_guard
from collector.errors import ConfigError


class KeyGuardTest(unittest.TestCase):
    def test_reads_key_from_env_only(self):
        self.assertEqual(key_guard.read_key({"DATA_GO_KR_SERVICE_KEY": FAKE_KEY}), FAKE_KEY)
        with self.assertRaises(ConfigError):
            key_guard.read_key({})
        with self.assertRaises(ConfigError):
            key_guard.read_key({"DATA_GO_KR_SERVICE_KEY": "  "})

    def test_key_forms_include_url_encoded(self):
        forms = key_guard.key_forms(FAKE_KEY)
        self.assertIn(FAKE_KEY, forms)
        self.assertIn("FAKE%2Bkey%2Fwith%3Dchars%2541-not-real", forms)
        self.assertIn("FAKE%2Bkey%2Fwith%3Dchars%2541-not-real", forms)

    def test_masks_only_on_github_actions(self):
        out = io.StringIO()
        key_guard.emit_masks(FAKE_KEY, {}, out)
        self.assertEqual(out.getvalue(), "")
        key_guard.emit_masks(FAKE_KEY, {"GITHUB_ACTIONS": "true"}, out)
        lines = out.getvalue().splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("::add-mask::") for line in lines))
        self.assertIn("::add-mask::" + FAKE_KEY, lines)

    def test_redact_removes_every_form_and_key_parameter(self):
        text = f"a {FAKE_KEY} b FAKE%2Bkey%2Fwith%3Dchars%2541-not-real c serviceKey=zzz&x=1"
        cleaned = key_guard.redact(text, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, cleaned)
        self.assertNotIn("FAKE%2B", cleaned)
        self.assertNotIn("serviceKey=", cleaned)


if __name__ == "__main__":
    unittest.main()
