import unittest
import urllib.parse

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY, FakeClock, RecordingTransport, ok

from collector import http_client
from collector.errors import ApiError
from collector.http_client import ApiClient, Response

URL = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"


def client(transport, clock=None, tps=8):
    clock = clock or FakeClock()
    return ApiClient(transport, FAKE_KEY, monotonic=clock.monotonic, sleep=clock.sleep, tps=tps)


class ApiClientTest(unittest.TestCase):
    def test_sends_key_and_params_and_returns_body(self):
        transport = RecordingTransport(lambda url, i: ok(b'{"a":1}'))
        body = client(transport).get("op", URL, {"pageNo": "1"}, max_bytes=100)
        self.assertEqual(body, b'{"a":1}')
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.urls[0]).query)
        self.assertEqual(query["serviceKey"], [FAKE_KEY])
        self.assertEqual(query["pageNo"], ["1"])

    def test_rate_limit_spaces_calls(self):
        clock = FakeClock()
        transport = RecordingTransport(lambda url, i: ok(b"{}"))
        api = client(transport, clock, tps=8)
        for _ in range(3):
            api.get("op", URL, {}, max_bytes=100)
        self.assertEqual(len(clock.sleeps), 2)
        for pause in clock.sleeps:
            self.assertAlmostEqual(pause, 0.125)

    def test_body_over_cap_is_rejected(self):
        transport = RecordingTransport(lambda url, i: ok(b"x" * 101))
        with self.assertRaises(ApiError) as ctx:
            client(transport).get("op", URL, {}, max_bytes=100)
        self.assertEqual(ctx.exception.reason, "body too large")

    def test_doctype_and_entity_are_rejected(self):
        for body in (b'<?xml version="1.0"?><!DOCTYPE x>', b"<a><!ENTITY e 'x'></a>", b"<!doctype html>"):
            transport = RecordingTransport(lambda url, i, b=body: ok(b))
            with self.assertRaises(ApiError) as ctx:
                client(transport).get("op", URL, {}, max_bytes=1000)
            self.assertEqual(ctx.exception.reason, "DOCTYPE or ENTITY")

    def test_http_error_carries_status_and_result_code_only(self):
        body = (b'{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR",'
                b'"returnReasonCode":"30"}}}')
        transport = RecordingTransport(lambda url, i: Response(status=403, body=body))
        with self.assertRaises(ApiError) as ctx:
            client(transport).get("getRlstCltrList2", URL, {}, max_bytes=1000)
        error = ctx.exception
        self.assertEqual((error.operation, error.status, error.result_code), ("getRlstCltrList2", 403, "30"))
        self.assertEqual(str(error), "getRlstCltrList2 failed: HTTP 403, resultCode 30")

    def test_redirect_status_is_refused(self):
        transport = RecordingTransport(lambda url, i: Response(status=302, body=b""))
        with self.assertRaises(ApiError) as ctx:
            client(transport).get("op", URL, {}, max_bytes=100)
        self.assertEqual(ctx.exception.status, 302)

    def test_transport_exception_text_is_dropped(self):
        def boom(url, i):
            raise OSError(f"cannot reach {url}")

        with self.assertRaises(ApiError) as ctx:
            client(RecordingTransport(boom)).get("op", URL, {}, max_bytes=100)
        self.assertNotIn("serviceKey", str(ctx.exception))
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_refuses_plain_http_and_key_in_params(self):
        transport = RecordingTransport(lambda url, i: ok(b"{}"))
        with self.assertRaises(ValueError):
            client(transport).get("op", "http://apis.data.go.kr/x", {}, max_bytes=100)
        with self.assertRaises(ValueError):
            client(transport).get("op", URL, {"serviceKey": "x"}, max_bytes=100)
        self.assertEqual(transport.urls, [])


class UrllibTransportTest(unittest.TestCase):
    def test_redirect_handler_refuses(self):
        handler = http_client.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))

    def test_real_transport_hits_the_network_guard(self):
        with self.assertRaises(netguard.NetworkBlocked):
            http_client.urllib_transport(URL + "?a=1", 1, 100)
        with self.assertRaises(ApiError):
            client(http_client.urllib_transport).get("op", URL, {}, max_bytes=100)


class ResultCodeTest(unittest.TestCase):
    def test_json_codes_are_limited_to_safe_characters(self):
        self.assertEqual(http_client.result_code_of(b'{"result":{"resultCode":"11"}}'), "11")
        for bad in (b'{"result":{"resultCode":"a b"}}', b'{"result":{"resultCode":"<x>"}}',
                    b'{"header":{"resultCode":"%41"}}'):
            self.assertIsNone(http_client.result_code_of(bad))


if __name__ == "__main__":
    unittest.main()
