import json
import unittest
import urllib.request
from unittest.mock import patch

from app.config import settings
from app.services.email import (
    _PreservePostRedirectHandler,
    _send_via_gapps_script,
    _validate_google_apps_script_url,
)


class FakeResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeOpener:
    def __init__(self, *, redirect_url=None, redirect_code=302, final_payload='{"success": true}', error=None):
        self.redirect_url = redirect_url
        self.redirect_code = redirect_code
        self.final_payload = final_payload
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if request.get_method() == "POST":
            if not self.redirect_url:
                return FakeResponse(self.final_payload)
            redirected = _PreservePostRedirectHandler().redirect_request(
                request,
                fp=None,
                code=self.redirect_code,
                msg="Redirect",
                headers={"Location": self.redirect_url},
                newurl=self.redirect_url,
            )
            self.assert_redirect_request(redirected)
            self.requests.append(redirected)
            return FakeResponse(self.final_payload)
        return FakeResponse(self.final_payload)

    @staticmethod
    def assert_redirect_request(redirected):
        assert redirected is not None
        assert redirected.get_method() == "GET"
        assert redirected.data is None
        assert b"apiKey" not in (redirected.data or b"")
        assert b"Plain message" not in (redirected.data or b"")


def _set_script_settings():
    settings.gymai_email_script_url = "https://script.google.com/macros/s/test/exec"
    settings.gymai_email_api_secret = "secret-value-123"


class EmailRedirectTests(unittest.TestCase):
    def test_valid_initial_apps_script_url(self):
        url = "https://script.google.com/macros/s/test/exec"
        self.assertEqual(_validate_google_apps_script_url(url), url)

    def test_initial_url_requires_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _validate_google_apps_script_url("http://script.google.com/macros/s/test/exec")

    def test_initial_untrusted_hostname_rejected(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            _validate_google_apps_script_url("https://evil.example.com/macros/s/test/exec")

    def test_initial_url_embedded_credentials_and_unexpected_ports_rejected(self):
        with self.assertRaisesRegex(ValueError, "credentials|unexpected port"):
            _validate_google_apps_script_url("https://user:pass@script.google.com:8443/macros/s/test/exec")

    def test_successful_302_redirect_and_final_get_response(self):
        _set_script_settings()
        redirect_url = "https://script.googleusercontent.com/macros/echo?user_content_key=test"
        fake_opener = FakeOpener(redirect_url=redirect_url, redirect_code=302)

        with patch("app.services.email.urllib.request.build_opener", return_value=fake_opener):
            _send_via_gapps_script("user@example.com", "Subject", "Plain message")

        self.assertEqual(len(fake_opener.requests), 2)
        initial = fake_opener.requests[0]
        self.assertEqual(initial.get_method(), "POST")
        initial_payload = json.loads(initial.data.decode("utf-8"))
        self.assertEqual(initial_payload["apiKey"], "secret-value-123")
        redirected = fake_opener.requests[1]
        self.assertEqual(redirected.get_method(), "GET")
        self.assertIsNone(redirected.data)
        self.assertNotIn(b"apiKey", redirected.data or b"")
        self.assertNotIn(b"Plain message", redirected.data or b"")

    def test_successful_303_redirect_and_final_get_response(self):
        _set_script_settings()
        redirect_url = "https://script.googleusercontent.com/macros/echo?user_content_key=test"
        fake_opener = FakeOpener(redirect_url=redirect_url, redirect_code=303)

        with patch("app.services.email.urllib.request.build_opener", return_value=fake_opener):
            _send_via_gapps_script("user@example.com", "Subject", "Plain message")

        self.assertEqual(len(fake_opener.requests), 2)
        redirected = fake_opener.requests[1]
        self.assertEqual(redirected.get_method(), "GET")
        self.assertIsNone(redirected.data)

    def test_rejects_301_307_308_redirects(self):
        request = urllib.request.Request(
            "https://script.google.com/macros/s/test/exec",
            data=json.dumps({"apiKey": "secret"}).encode("utf-8"),
            method="POST",
        )
        for code in (301, 307, 308):
            with self.subTest(code=code):
                self.assertIsNone(_PreservePostRedirectHandler().redirect_request(request, None, code, "Redirect", {}, "https://script.googleusercontent.com/macros/echo"))

    def test_redirect_get_contains_no_original_body_or_secret(self):
        request = urllib.request.Request(
            "https://script.google.com/macros/s/test/exec",
            data=json.dumps({"apiKey": "secret-value-123", "text": "Plain message"}).encode("utf-8"),
            method="POST",
        )
        redirected = _PreservePostRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {"Location": "https://script.googleusercontent.com/macros/echo"},
            "https://script.googleusercontent.com/macros/echo",
        )
        self.assertEqual(redirected.get_method(), "GET")
        self.assertIsNone(redirected.data)
        self.assertNotIn(b"apiKey", redirected.data or b"")
        self.assertNotIn(b"Plain message", redirected.data or b"")

    def test_untrusted_redirect_host_rejected(self):
        request = urllib.request.Request(
            "https://script.google.com/macros/s/test/exec",
            data=json.dumps({"apiKey": "secret"}).encode("utf-8"),
            method="POST",
        )
        with self.assertRaisesRegex(ValueError, "Untrusted redirect host"):
            _PreservePostRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": "https://evil.example.com/callback"},
                "https://evil.example.com/callback",
            )

    def test_https_downgrade_rejected(self):
        request = urllib.request.Request(
            "https://script.google.com/macros/s/test/exec",
            data=json.dumps({"apiKey": "secret"}).encode("utf-8"),
            method="POST",
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _PreservePostRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": "http://script.googleusercontent.com/callback"},
                "http://script.googleusercontent.com/callback",
            )

    def test_invalid_or_missing_location_header(self):
        request = urllib.request.Request("https://script.google.com/macros/s/test/exec", method="POST")
        with self.assertRaisesRegex(ValueError, "Location"):
            _PreservePostRedirectHandler().redirect_request(request, None, 302, "Found", {}, "")
        with self.assertRaisesRegex(ValueError, "Location"):
            _PreservePostRedirectHandler().redirect_request(request, None, 302, "Found", {}, None)

    def test_excessive_redirects(self):
        request = urllib.request.Request("https://script.google.com/macros/s/test/exec", method="POST")
        handler = _PreservePostRedirectHandler(max_redirects=1)
        handler.redirect_count = 1
        with self.assertRaisesRegex(ValueError, "Excessive redirects"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://script.googleusercontent.com/macros/echo")

    def test_success_and_failure_json_responses(self):
        _set_script_settings()
        with patch("app.services.email.urllib.request.build_opener", return_value=FakeOpener(final_payload='{"success": true, "message": "ok"}')):
            _send_via_gapps_script("user@example.com", "Subject", "Plain message")

        with patch("app.services.email.urllib.request.build_opener", return_value=FakeOpener(final_payload='{"success": false, "error": "Rejected"}')):
            with self.assertRaisesRegex(RuntimeError, "delivery failure"):
                _send_via_gapps_script("user@example.com", "Subject", "Plain message")

    def test_invalid_json_and_timeout_handled(self):
        _set_script_settings()
        with patch("app.services.email.urllib.request.build_opener", return_value=FakeOpener(final_payload="not-json")):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                _send_via_gapps_script("user@example.com", "Subject", "Plain message")

        with patch("app.services.email.urllib.request.build_opener", return_value=FakeOpener(error=TimeoutError())):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                _send_via_gapps_script("user@example.com", "Subject", "Plain message")

    def test_existing_public_email_functions_still_work_with_mocked_http_responses(self):
        _set_script_settings()
        with patch("app.services.email.urllib.request.build_opener", return_value=FakeOpener()):
            from app.services.email import (
                send_event_alert,
                send_incomplete_workout_reminder,
                send_password_reset_code,
                send_registration_code,
            )

            _send_via_gapps_script("user@example.com", "Custom", "Body")
            send_registration_code("user@example.com", "123456")
            send_password_reset_code("user@example.com", "654321")
            send_event_alert("user@example.com", "Marathon", "Come run", "US", "CA", "San Diego", "https://example.com")
            send_incomplete_workout_reminder("user@example.com", "https://app.example.com/workout")


if __name__ == "__main__":
    unittest.main()
