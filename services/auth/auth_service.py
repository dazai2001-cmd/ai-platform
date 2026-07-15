import hashlib
import secrets
import time
import uuid

from flask import g, has_request_context
from werkzeug.security import check_password_hash, generate_password_hash

from core.config.settings import settings
from services.auth.email_service import EmailDeliveryError, email_service
from services.storage.sqlite_service import db


class AuthError(ValueError):
    pass


class AuthService:
    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _public_user(self, user: dict | None) -> dict | None:
        if not user:
            return None
        return {
            "id": user["id"],
            "email": user["email"],
            "email_verified": bool(user["email_verified"]),
            "created_at": user["created_at"],
        }

    def create_user(self, email: str, password: str) -> dict:
        normalized = (email or "").strip().lower()
        if not normalized or "@" not in normalized:
            raise AuthError("Enter a valid email address")
        if len(password or "") < 8:
            raise AuthError("Password must be at least 8 characters")

        existing = db.query_one("SELECT * FROM auth_users WHERE email = ?", (normalized,))
        if existing:
            raise AuthError("An account with this email already exists")

        now = time.time()
        user_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO auth_users (id, email, password_hash, email_verified, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (user_id, normalized, generate_password_hash(password), now, now),
        )
        user = db.query_one("SELECT * FROM auth_users WHERE id = ?", (user_id,))
        verification = self.create_verification_token(user_id)
        return {"user": self._public_user(user), **self._deliver_verification(normalized, verification)}

    def create_verification_token(self, user_id: str) -> dict:
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + settings.AUTH_VERIFICATION_HOURS * 60 * 60
        db.execute(
            """
            INSERT INTO auth_email_tokens (token_hash, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self._hash_token(token), user_id, expires_at, now),
        )
        return {
            "verification_token": token,
            "verification_url": f"{settings.APP_PUBLIC_URL}/auth/verify?token={token}",
            "verification_expires_at": expires_at,
        }

    def verify_email(self, token: str) -> dict:
        token_hash = self._hash_token((token or "").strip())
        row = db.query_one("SELECT * FROM auth_email_tokens WHERE token_hash = ?", (token_hash,))
        now = time.time()
        if not row or row["used_at"]:
            raise AuthError("Verification link is invalid")
        if row["expires_at"] < now:
            raise AuthError("Verification link has expired")

        db.execute("UPDATE auth_email_tokens SET used_at = ? WHERE token_hash = ?", (now, token_hash))
        db.execute(
            "UPDATE auth_users SET email_verified = 1, updated_at = ? WHERE id = ?",
            (now, row["user_id"]),
        )
        user = db.query_one("SELECT * FROM auth_users WHERE id = ?", (row["user_id"],))
        return {"user": self._public_user(user)}

    def resend_verification(self, email: str) -> dict:
        normalized = (email or "").strip().lower()
        user = db.query_one("SELECT * FROM auth_users WHERE email = ?", (normalized,))
        if not user:
            raise AuthError("No account found for this email")
        if user["email_verified"]:
            return {"already_verified": True, "user": self._public_user(user)}
        verification = self.create_verification_token(user["id"])
        return {
            "already_verified": False,
            "user": self._public_user(user),
            **self._deliver_verification(normalized, verification),
        }

    def _deliver_verification(self, email: str, verification: dict) -> dict:
        response = {
            "verification_sent": False,
            "verification_delivery": "link",
            "verification_expires_at": verification["verification_expires_at"],
            "message": "Verification link created.",
        }

        if email_service.enabled():
            try:
                delivery = email_service.send_verification_email(email, verification["verification_url"])
                response.update({
                    "verification_sent": True,
                    "verification_delivery": "email",
                    "email_id": delivery.get("id"),
                    "message": "Verification email sent. Check your inbox.",
                })
            except EmailDeliveryError as exc:
                response.update({
                    "verification_error": str(exc),
                    "message": "Could not send email, so a verification link was created instead.",
                })

        # Development may surface a link when email delivery is intentionally
        # disabled. Production must never expose account-verification secrets
        # through an API response, regardless of the selected model runtime.
        if not getattr(settings, "IS_PRODUCTION", False):
            response.update({
                "verification_token": verification["verification_token"],
                "verification_url": verification["verification_url"],
            })
        return response

    def login(self, email: str, password: str) -> dict:
        normalized = (email or "").strip().lower()
        user = db.query_one("SELECT * FROM auth_users WHERE email = ?", (normalized,))
        if not user or not check_password_hash(user["password_hash"], password or ""):
            raise AuthError("Invalid email or password")
        if not user["email_verified"]:
            raise AuthError("Please verify your email before logging in")

        token = secrets.token_urlsafe(48)
        now = time.time()
        expires_at = now + settings.AUTH_SESSION_DAYS * 24 * 60 * 60
        db.execute(
            """
            INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self._hash_token(token), user["id"], now, expires_at, now),
        )
        return {"token": token, "expires_at": expires_at, "user": self._public_user(user)}

    def authenticate_token(self, token: str) -> dict | None:
        resolved_token = (token or "").strip()
        source = "bearer" if resolved_token else ""
        if not resolved_token and has_request_context():
            # The app-level auth guard passes the bearer value it found. When
            # that value is absent, resolve the HttpOnly browser cookie here so
            # existing non-browser bearer callers keep their exact behavior.
            from apps.api.auth_context import request_auth_token

            resolved_token, source = request_auth_token()
        if not resolved_token:
            return None

        token_hash = self._hash_token(resolved_token)
        now = time.time()
        row = db.query_one(
            """
            SELECT auth_users.*, auth_sessions.last_seen_at AS session_last_seen_at
            FROM auth_sessions
            JOIN auth_users ON auth_users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
            """,
            (token_hash, now),
        )
        if not row:
            return None
        # Avoid turning every authenticated read into a SQLite write while
        # still keeping useful session activity data.
        if now - float(row.get("session_last_seen_at") or 0) >= 300:
            db.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
        if has_request_context():
            g.auth_token_source = source
        return self._public_user(row)

    def logout(self, token: str):
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash_token(token or ""),))


auth_service = AuthService()
