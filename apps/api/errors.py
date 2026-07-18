import logging
from flask import jsonify
from core.config.settings import settings

logger = logging.getLogger(__name__)


def error_response(
    exc: Exception,
    status: int = 500,
    message: str = "Request failed",
    expose: bool | None = None,
):
    logger.exception("API request failed", exc_info=exc)
    if settings.DEBUG or expose is True or (expose is None and isinstance(exc, ValueError)):
        message = str(exc)
    return jsonify({"error": message}), status
