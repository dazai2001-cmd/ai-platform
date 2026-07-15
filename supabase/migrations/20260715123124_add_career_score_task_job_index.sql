CREATE INDEX IF NOT EXISTS idx_career_score_tasks_job
  ON app_private.career_score_tasks(job_id);

INSERT INTO app_private.app_schema_migrations (version, name, applied_at)
VALUES (3, 'foreign_key_indexes', EXTRACT(EPOCH FROM NOW()))
ON CONFLICT (version) DO NOTHING;
