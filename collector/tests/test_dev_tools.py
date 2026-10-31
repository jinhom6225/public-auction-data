import importlib.util
import os
import threading
import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import RecordingTransport, ok


def load_measure():
    path = os.path.join(netguard.REPO_ROOT, "collector", "dev", "measure_a9.py")
    spec = importlib.util.spec_from_file_location("measure_a9", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MeasureWorkDirTest(unittest.TestCase):
    """Security LOW-6: raw MOLIT responses may only land in the gitignored .local/ (or outside the repo)."""

    def test_work_dir_inside_the_repo_must_be_under_local(self):
        measure = load_measure()
        root = netguard.REPO_ROOT
        for bad in (root, os.path.join(root, "collector"), os.path.join(root, "collector", "tests", "fixtures"),
                    os.path.join(root, ".localx")):
            with self.assertRaises(SystemExit, msg=bad):
                measure.check_work_dir(bad)
        measure.check_work_dir(os.path.join(root, ".local", "a9"))
        measure.check_work_dir(os.path.join(os.path.dirname(root), "elsewhere"))


class RecordingTransportTest(unittest.TestCase):
    """Review LOW-6: the API threads share one fake transport."""

    def test_concurrent_calls_get_unique_indexes(self):
        seen = []
        transport = RecordingTransport(lambda url, i: seen.append(i) or ok(b""))
        threads = [threading.Thread(target=lambda: [transport(f"u{k}", 1, 1) for k in range(200)]) for _ in range(7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(seen), list(range(1400)))
        self.assertEqual(len(transport.urls), 1400)


if __name__ == "__main__":
    unittest.main()
