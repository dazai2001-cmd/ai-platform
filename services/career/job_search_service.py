from __future__ import annotations

import re
import threading
import time
import uuid
import base64
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from application.ingestion.ingestion_service import IngestionService
from core.config.settings import settings
from services.career.career_service import career_service
from services.storage.sqlite_service import db


DEFAULT_PREFERENCES = {
    "roles": "",
    "locations": "",
    "remote": "any",
    "industries": "",
    "must_have": "",
    "avoid": "",
    "match_mode": "both",
}

JOB_STATUSES = {"found", "saved", "scored", "opened", "applied", "skipped"}
MIN_MATCH_SCORE = 70
SEARCH_JOB_SOURCES = {"adzuna", "reed", "remotive", "arbeitnow", "search"}
USAGE_ACTION_SCORE = "career_score"
USAGE_ACTION_PACK = "career_pack"
DAY_SECONDS = 24 * 60 * 60


class CareerJobService:
    def __init__(self):
        self._score_worker_lock = threading.Lock()
        self._score_worker: threading.Thread | None = None

    def cloud_quota_status(self, user_id: str = "local") -> dict[str, Any]:
        return {
            "enabled": settings.IS_CLOUD_RUNTIME,
            "score": self._quota_status(USAGE_ACTION_SCORE, user_id=user_id),
            "pack": self._quota_status(USAGE_ACTION_PACK, user_id=user_id),
            "score_batch_max": self._cloud_score_batch_limit(),
            "search_max_results": 50,
        }

    def record_application_pack(self, user_id: str = "local"):
        self._record_usage(USAGE_ACTION_PACK, user_id=user_id)

    def ensure_application_pack_allowed(self, user_id: str = "local"):
        self._ensure_quota(USAGE_ACTION_PACK, user_id=user_id)

    def _quota_status(self, action: str, user_id: str = "local") -> dict[str, int | bool]:
        limit = self._cloud_limit(action)
        used = self._usage_count(action, user_id=user_id) if settings.IS_CLOUD_RUNTIME else 0
        if limit is None:
            return {"limit": 0, "used": used, "remaining": 0, "limited": False}
        return {
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "limited": True,
        }

    def _ensure_quota(self, action: str, user_id: str = "local"):
        if not settings.IS_CLOUD_RUNTIME:
            return
        limit = self._cloud_limit(action)
        if limit is None:
            return
        used = self._usage_count(action, user_id=user_id)
        if used >= limit:
            label = "job scores" if action == USAGE_ACTION_SCORE else "application packs"
            raise ValueError(f"Daily cloud limit reached for {label}: {used}/{limit}. Try again tomorrow or run locally.")

    def _remaining_quota(self, action: str, user_id: str = "local") -> int | None:
        if not settings.IS_CLOUD_RUNTIME:
            return None
        limit = self._cloud_limit(action)
        if limit is None:
            return None
        return max(0, limit - self._usage_count(action, user_id=user_id))

    def _record_usage(self, action: str, user_id: str = "local"):
        if not settings.IS_CLOUD_RUNTIME:
            return
        db.execute(
            "INSERT INTO usage_events (user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, action, time.time()),
        )

    def _usage_count(self, action: str, user_id: str = "local") -> int:
        row = db.query_one(
            """
            SELECT COUNT(*) AS count
            FROM usage_events
            WHERE user_id = ? AND action = ? AND created_at >= ?
            """,
            (user_id, action, time.time() - DAY_SECONDS),
        )
        return int(row["count"] if row else 0)

    @staticmethod
    def _cloud_limit(action: str) -> int | None:
        if action == USAGE_ACTION_SCORE:
            return max(0, settings.CAREER_CLOUD_DAILY_SCORE_LIMIT)
        if action == USAGE_ACTION_PACK:
            return max(0, settings.CAREER_CLOUD_DAILY_PACK_LIMIT)
        return None

    @staticmethod
    def _cloud_score_batch_limit() -> int:
        return max(1, settings.CAREER_CLOUD_MAX_SCORE_BATCH_JOBS)

    def preferences(self, user_id: str = "local") -> dict[str, str]:
        row = db.query_one("SELECT preferences_json FROM career_preferences WHERE user_id = ?", (user_id,))
        saved = db.loads(row["preferences_json"], {}) if row else {}
        return {**DEFAULT_PREFERENCES, **(saved or {})}

    def save_preferences(self, preferences: dict[str, Any], user_id: str = "local") -> dict[str, str]:
        clean = {
            key: str(preferences.get(key, "")).strip()
            for key in DEFAULT_PREFERENCES
        }
        if clean["remote"] not in {"any", "remote", "hybrid", "onsite"}:
            clean["remote"] = "any"
        if clean["match_mode"] not in {"both", "profile", "criteria"}:
            clean["match_mode"] = "both"
        db.execute(
            """
            INSERT OR REPLACE INTO career_preferences (user_id, preferences_json, updated_at)
            VALUES (?, ?, ?)
            """,
            (user_id, db.dumps(clean), time.time()),
        )
        return clean

    def profile(self, user_id: str = "local") -> dict[str, Any]:
        row = db.query_one("SELECT cv_text, updated_at FROM career_profile WHERE user_id = ?", (user_id,))
        if not row:
            return {"cv_text": "", "updated_at": None}
        return {"cv_text": row["cv_text"], "updated_at": row["updated_at"]}

    def save_profile(self, cv_text: str, user_id: str = "local") -> dict[str, Any]:
        clean = str(cv_text or "").strip()
        now = time.time()
        db.execute(
            """
            INSERT OR REPLACE INTO career_profile (user_id, cv_text, updated_at)
            VALUES (?, ?, ?)
            """,
            (user_id, clean, now),
        )
        return {"cv_text": clean, "updated_at": now}

    def list_jobs(self, user_id: str = "local") -> list[dict[str, Any]]:
        self.remove_duplicates(user_id=user_id)
        rows = db.query("SELECT * FROM career_jobs WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
        return [self._row_to_job(row) for row in rows]

    def remove_duplicates(self, user_id: str = "local") -> int:
        rows = db.query("SELECT * FROM career_jobs WHERE user_id = ?", (user_id,))
        ordered = sorted(rows, key=self._deduplication_priority, reverse=True)
        seen: set[str] = set()
        duplicate_ids: list[str] = []

        for row in ordered:
            keys = self._job_identity_keys(row)
            if keys and keys.intersection(seen):
                duplicate_ids.append(row["id"])
                continue
            seen.update(keys)

        if duplicate_ids:
            db.execute_many([
                ("DELETE FROM career_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
                for job_id in duplicate_ids
            ])
        return len(duplicate_ids)

    def get_job(self, job_id: str, user_id: str = "local") -> dict[str, Any] | None:
        row = db.query_one("SELECT * FROM career_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        return self._row_to_job(row) if row else None

    def delete_job(self, job_id: str, user_id: str = "local"):
        if not self.get_job(job_id, user_id=user_id):
            return
        db.execute_many([
            (
                "UPDATE career_score_tasks SET status = 'cancelled', updated_at = ? "
                "WHERE job_id = ? AND status = 'queued'",
                (time.time(), job_id),
            ),
            ("DELETE FROM career_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)),
        ])

    def search_jobs(self, cv_text: str = "", limit: int = 10, user_id: str = "local") -> dict[str, Any]:
        preferences = self.preferences(user_id=user_id)
        query = self._search_query(preferences, cv_text)
        raw_jobs, searched_sources, skipped_sources = self._fetch_public_jobs(
            query,
            preferences,
            limit=max(limit * 2, 12),
        )
        self.remove_duplicates(user_id=user_id)
        existing_keys = self._existing_identity_keys(user_id=user_id)

        saved: list[dict[str, Any]] = []
        errors: list[str] = []
        for candidate in raw_jobs:
            if len(saved) >= limit:
                break
            url = candidate.get("url", "").strip()
            description = candidate.get("description", "").strip()
            candidate_keys = self._job_identity_keys(candidate)
            if not url or not description or candidate_keys.intersection(existing_keys):
                continue
            if not self._matches_preferences(candidate, preferences):
                continue

            try:
                job = self.save_job(
                    description=description,
                    title=candidate.get("title", ""),
                    company=candidate.get("company", ""),
                    location=candidate.get("location", ""),
                    url=url,
                    source=candidate.get("source", "search"),
                    cv_text="",
                    user_id=user_id,
                )
                if job["status"] == "saved":
                    job = self.update_status(job["id"], "found", user_id=user_id)
                saved.append(job)
                existing_keys.update(self._job_identity_keys(job))
            except Exception as e:
                errors.append(str(e))

        saved.sort(key=lambda job: job.get("fit_score") if job.get("fit_score") is not None else -1, reverse=True)
        return {
            "query": query,
            "match_mode": preferences.get("match_mode", "both"),
            "searched_sources": searched_sources,
            "skipped_sources": skipped_sources,
            "saved": saved,
            "count": len(saved),
            "scored_count": 0,
            "min_match_score": MIN_MATCH_SCORE,
            "rejected_low_score": 0,
            "will_score": preferences.get("match_mode") != "criteria" and bool(cv_text.strip()),
            "errors": errors[:3],
        }

    def stream_search_jobs(self, cv_text: str = "", limit: int = 10, user_id: str = "local"):
        preferences = self.preferences(user_id=user_id)
        query = self._search_query(preferences, cv_text)
        raw_jobs, searched_sources, skipped_sources = self._fetch_public_jobs(
            query,
            preferences,
            limit=max(limit * 2, 12),
        )
        self.remove_duplicates(user_id=user_id)
        existing_keys = self._existing_identity_keys(user_id=user_id)
        should_score_jobs = preferences.get("match_mode") != "criteria" and bool(cv_text.strip())

        yield {
            "event": "started",
            "query": query,
            "match_mode": preferences.get("match_mode", "both"),
            "searched_sources": searched_sources,
            "skipped_sources": skipped_sources,
            "min_match_score": MIN_MATCH_SCORE,
            "will_score": should_score_jobs,
        }

        saved_count = 0
        scored_count = 0
        rejected_low_score = 0
        errors: list[str] = []

        for candidate in raw_jobs:
            if saved_count >= limit:
                break

            url = candidate.get("url", "").strip()
            description = candidate.get("description", "").strip()
            candidate_keys = self._job_identity_keys(candidate)
            if not url or not description or candidate_keys.intersection(existing_keys):
                continue
            if not self._matches_preferences(candidate, preferences):
                continue

            try:
                job = self.save_job(
                    description=description,
                    title=candidate.get("title", ""),
                    company=candidate.get("company", ""),
                    location=candidate.get("location", ""),
                    url=url,
                    source=candidate.get("source", "search"),
                    cv_text="",
                    user_id=user_id,
                )

                if job["status"] == "saved":
                    job = self.update_status(job["id"], "found", user_id=user_id)
                    yield {
                        "event": "found",
                        "job": job,
                        "saved_count": saved_count + 1,
                    }

                saved_count += 1
                existing_keys.update(self._job_identity_keys(job))
            except Exception as e:
                message = str(e)
                errors.append(message)
                yield {"event": "error", "error": message}

        yield {
            "event": "done",
            "query": query,
            "match_mode": preferences.get("match_mode", "both"),
            "count": saved_count,
            "scored_count": scored_count,
            "rejected_low_score": rejected_low_score,
            "min_match_score": MIN_MATCH_SCORE,
            "searched_sources": searched_sources,
            "skipped_sources": skipped_sources,
            "errors": errors[:3],
        }

    def update_status(self, job_id: str, status: str, user_id: str = "local") -> dict[str, Any]:
        status = status.strip().lower()
        if status not in JOB_STATUSES:
            raise ValueError("invalid job status")
        if not self.get_job(job_id, user_id=user_id):
            raise ValueError("job not found")

        now = time.time()
        applied_at = now if status == "applied" else None
        db.execute(
            """
            UPDATE career_jobs
            SET status = ?, applied_at = COALESCE(?, applied_at), updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (status, applied_at, now, job_id, user_id),
        )
        if status in {"applied", "skipped"}:
            db.execute(
                """
                UPDATE career_score_tasks
                SET status = 'cancelled', updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, job_id),
            )
        return self.get_job(job_id, user_id=user_id)

    def import_url(self, url: str, cv_text: str = "", user_id: str = "local") -> dict[str, Any]:
        html = IngestionService._read_limited_url(url).decode("utf-8", errors="ignore")
        parsed = self._parse_job_html(html, url)
        return self.save_job(
            description=parsed["description"],
            title=parsed["title"],
            company=parsed["company"],
            location=parsed["location"],
            url=url,
            source=self._source_for_url(url),
            cv_text=cv_text,
            user_id=user_id,
        )

    def save_job(
        self,
        description: str,
        title: str = "",
        company: str = "",
        location: str = "",
        url: str = "",
        source: str = "manual",
        cv_text: str = "",
        user_id: str = "local",
    ) -> dict[str, Any]:
        description = description.strip()
        if not description:
            raise ValueError("job description is required")

        job_id = uuid.uuid4().hex
        now = time.time()
        title = title.strip() or self._infer_title(description)
        company = company.strip()
        location = location.strip()
        candidate = {
            "title": title,
            "company": company,
            "location": location,
            "url": url.strip(),
        }
        existing = self._find_duplicate(candidate, user_id=user_id)
        if existing:
            if cv_text.strip() and existing.get("fit_score") is None:
                return self.score_job(existing["id"], cv_text, user_id=user_id)
            return existing

        score_data = self._score(cv_text, description) if cv_text.strip() else {}

        db.execute(
            """
            INSERT INTO career_jobs
            (id, user_id, title, company, location, url, description, source, status,
             fit_score, decision, analysis_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                title,
                company,
                location,
                url.strip(),
                description,
                source,
                "scored" if score_data else "saved",
                score_data.get("fit_score"),
                score_data.get("application_decision"),
                db.dumps(score_data) if score_data else None,
                now,
                now,
            ),
        )
        return self.get_job(job_id, user_id=user_id)

    def score_job(self, job_id: str, cv_text: str, user_id: str = "local") -> dict[str, Any]:
        job = self.get_job(job_id, user_id=user_id)
        if not job:
            raise ValueError("job not found")
        if not cv_text.strip():
            raise ValueError("cv_text is required")

        self._ensure_quota(USAGE_ACTION_SCORE, user_id=user_id)
        score_data = self._score(cv_text, job["description"])
        db.execute(
            """
            UPDATE career_jobs
            SET status = CASE WHEN status IN ('applied', 'skipped') THEN status ELSE ? END,
                fit_score = ?, decision = ?, analysis_json = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                "scored",
                score_data.get("fit_score"),
                score_data.get("application_decision"),
                db.dumps(score_data),
                time.time(),
                job_id,
                user_id,
            ),
        )
        self._record_usage(USAGE_ACTION_SCORE, user_id=user_id)
        return self.get_job(job_id, user_id=user_id)

    def create_score_batch(self, cv_text: str = "", job_ids: list[str] | None = None, user_id: str = "local") -> dict[str, Any]:
        cv_text = str(cv_text or "").strip() or self.profile(user_id=user_id)["cv_text"]
        if not cv_text:
            raise ValueError("CV/profile is required before scoring jobs")

        self.remove_duplicates(user_id=user_id)
        candidates = self._score_batch_candidates(job_ids, user_id=user_id)
        if settings.IS_CLOUD_RUNTIME and candidates:
            remaining = self._remaining_quota(USAGE_ACTION_SCORE, user_id=user_id)
            if remaining is not None and remaining <= 0:
                limit = settings.CAREER_CLOUD_DAILY_SCORE_LIMIT
                raise ValueError(f"Daily cloud limit reached for job scores: {limit}/{limit}. Try again tomorrow or run locally.")
            cap = self._cloud_score_batch_limit()
            if remaining is not None:
                cap = min(cap, remaining)
            candidates = candidates[:cap]

        active = db.query_one(
            """
            SELECT * FROM career_score_batches
            WHERE user_id = ? AND status IN ('queued', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id,),
        )
        now = time.time()
        if active:
            batch_id = active["id"]
            db.execute(
                "UPDATE career_score_batches SET cv_text = ?, updated_at = ? WHERE id = ?",
                (cv_text, now, batch_id),
            )
        else:
            batch_id = uuid.uuid4().hex
            db.execute(
                """
                INSERT INTO career_score_batches
                (id, user_id, status, cv_text, total, completed, failed, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, 0, 0, 0, ?, ?)
                """,
                (batch_id, user_id, cv_text, now, now),
            )

        for job in candidates:
            db.execute(
                """
                INSERT OR IGNORE INTO career_score_tasks
                (batch_id, job_id, status, error, created_at, updated_at)
                VALUES (?, ?, 'queued', NULL, ?, ?)
                """,
                (batch_id, job["id"], now, now),
            )

        self._refresh_score_batch(batch_id)
        self.start_score_worker()
        return self.get_score_batch(batch_id, user_id=user_id)

    def get_current_score_batch(self, user_id: str = "local") -> dict[str, Any] | None:
        row = db.query_one(
            """
            SELECT * FROM career_score_batches
            WHERE user_id = ?
            ORDER BY CASE WHEN status IN ('queued', 'running') THEN 0 ELSE 1 END,
                     updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return self._score_batch_payload(row) if row else None

    def get_score_batch(self, batch_id: str, user_id: str = "local") -> dict[str, Any] | None:
        row = db.query_one("SELECT * FROM career_score_batches WHERE id = ? AND user_id = ?", (batch_id, user_id))
        return self._score_batch_payload(row) if row else None

    def cancel_score_batch(self, batch_id: str, user_id: str = "local") -> dict[str, Any]:
        if not db.query_one("SELECT id FROM career_score_batches WHERE id = ? AND user_id = ?", (batch_id, user_id)):
            raise ValueError("score batch not found")
        now = time.time()
        db.execute_many([
            (
                "UPDATE career_score_tasks SET status = 'cancelled', updated_at = ? "
                "WHERE batch_id = ? AND status = 'queued'",
                (now, batch_id),
            ),
            (
                "UPDATE career_score_batches SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, batch_id),
            ),
        ])
        return self.get_score_batch(batch_id, user_id=user_id)

    def start_score_worker(self):
        with self._score_worker_lock:
            if self._score_worker and self._score_worker.is_alive():
                return
            now = time.time()
            db.execute_many([
                (
                    "UPDATE career_score_tasks SET status = 'queued', updated_at = ? WHERE status = 'running'",
                    (now,),
                ),
                (
                    "UPDATE career_score_batches SET status = 'queued', updated_at = ? WHERE status = 'running'",
                    (now,),
                ),
            ])
            self._score_worker = threading.Thread(
                target=self._score_worker_loop,
                name="career-score-worker",
                daemon=True,
            )
            self._score_worker.start()

    def _score_worker_loop(self):
        while True:
            task = db.query_one(
                """
                SELECT task.batch_id, task.job_id, batch.cv_text, batch.user_id
                FROM career_score_tasks AS task
                JOIN career_score_batches AS batch ON batch.id = task.batch_id
                WHERE task.status = 'queued' AND batch.status IN ('queued', 'running')
                ORDER BY task.created_at, task.job_id
                LIMIT 1
                """
            )
            if not task:
                time.sleep(0.75)
                continue

            now = time.time()
            db.execute_many([
                (
                    "UPDATE career_score_batches SET status = 'running', updated_at = ? WHERE id = ?",
                    (now, task["batch_id"]),
                ),
                (
                    "UPDATE career_score_tasks SET status = 'running', error = NULL, updated_at = ? "
                    "WHERE batch_id = ? AND job_id = ?",
                    (now, task["batch_id"], task["job_id"]),
                ),
            ])

            try:
                job = self.get_job(task["job_id"], user_id=task["user_id"])
                if not job:
                    raise ValueError("job no longer exists")
                if job.get("status") in {"applied", "skipped"}:
                    db.execute(
                        """
                        UPDATE career_score_tasks SET status = 'cancelled', updated_at = ?
                        WHERE batch_id = ? AND job_id = ?
                        """,
                        (time.time(), task["batch_id"], task["job_id"]),
                    )
                    self._refresh_score_batch(task["batch_id"])
                    continue
                if job.get("fit_score") is None:
                    self.score_job(task["job_id"], task["cv_text"], user_id=task["user_id"])
                db.execute(
                    """
                    UPDATE career_score_tasks
                    SET status = 'completed', error = NULL, updated_at = ?
                    WHERE batch_id = ? AND job_id = ?
                    """,
                    (time.time(), task["batch_id"], task["job_id"]),
                )
            except Exception as exc:
                db.execute(
                    """
                    UPDATE career_score_tasks
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE batch_id = ? AND job_id = ?
                    """,
                    (str(exc)[:500], time.time(), task["batch_id"], task["job_id"]),
                )
            self._refresh_score_batch(task["batch_id"])

    def _score_batch_candidates(self, job_ids: list[str] | None, user_id: str = "local") -> list[dict[str, Any]]:
        rows = db.query(
            """
            SELECT id, source FROM career_jobs
            WHERE user_id = ? AND fit_score IS NULL AND status NOT IN ('applied', 'skipped')
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        requested = {str(job_id) for job_id in (job_ids or []) if str(job_id).strip()}
        return [
            row
            for row in rows
            if row["source"] in SEARCH_JOB_SOURCES and (not requested or row["id"] in requested)
        ]

    def _refresh_score_batch(self, batch_id: str):
        batch = db.query_one("SELECT status FROM career_score_batches WHERE id = ?", (batch_id,))
        if not batch:
            return
        counts = {
            row["status"]: row["count"]
            for row in db.query(
                "SELECT status, COUNT(*) AS count FROM career_score_tasks WHERE batch_id = ? GROUP BY status",
                (batch_id,),
            )
        }
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        processed = completed + failed + counts.get("cancelled", 0)
        status = batch["status"]
        if status != "cancelled":
            status = "completed" if total == 0 or processed >= total else status
        db.execute(
            """
            UPDATE career_score_batches
            SET status = ?, total = ?, completed = ?, failed = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, total, completed, failed, time.time(), batch_id),
        )

    def _score_batch_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        counts = {
            item["status"]: item["count"]
            for item in db.query(
                "SELECT status, COUNT(*) AS count FROM career_score_tasks WHERE batch_id = ? GROUP BY status",
                (row["id"],),
            )
        }
        current = db.query_one(
            """
            SELECT job.id, job.title
            FROM career_score_tasks AS task
            JOIN career_jobs AS job ON job.id = task.job_id
            WHERE task.batch_id = ? AND task.status = 'running'
            LIMIT 1
            """,
            (row["id"],),
        )
        processed = counts.get("completed", 0) + counts.get("failed", 0)
        total = int(row["total"] or 0)
        return {
            "id": row["id"],
            "status": row["status"],
            "total": total,
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "remaining": counts.get("queued", 0) + counts.get("running", 0),
            "processed": processed,
            "progress": round((processed / total) * 100) if total else 100,
            "current_job": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _score(self, cv_text: str, description: str) -> dict[str, Any]:
        analysis = career_service.analyze_fit(cv_text, description)
        if "analysis" in analysis and isinstance(analysis["analysis"], dict):
            analysis = analysis["analysis"]
        return analysis

    @staticmethod
    def _is_good_match(job: dict[str, Any]) -> bool:
        score = job.get("fit_score")
        return isinstance(score, (int, float)) and score >= MIN_MATCH_SCORE

    def _existing_identity_keys(self, user_id: str = "local") -> set[str]:
        keys: set[str] = set()
        for row in db.query("SELECT title, company, location, url FROM career_jobs WHERE user_id = ?", (user_id,)):
            keys.update(self._job_identity_keys(row))
        return keys

    def _find_duplicate(self, candidate: dict[str, Any], user_id: str = "local") -> dict[str, Any] | None:
        candidate_keys = self._job_identity_keys(candidate)
        if not candidate_keys:
            return None
        for row in db.query("SELECT * FROM career_jobs WHERE user_id = ?", (user_id,)):
            if candidate_keys.intersection(self._job_identity_keys(row)):
                return self._row_to_job(row)
        return None

    @staticmethod
    def _job_identity_keys(job: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        url = str(job.get("url") or "").strip()
        if url:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            path = re.sub(r"/+", "/", parsed.path).rstrip("/").lower()
            if host and path:
                keys.add(f"url:{host}{path}")

        title = CareerJobService._identity_text(job.get("title", ""))
        company = CareerJobService._identity_text(job.get("company", ""))
        location = CareerJobService._identity_location(job.get("location", ""))
        if title and company:
            keys.add(f"role:{title}|{company}|{location}")
        return keys

    @staticmethod
    def _identity_text(value: Any) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()

    @staticmethod
    def _identity_location(value: Any) -> str:
        text = CareerJobService._identity_text(value)
        country_terms = {"uk", "united kingdom", "england", "great britain", "britain"}
        parts = [part.strip() for part in re.split(r"[,/|]", str(value or "").lower()) if part.strip()]
        local_parts = [part for part in parts if CareerJobService._identity_text(part) not in country_terms]
        return CareerJobService._identity_text(local_parts[0] if local_parts else text)

    @staticmethod
    def _deduplication_priority(job: dict[str, Any]) -> tuple[int, int, float]:
        status_priority = {
            "applied": 6,
            "saved": 5,
            "opened": 4,
            "scored": 3,
            "found": 2,
            "skipped": 1,
        }
        return (
            status_priority.get(str(job.get("status") or ""), 0),
            1 if isinstance(job.get("fit_score"), (int, float)) else 0,
            float(job.get("updated_at") or 0),
        )

    def _fetch_public_jobs(
        self,
        query: str,
        preferences: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, str]], list[str], list[str]]:
        jobs: list[dict[str, str]] = []
        source_jobs: list[list[dict[str, str]]] = []
        searched_sources: list[str] = []
        skipped_sources: list[str] = []
        fetchers = [
            ("adzuna", lambda: self._fetch_adzuna_jobs(query, preferences, limit)),
            ("reed", lambda: self._fetch_reed_jobs(query, preferences, limit)),
            ("remotive", lambda: self._fetch_remotive_jobs(query, limit)),
            ("arbeitnow", lambda: self._fetch_arbeitnow_jobs(query, limit)),
        ]
        for source, fetcher in fetchers:
            try:
                fetched = fetcher()
                if fetched is None:
                    skipped_sources.append(source)
                    continue
                source_jobs.append(fetched)
                searched_sources.append(source)
            except Exception:
                skipped_sources.append(source)
                continue
        jobs = self._interleave_source_jobs(source_jobs)
        return jobs, searched_sources, skipped_sources

    @staticmethod
    def _interleave_source_jobs(source_jobs: list[list[dict[str, str]]]) -> list[dict[str, str]]:
        interleaved: list[dict[str, str]] = []
        max_length = max((len(jobs) for jobs in source_jobs), default=0)
        for index in range(max_length):
            for jobs in source_jobs:
                if index < len(jobs):
                    interleaved.append(jobs[index])
        return interleaved

    @staticmethod
    def _fetch_adzuna_jobs(query: str, preferences: dict[str, str], limit: int) -> list[dict[str, str]] | None:
        if not settings.ADZUNA_APP_ID or not settings.ADZUNA_APP_KEY:
            return None
        params = {
            "app_id": settings.ADZUNA_APP_ID,
            "app_key": settings.ADZUNA_APP_KEY,
            "results_per_page": str(min(limit, 50)),
            "what": query,
            "where": preferences.get("locations", ""),
            "content-type": "application/json",
        }
        url = "https://api.adzuna.com/v1/api/jobs/gb/search/1?" + "&".join(
            f"{quote_plus(key)}={quote_plus(value)}"
            for key, value in params.items()
            if value
        )
        data = CareerJobService._read_json(url, timeout=12)
        jobs = []
        for item in data.get("results", []):
            location = item.get("location") or {}
            company = item.get("company") or {}
            jobs.append({
                "title": item.get("title", ""),
                "company": company.get("display_name", ""),
                "location": location.get("display_name", ""),
                "url": item.get("redirect_url", ""),
                "description": CareerJobService._html_to_text(item.get("description", "")),
                "source": "adzuna",
            })
        return jobs

    @staticmethod
    def _fetch_reed_jobs(query: str, preferences: dict[str, str], limit: int) -> list[dict[str, str]] | None:
        if not settings.REED_API_KEY:
            return None
        params = {
            "keywords": query,
            "locationName": preferences.get("locations", ""),
            "distanceFromLocation": "25",
            "resultsToTake": str(min(limit, 100)),
        }
        url = "https://www.reed.co.uk/api/1.0/search?" + "&".join(
            f"{quote_plus(key)}={quote_plus(value)}"
            for key, value in params.items()
            if value
        )
        token = base64.b64encode(f"{settings.REED_API_KEY}:".encode("utf-8")).decode("ascii")
        data = CareerJobService._read_json(url, timeout=12, headers={"Authorization": f"Basic {token}"})
        jobs = []
        for item in data.get("results", []):
            jobs.append({
                "title": item.get("jobTitle", ""),
                "company": item.get("employerName", ""),
                "location": item.get("locationName", ""),
                "url": item.get("jobUrl", ""),
                "description": CareerJobService._html_to_text(item.get("jobDescription", "")),
                "source": "reed",
            })
        return jobs

    @staticmethod
    def _fetch_remotive_jobs(query: str, limit: int) -> list[dict[str, str]]:
        data = CareerJobService._read_json(
            f"https://remotive.com/api/remote-jobs?search={quote_plus(query)}",
            timeout=12,
        )
        jobs = []
        for item in (data.get("jobs") or [])[:limit]:
            description = CareerJobService._html_to_text(item.get("description", ""))
            jobs.append({
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "location": item.get("candidate_required_location", "Remote"),
                "url": item.get("url", ""),
                "description": description,
                "source": "remotive",
            })
        return jobs

    @staticmethod
    def _fetch_arbeitnow_jobs(query: str, limit: int) -> list[dict[str, str]]:
        data = CareerJobService._read_json("https://www.arbeitnow.com/api/job-board-api", timeout=12)
        terms = [term.lower() for term in re.split(r"[\s,;/]+", query) if len(term) > 2]
        jobs = []
        for item in data.get("data", []):
            haystack = " ".join([
                item.get("title", ""),
                item.get("company_name", ""),
                " ".join(item.get("tags") or []),
                item.get("description", ""),
            ]).lower()
            if terms and not any(term in haystack for term in terms):
                continue
            jobs.append({
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "location": item.get("location", ""),
                "url": item.get("url", ""),
                "description": CareerJobService._html_to_text(item.get("description", "")),
                "source": "arbeitnow",
            })
            if len(jobs) >= limit:
                break
        return jobs

    @staticmethod
    def _read_json(url: str, timeout: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
        import json

        request_headers = {"User-Agent": "ai-platform-career-agent/0.1"}
        request_headers.update(headers or {})
        request = Request(url, headers=request_headers)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))

    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        return text[:12000]

    @staticmethod
    def _search_query(preferences: dict[str, str], cv_text: str = "") -> str:
        mode = preferences.get("match_mode", "both")
        criteria_query = " ".join(
            part.strip()
            for part in [preferences.get("roles", ""), preferences.get("must_have", "")]
            if part.strip()
        ).strip()
        profile_query = CareerJobService._profile_search_query(cv_text)

        if mode == "profile":
            query = profile_query or criteria_query
        elif mode == "criteria":
            query = criteria_query
        else:
            query = criteria_query or profile_query
        return query or "AI engineer"

    @staticmethod
    def _profile_search_query(cv_text: str) -> str:
        text = cv_text.lower()
        role_terms = [
            ("ai engineer", ["ai engineer", "artificial intelligence engineer", "llm"]),
            ("machine learning engineer", ["machine learning", "pytorch", "tensorflow"]),
            ("data scientist", ["data scientist", "data science", "forecasting"]),
            ("full stack developer", ["react", "next.js", "typescript", "flask"]),
            ("backend engineer", ["api", "sql", "docker", "redis"]),
        ]
        selected: list[str] = []
        for label, markers in role_terms:
            if any(marker in text for marker in markers):
                selected.append(label)
        if not selected:
            return ""
        return " ".join(selected[:3])

    @staticmethod
    def _matches_preferences(candidate: dict[str, str], preferences: dict[str, str]) -> bool:
        mode = preferences.get("match_mode", "both")
        title = candidate.get("title", "").lower()
        location_text = candidate.get("location", "").lower()
        haystack = " ".join([
            candidate.get("title", ""),
            candidate.get("company", ""),
            candidate.get("location", ""),
            candidate.get("description", ""),
        ]).lower()
        avoid = [term.strip().lower() for term in re.split(r"[,;/]+", preferences.get("avoid", "")) if term.strip()]
        if any(term and term in haystack for term in avoid):
            return False
        if mode != "profile":
            roles = CareerJobService._split_preference_terms(preferences.get("roles", ""))
            if roles and not any(CareerJobService._role_matches(title, role) for role in roles):
                return False

            locations = CareerJobService._split_preference_terms(preferences.get("locations", ""))
            if locations and not any(CareerJobService._location_matches(location_text, location) for location in locations):
                return False
        return True

    @staticmethod
    def _split_preference_terms(value: str) -> list[str]:
        return [term.strip().lower() for term in re.split(r"[,;/]+", value or "") if term.strip()]

    @staticmethod
    def _role_matches(title: str, role: str) -> bool:
        title = title.lower()
        role = role.lower().replace("artificial intelligence", "ai").replace("machine learning", "ml")
        if role in title:
            return True

        words = [word for word in re.findall(r"[a-z0-9+.#]+", role) if word not in {"and", "or", "the", "role"}]
        if not words:
            return True

        aliases = {
            "ai": ("ai", "artificial intelligence", "llm", "genai"),
            "ml": ("ml", "machine learning"),
            "frontend": ("frontend", "front-end", "front end"),
            "fullstack": ("fullstack", "full-stack", "full stack"),
        }
        for word in words:
            options = aliases.get(word, (word,))
            if not any(option in title for option in options):
                return False
        return True

    @staticmethod
    def _location_matches(text: str, location: str) -> bool:
        text = text.lower()
        location = location.lower()
        options = {location}
        if location in {"uk", "u.k.", "united kingdom", "great britain", "britain"}:
            options.update({
                "uk",
                "u.k.",
                "united kingdom",
                "great britain",
                "britain",
                "england",
                "scotland",
                "wales",
                "northern ireland",
                "london",
                "manchester",
                "birmingham",
                "bristol",
                "leeds",
                "liverpool",
                "glasgow",
                "edinburgh",
            })
        return any(CareerJobService._contains_location_token(text, option) for option in options)

    @staticmethod
    def _contains_location_token(text: str, option: str) -> bool:
        if len(option.replace(".", "")) <= 3:
            compact_text = text.replace(".", "")
            compact_option = option.replace(".", "")
            return re.search(rf"(?<![a-z0-9]){re.escape(compact_option)}(?![a-z0-9])", compact_text) is not None
        return option in text

    @staticmethod
    def _parse_job_html(html: str, url: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        title = (
            CareerJobService._meta(soup, "og:title")
            or (soup.title.string.strip() if soup.title and soup.title.string else "")
            or "Imported job"
        )
        company = CareerJobService._meta(soup, "og:site_name")
        location = CareerJobService._find_labeled_text(soup, ["location", "office"])

        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if len(text) < 80:
            raise ValueError("Could not extract enough job description text from this URL")
        if len(text) > 16000:
            text = text[:16000].rsplit("\n", 1)[0].strip()

        hostname = urlparse(url).hostname or ""
        return {
            "title": title.replace(" | ", " - ")[:180],
            "company": company[:120],
            "location": location[:120],
            "description": f"Source: {hostname}\n\n{text}",
        }

    @staticmethod
    def _meta(soup: BeautifulSoup, key: str) -> str:
        tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
        return (tag.get("content") or "").strip() if tag else ""

    @staticmethod
    def _find_labeled_text(soup: BeautifulSoup, labels: list[str]) -> str:
        text = soup.get_text("\n", strip=True)
        for label in labels:
            match = re.search(rf"{label}\s*:?\s*\n?(.{{2,80}})", text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _infer_title(description: str) -> str:
        for line in description.splitlines():
            line = line.strip()
            if 6 <= len(line) <= 90:
                return line
        return "Saved job"

    @staticmethod
    def _source_for_url(url: str) -> str:
        host = (urlparse(url).hostname or "url").lower()
        if "greenhouse" in host:
            return "greenhouse"
        if "lever" in host:
            return "lever"
        return host

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "company": row["company"] or "",
            "location": row["location"] or "",
            "url": row["url"] or "",
            "description": row["description"],
            "source": row["source"],
            "status": row["status"],
            "fit_score": row["fit_score"],
            "decision": row["decision"] or "",
            "analysis": db.loads(row["analysis_json"], None),
            "applied_at": row.get("applied_at"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


career_jobs = CareerJobService()
