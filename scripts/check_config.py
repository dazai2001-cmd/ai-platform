"""Validate runtime configuration without printing secret values."""

from core.config.settings import settings
from core.config.validation import configuration_issues


def main() -> int:
    issues = configuration_issues(settings)
    errors = {issue.name: issue for issue in issues if issue.severity == "error"}
    warnings = {issue.name: issue for issue in issues if issue.severity == "warning"}

    has_cloud_model = bool(
        (settings.GEMINI_API_KEY and settings.GEMINI_MODELS)
        or (settings.OPENROUTER_API_KEY and settings.OPENROUTER_MODELS)
    )
    checks = {
        "runtime": settings.AI_RUNTIME in {"local", "cloud"},
        "cloud model": not settings.IS_CLOUD_RUNTIME or has_cloud_model,
        "authentication": not settings.IS_CLOUD_RUNTIME or settings.AUTH_REQUIRED,
        "email provider": not settings.SEND_VERIFICATION_EMAILS or bool(settings.RESEND_API_KEY),
        "application database": not settings.IS_PRODUCTION or bool(settings.DATABASE_URL),
        "rate limiting": settings.RATE_LIMIT_ENABLED,
        "frontend URL": bool(settings.APP_PUBLIC_URL),
    }

    for label, ok in checks.items():
        print(f"{'OK' if ok else 'FAIL':<4} {label}")
    for issue in warnings.values():
        print(f"WARN {issue.name}: {issue.message}")
    for issue in errors.values():
        print(f"FAIL {issue.name}: {issue.message}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
