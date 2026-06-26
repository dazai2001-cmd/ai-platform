import hashlib
import secrets
import time
import uuid

from werkzeug.security import check_password_hash, generate_password_hash

from core.config.settings import settings
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
        return {"user": self._public_user(user), **verification}

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
        return {"already_verified": False, "user": self._public_user(user), **self.create_verification_token(user["id"])}

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
        token_hash = self._hash_token((token or "").strip())
        now = time.time()
        row = db.query_one(
            """
            SELECT auth_users.*
            FROM auth_sessions
            JOIN auth_users ON auth_users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
            """,
            (token_hash, now),
        )
        if not row:
            return None
        db.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
        return self._public_user(row)

    def logout(self, token: str):
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash_token(token or ""),))


auth_service = AuthService()
