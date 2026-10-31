"""D28: the key never reaches stdout, stderr or any output file.

Every failure path runs with a fake key that has characters which change
under URL encoding, so both the raw and encoded forms are checked.
"""

import contextlib
import io
import os
import tempfile
import unittest
import urllib.parse

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY, RecordingTransport, is_rtms, ok, page_body, raw_row, rtms_body, rtms_item, with_rtms

from collector import collect
from collector.http_client import Response

FORMS = {FAKE_KEY, urllib.parse.quote(FAKE_KEY, safe=""), urllib.parse.quote_plus(FAKE_KEY)}
FORBIDDEN = [*FORMS, "serviceKey="]


def _raise(url, i):
    raise OSError(f"connection to {url} refused")   # the URL holds the key


def _echo_url(url, i):
    return Response(status=500, body=f"<error>{url}</error>".encode())


FAILURES = {
    "unregistered key 403": lambda url, i: Response(status=403, body=open(
        netguard.fixture_path("raw", "error_onbid_fake_key.json"), "rb").read()),
    "missing parameter": lambda url, i: ok(open(netguard.fixture_path("raw", "error_onbid_no_prptDivCd.json"),
                                                "rb").read()),
    "server error echoing the URL": _echo_url,
    "transport exception with URL": _raise,
    "redirect": lambda url, i: Response(status=302, body=url.encode()),
    "not JSON": lambda url, i: ok(b"<html>" + url.encode()),
    "DOCTYPE": lambda url, i: ok(b"<!DOCTYPE x>"),
    "oversized": lambda url, i: ok(b"x" * (5 * 1024 * 1024)),
    "short page": lambda url, i: ok(page_body([raw_row()], 5)),
}


def _onbid_ok(url, i):
    return ok(page_body([raw_row(cltrBidEndDt="202611111700")], 1))


# Phase 3: the Onbid list succeeds and every MOLIT call fails this way.
RTMS_FAILURES = {
    "rtms unregistered key 403": lambda url, i: Response(status=403, body=open(
        netguard.fixture_path("raw", "error_rtms_rh_fake_key.xml"), "rb").read()),
    "rtms server error echoing the URL": _echo_url,
    "rtms transport exception with URL": _raise,
    "rtms redirect": lambda url, i: Response(status=302, body=url.encode()),
    "rtms not XML": lambda url, i: ok(b"<html>" + url.encode()),
    "rtms DOCTYPE": lambda url, i: ok(rtms_body([]).replace(b"<response>", b"<!DOCTYPE r><response>")),
    "rtms BOM": lambda url, i: ok(b"\xef\xbb\xbf" + rtms_body([])),
    "rtms UTF-16-LE DOCTYPE": lambda url, i: ok(('<!DOCTYPE r [<!ENTITY a "' + url + '">]><response>&a;</response>')
                                                .encode("utf-16-le")),
    "rtms UTF-16-BE DOCTYPE": lambda url, i: ok(('<!DOCTYPE r [<!ENTITY a "' + url + '">]><response>&a;</response>')
                                                .encode("utf-16-be")),
    "rtms other encoding": lambda url, i: ok(rtms_body([]).replace(b"utf-8", b"euc-kr")),
    "rtms oversized": lambda url, i: ok(rtms_body([]) + b" " * (2 * 1024 * 1024)),
    "rtms short page": lambda url, i: ok(rtms_body([rtms_item()], total=5)),
    "rtms result code with URL": lambda url, i: ok(rtms_body([]).replace(
        b"<resultCode>000", b"<resultCode>" + url.encode())),
}


class SecretLeakTest(unittest.TestCase):
    def run_case(self, handler, env):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "out")
            stdout, stderr = io.StringIO(), io.StringIO()
            transport = RecordingTransport(handler)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = collect.main(["collect", "--out", out, "--first-run", "--now", "2026-11-01T20:10:00Z"],
                                    env=env, transport=transport, sleep=lambda seconds: None)
            files = b"".join(open(os.path.join(r, n), "rb").read()
                             for r, _, names in os.walk(root) for n in names)
            return code, stdout.getvalue(), stderr.getvalue(), files, transport

    def test_every_failure_path_keeps_the_key_out(self):
        for name, handler in FAILURES.items():
            with self.subTest(name):
                code, out, err, files, transport = self.run_case(handler, {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY})
                self.assertEqual(code, 1)
                self.assertTrue(transport.urls, "the request was really made with the key")
                self.assertTrue(any(form in transport.urls[0] for form in FORMS))
                for text in FORBIDDEN:
                    self.assertNotIn(text, out)
                    self.assertNotIn(text, err)
                    self.assertNotIn(text.encode(), files)

    def test_every_rtms_failure_path_fails_the_run_and_keeps_the_key_out(self):
        for name, failing in RTMS_FAILURES.items():
            with self.subTest(name):
                code, out, err, files, transport = self.run_case(with_rtms(_onbid_ok, failing),
                                                                 {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY})
                self.assertEqual(code, 1)
                self.assertIn("real-transaction months failed", err)
                rtms_urls = [url for url in transport.urls if is_rtms(url)]
                self.assertTrue(rtms_urls and any(form in rtms_urls[0] for form in FORMS))
                for text in FORBIDDEN:
                    self.assertNotIn(text, out)
                    self.assertNotIn(text, err)
                    self.assertNotIn(text.encode(), files)

    def test_rtms_daily_limit_429_stops_quietly_without_the_key(self):
        code, out, err, files, _ = self.run_case(with_rtms(_onbid_ok, lambda url, i: Response(429, url.encode())),
                                                 {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY})
        self.assertEqual(code, 0)
        self.assertIn("blocked=quota", out)
        for text in FORBIDDEN:
            self.assertNotIn(text, out + err)
            self.assertNotIn(text.encode(), files)

    def test_success_output_has_no_key(self):
        code, out, err, files, transport = self.run_case(with_rtms(_onbid_ok, lambda url, i: ok(rtms_body(
            [rtms_item(sggCd="11110", umdNm="청운동", jibun="12-3")]))), {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY})
        self.assertEqual(code, 0)
        self.assertTrue(any(is_rtms(url) for url in transport.urls))
        for text in FORBIDDEN:
            self.assertNotIn(text, out + err)
            self.assertNotIn(text.encode(), files)

    def test_on_actions_only_mask_lines_contain_the_key(self):
        env = {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY, "GITHUB_ACTIONS": "true"}
        code, out, err, files, _ = self.run_case(FAILURES["server error echoing the URL"], env)
        self.assertEqual(code, 1)
        lines = (out + err).splitlines()
        masks = [line for line in lines if line.startswith("::add-mask::")]
        self.assertEqual({line[len("::add-mask::"):] for line in masks}, FORMS)
        for line in lines:
            if not line.startswith("::add-mask::"):
                for text in FORBIDDEN:
                    self.assertNotIn(text, line)
        self.assertEqual(lines[0][:12], "::add-mask::")

    def test_fixture_files_carry_no_key_parameter(self):
        for root, _, names in os.walk(netguard.FIXTURES):
            for name in names:
                with open(os.path.join(root, name), "rb") as handle:
                    self.assertNotIn(b"serviceKey=", handle.read(), name)


if __name__ == "__main__":
    unittest.main()
