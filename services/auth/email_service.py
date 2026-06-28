import html
from typing import Any

import requests

from core.config.settings import settings


class EmailDeliveryError(RuntimeError):
    pass


class EmailService:
    def enabled(self) -> bool:
        return settings.SEND_VERIFICATION_EMAILS and bool(settings.RESEND_API_KEY)

    def send_verification_email(self, to_email: str, verification_url: str) -> dict[str, Any]:
        if not self.enabled():
            return {"sent": False, "reason": "email_not_configured"}

        escaped_url = html.escape(verification_url, quote=True)
        payload = {
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": "Verify your AI Platform email",
            "html": self._verification_html(escaped_url),
            "text": (
                "Verify your AI Platform email by opening this link:\n\n"
                f"{verification_url}\n\n"
                f"This link expires in {settings.AUTH_VERIFICATION_HOURS} hours."
            ),
        }
        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=12,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                detail = f": {exc.response.text[:300]}"
            raise EmailDeliveryError(f"Could not send verification email{detail}") from exc

        data = response.json() if response.content else {}
        return {"sent": True, "id": data.get("id")}

    @staticmethod
    def _verification_html(verification_url: str) -> str:
        return f"""
        <div style="font-family:Arial,sans-serif;line-height:1.5;color:#0f172a">
          <h2 style="margin:0 0 12px">Verify your email</h2>
          <p>Open the link below to finish creating your AI Platform account.</p>
          <p style="margin:24px 0">
            <a href="{verification_url}" style="background:#67e8f9;color:#020617;padding:10px 16px;border-radius:6px;text-decoration:none;font-weight:600">
              Verify email
            </a>
          </p>
          <p>If the button does not work, paste this link into your browser:</p>
          <p style="word-break:break-all;color:#0369a1">{verification_url}</p>
        </div>
        """.strip()


email_service = EmailService()
