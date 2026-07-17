import time

import pytest

from services.career.job_search_service import CareerJobService, DEFAULT_PREFERENCES
from services.storage.sqlite_service import SQLiteService, db


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    isolated = SQLiteService(str(tmp_path / "career.db"))
    monkeypatch.setattr(db, "path", isolated.path)


def _insert_queued_job_and_batch(*, job_id: str = "job-1", batch_id: str = "batch-1"):
    now = time.time()
    db.execute(
        """
        INSERT INTO career_jobs
        (id, user_id, title, company, location, url, description, source, status,
         fit_score, decision, analysis_json, created_at, updated_at)
        VALUES (?, 'user-1', 'Python Engineer', '', 'London', '', 'Python role',
                'adzuna', 'found', NULL, NULL, NULL, ?, ?)
        """,
        (job_id, now, now),
    )
    db.execute(
        """
        INSERT INTO career_score_batches
        (id, user_id, status, cv_text, total, completed, failed, created_at, updated_at)
        VALUES (?, 'user-1', 'queued', 'Python CV', 1, 0, 0, ?, ?)
        """,
        (batch_id, now, now),
    )
    db.execute(
        """
        INSERT INTO career_score_tasks
        (batch_id, job_id, status, error, created_at, updated_at)
        VALUES (?, ?, 'queued', NULL, ?, ?)
        """,
        (batch_id, job_id, now, now),
    )


def test_combined_search_query_uses_profile_and_criteria():
    preferences = {
        **DEFAULT_PREFERENCES,
        "roles": "AI Engineer",
        "must_have": "Python",
        "match_mode": "both",
    }

    query = CareerJobService._search_query(
        preferences,
        "React, TypeScript, Next.js, and Flask application experience",
    )

    assert query.startswith("AI Engineer Python")
    assert "full stack developer" in query


@pytest.mark.parametrize(
    ("requested_mode", "expected"),
    [("remote", True), ("hybrid", False), ("onsite", False)],
)
def test_work_mode_preference_is_enforced(requested_mode, expected):
    candidate = {
        "title": "Python Engineer",
        "company": "Example",
        "location": "Remote",
        "description": "Fully remote Python and PostgreSQL role.",
    }
    preferences = {
        **DEFAULT_PREFERENCES,
        "roles": "Python Engineer",
        "remote": requested_mode,
        "must_have": "Python, PostgreSQL",
        "match_mode": "criteria",
    }

    assert CareerJobService._matches_preferences(candidate, preferences) is expected


def test_all_must_have_skills_are_required():
    candidate = {
        "title": "Python Engineer",
        "company": "Example",
        "location": "London",
        "description": "Build Python APIs backed by PostgreSQL.",
    }
    preferences = {
        **DEFAULT_PREFERENCES,
        "must_have": "Python, Rust",
        "match_mode": "criteria",
    }

    assert not CareerJobService._matches_preferences(candidate, preferences)


def test_short_must_have_skill_uses_token_boundaries():
    assert CareerJobService._contains_preference_term("Build services in C and Python", "C")
    assert not CareerJobService._contains_preference_term("Build React applications", "C")
    assert CareerJobService._contains_preference_term("Machine   learning systems", "machine learning")


def test_negated_remote_description_is_not_classified_as_remote():
    candidate = {
        "title": "Office Engineer",
        "location": "London",
        "description": "This position is not remote and is office-based.",
    }

    assert CareerJobService._candidate_work_mode(candidate) == "onsite"


def test_deleting_a_queued_job_completes_its_batch():
    service = CareerJobService()
    _insert_queued_job_and_batch()

    service.delete_job("job-1", user_id="user-1")

    batch = db.query_one("SELECT status, total FROM career_score_batches WHERE id = 'batch-1'")
    assert batch == {"status": "completed", "total": 0}


def test_skipping_a_queued_job_cancels_task_and_completes_batch():
    service = CareerJobService()
    _insert_queued_job_and_batch()

    service.update_status("job-1", "skipped", user_id="user-1")

    task = db.query_one(
        "SELECT status FROM career_score_tasks WHERE batch_id = 'batch-1' AND job_id = 'job-1'"
    )
    batch = db.query_one("SELECT status, total FROM career_score_batches WHERE id = 'batch-1'")
    assert task == {"status": "cancelled"}
    assert batch == {"status": "completed", "total": 1}


def test_startup_refresh_finishes_an_orphaned_active_batch():
    service = CareerJobService()
    _insert_queued_job_and_batch()
    db.execute(
        "UPDATE career_score_tasks SET status = 'cancelled' WHERE batch_id = 'batch-1'"
    )

    service._refresh_active_score_batches()

    batch = db.query_one("SELECT status FROM career_score_batches WHERE id = 'batch-1'")
    assert batch == {"status": "completed"}
