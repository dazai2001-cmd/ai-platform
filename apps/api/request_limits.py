from flask import Request

from core.config.settings import settings


CV_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class SizeLimitedRequest(Request):
    """Apply route-aware limits before Flask parses or spools request bodies."""

    @property
    def max_content_length(self) -> int | None:
        if self.is_json:
            return settings.MAX_JSON_BYTES
        configured_limit = super().max_content_length
        if self.method == "POST" and self.path.rstrip("/") == "/api/career/profile/import":
            cv_multipart_limit = settings.MAX_CV_UPLOAD_BYTES + CV_MULTIPART_OVERHEAD_BYTES
            if configured_limit is None:
                return cv_multipart_limit
            return min(configured_limit, cv_multipart_limit)
        return configured_limit
