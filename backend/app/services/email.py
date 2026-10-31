import json
import re
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings


_INITIAL_GAPPS_HOST = "script.google.com"
_REDIRECT_GAPPS_HOST = "script.googleusercontent.com"
_ALLOWED_REDIRECT_CODES = {302, 303}
_MAX_REDIRECTS = 5


def _validate_google_apps_script_url(url: str, *, redirect: bool = False) -> str:
    if not isinstance(url, str):
        raise ValueError("Google Apps Script URL is invalid.")

    candidate = url.strip()
    if not candidate:
        raise ValueError("Google Apps Script URL is not configured.")

    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme.lower() != "https":
        raise ValueError("Google Apps Script URL must use HTTPS.")
    if parsed.hostname is None:
        raise ValueError("Google Apps Script URL is malformed.")
    if parsed.username or parsed.password:
        raise ValueError("Google Apps Script URL must not include credentials.")
    if parsed.port not in (None, 443):
        raise ValueError("Google Apps Script URL uses an unexpected port.")

    allowed_host = _REDIRECT_GAPPS_HOST if redirect else _INITIAL_GAPPS_HOST
    if parsed.hostname.lower() != allowed_host:
        raise ValueError(f"Google Apps Script URL hostname is not allowed: {parsed.hostname}.")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Google Apps Script URL path is missing.")
    return candidate


class _PreservePostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow only safe Google Apps Script redirects and never resend the original email POST."""

    def __init__(self, max_redirects: int = _MAX_REDIRECTS):
        super().__init__()
        self.redirect_count = 0
        self.max_redirects = max_redirects

    def _validated_redirect_target(self, req, newurl):
        if newurl is None or not str(newurl).strip():
            raise ValueError("Redirect Location header is missing or invalid.")

        redirect_url = urllib.parse.urljoin(req.full_url, str(newurl).strip())
        parsed = urllib.parse.urlparse(redirect_url)
        if parsed.scheme.lower() != "https":
            raise ValueError("Google Apps Script redirect must use HTTPS; HTTPS downgrade rejected.")
        if parsed.hostname is None:
            raise ValueError("Malformed redirect URL.")
        if parsed.username or parsed.password:
            raise ValueError("Redirect URL must not include credentials.")
        if parsed.port not in (None, 443):
            raise ValueError("Redirect URL uses an unexpected port.")
        if parsed.hostname.lower() != _REDIRECT_GAPPS_HOST:
            raise ValueError(f"Untrusted redirect host: {parsed.hostname}.")
        return redirect_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code not in _ALLOWED_REDIRECT_CODES:
            return None
        if self.redirect_count >= self.max_redirects:
            raise ValueError("Excessive redirects encountered while following Google Apps Script response.")
        self.redirect_count += 1
        secured_url = self._validated_redirect_target(req, newurl)
        return urllib.request.Request(
            secured_url,
            data=None,
            headers={"Accept": "application/json"},
            origin_req_host=req.origin_req_host,
            unverifiable=True,
            method="GET",
        )


def _validate_email_target(to_email: str, subject: str, text_body: str) -> None:
    if not to_email or not isinstance(to_email, str):
        raise ValueError("Recipient email is required.")
    normalized = to_email.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Recipient email is invalid.")
    if not subject or not subject.strip():
        raise ValueError("Email subject is required.")
    if not text_body or not text_body.strip():
        raise ValueError("Email body is required.")


def _send_via_gapps_script(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    script_url = (settings.gymai_email_script_url or "").strip()
    api_secret = (settings.gymai_email_api_secret or "").strip()
    if not api_secret:
        raise RuntimeError("GYMAI_EMAIL_API_SECRET is not configured.")

    try:
        script_url = _validate_google_apps_script_url(script_url)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    _validate_email_target(to_email, subject, text_body)

    payload = {
        "apiKey": api_secret,
        "to": to_email.strip(),
        "subject": subject.strip(),
        "text": text_body.strip(),
    }
    if html_body is not None:
        payload["html"] = html_body

    request = urllib.request.Request(
        script_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_PreservePostRedirectHandler())

    try:
        with opener.open(request, timeout=20) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Apps Script HTTP error {exc.code}: {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Google Apps Script could not be reached: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Google Apps Script request timed out.") from exc
    except Exception as exc:  # pragma: no cover - defensive guard for network edge cases
        raise RuntimeError(f"Email delivery failed via Google Apps Script: {exc}") from exc

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Google Apps Script returned invalid JSON.") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Google Apps Script returned an unexpected response payload.")

    if result.get("success") is not True:
        error_text = result.get("error") or result.get("message") or "Google Apps Script rejected the email."
        raise RuntimeError(f"Google Apps Script reported a delivery failure: {error_text}")


def send_registration_code(to_email: str, code: str) -> None:
    text = f"""Hi,\n\nWelcome to GymAI. Your 6-digit verification code is: {code}\n\nThis code expires in {settings.registration_code_expire_minutes} minutes. If you did not create this account, you can ignore this email.\n\n— GymAI Security\n"""
    _send_via_gapps_script(
        to_email,
        "Verify your GymAI email",
        text,
    )


def send_password_reset_code(to_email: str, code: str) -> None:
    text = f"""Hi,\n\nWe received a request to reset your GymAI password.\n\nYour verification code is: {code}\n\nThis code expires in {settings.password_reset_expire_minutes} minutes. If you did not request a password reset, you can safely ignore this email.\n\n— GymAI Security\n"""
    _send_via_gapps_script(
        to_email,
        "GymAI password reset code",
        text,
    )


def send_event_alert(to_email: str, title: str, message: str, country: str, state: str, area: str, url: str | None) -> None:
    """Send one marathon / fitness event alert. Raises on delivery problems."""
    link = f"\nDetails: {url}\n" if url else ""
    text = f"{title}\nLocation: {area}, {state}, {country}\n\n{message}\n{link}\n— GymAI Events"
    _send_via_gapps_script(
        to_email,
        f"GymAI event near you: {title}",
        text,
    )


def send_incomplete_workout_reminder(to_email: str, workout_url: str) -> None:
    """Reminder email with a prominent CTA button."""
    text = "Your workout today is not completed. Visit GymAI and complete your workout.\n\nComplete It Now: " + workout_url
    html = f"""<!doctype html><html><body style="margin:0;padding:24px;background:#0b0b0d;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:480px;background:#16161a;border:1px solid #2a2a30;border-radius:16px;">
<tr><td style="padding:32px;text-align:center;">
<p style="margin:0 0 8px;font-size:20px;font-weight:700;color:#ffffff;">Gym<span style="color:#FF5A00;">AI</span></p>
<p style="margin:16px 0 28px;font-size:16px;line-height:1.6;color:#d4d4d8;">Your workout today is not completed. Visit GymAI and complete your workout.</p>
<a href="{workout_url}" style="display:inline-block;padding:14px 32px;background:#FF5A00;color:#ffffff;font-size:16px;font-weight:700;text-decoration:none;border-radius:12px;">Complete It Now</a>
</td></tr></table></td></tr></table></body></html>"""
    _send_via_gapps_script(
        to_email,
        "Your workout today is not completed!",
        text,
        html,
    )
