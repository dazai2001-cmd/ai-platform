import logging
from flask import jsonify
from core.config.settings import settings

logger = logging.getLogger(__name__)


def error_response(
    exc: Exception,
    status: int = 500,
    message: str = "Request failed",
    expose: bool = False,
):
    logger.exception("API request failed", exc_info=exc)
    if expose or isinstance(exc, ValueError) or settings.DEBUG:
        message = str(exc)
    return jsonify({"error": message}), status
