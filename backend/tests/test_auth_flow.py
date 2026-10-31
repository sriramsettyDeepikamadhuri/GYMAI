import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import models
from app.config import settings
from app.database import SessionLocal
from app.main import app


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.db.query(models.PendingRegistration).delete()
        self.db.query(models.User).delete()
        self.db.commit()

    def tearDown(self):
        self.db.query(models.PendingRegistration).delete()
        self.db.query(models.User).delete()
        self.db.commit()
        self.db.close()

    def test_registration_generates_and_sends_otp(self):
        with patch("app.routers.auth.send_registration_code") as mock_send:
            response = self.client.post(
                "/api/auth/register",
                json={
                    "full_name": "Ada Lovelace",
                    "email": "ada@example.com",
                    "password": "secret123",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["email"], "ada@example.com")
        self.assertTrue(mock_send.called)

        code = mock_send.call_args[0][1]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        verify_response = self.client.post(
            "/api/auth/verify-registration",
            json={"email": "ada@example.com", "code": code},
        )

        self.assertEqual(verify_response.status_code, 200, verify_response.text)
        data = verify_response.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["email"], "ada@example.com")

    def test_google_oauth_start_redirects_to_backend_callback(self):
        original_client_id = settings.google_client_id
        original_client_secret = settings.google_client_secret
        original_redirect = settings.google_redirect_uri

        try:
            settings.google_client_id = "production-client-id.apps.googleusercontent.com"
            settings.google_client_secret = "production-client-secret"
            settings.google_redirect_uri = "https://backend.example.com/api/auth/google/callback"

            response = self.client.get("/api/auth/google/start", follow_redirects=False)
        finally:
            settings.google_client_id = original_client_id
            settings.google_client_secret = original_client_secret
            settings.google_redirect_uri = original_redirect

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        self.assertIn("client_id=production-client-id.apps.googleusercontent.com", location)
        self.assertIn("redirect_uri=https%3A%2F%2Fbackend.example.com%2Fapi%2Fauth%2Fgoogle%2Fcallback", location)

    def test_google_oauth_callback_rejects_invalid_state(self):
        response = self.client.get(
            "/api/auth/google/callback",
            params={"code": "abc123", "state": "not-valid-state"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid Google OAuth state", response.json()["detail"])

    def test_google_oauth_callback_success_redirects_with_token(self):
        original_client_id = settings.google_client_id
        original_client_secret = settings.google_client_secret
        original_frontend_url = settings.frontend_url

        try:
            settings.google_client_id = "production-client-id.apps.googleusercontent.com"
            settings.google_client_secret = "production-client-secret"
            settings.frontend_url = "https://gymai-deployment-final.vercel.app"

            with patch("app.routers.auth._valid_oauth_state", return_value=True):
                with patch("app.routers.auth._google_user_from_id_token", return_value={
                    "email": "google-user@example.com",
                    "name": "Google User",
                    "picture": "https://example.com/avatar.png",
                    "email_verified": True,
                }):
                    with patch("app.routers.auth.urllib.request.urlopen") as mock_urlopen:
                        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"id_token":"test-id-token"}'
                        response = self.client.get(
                            "/api/auth/google/callback",
                            params={"code": "auth-code", "state": "test-nonce.abc"},
                            follow_redirects=False,
                        )
        finally:
            settings.google_client_id = original_client_id
            settings.google_client_secret = original_client_secret
            settings.frontend_url = original_frontend_url

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        self.assertIn("/auth/callback?token=", location)


if __name__ == "__main__":
    unittest.main()
