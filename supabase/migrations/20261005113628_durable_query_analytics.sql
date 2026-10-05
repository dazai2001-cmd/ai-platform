CREATE TABLE IF NOT EXISTS app_private.analytics_events (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  occurred_at DOUBLE PRECISION NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_user_time
  ON app_private.analytics_events(user_id, occurred_at);

REVOKE ALL ON TABLE app_private.analytics_events FROM PUBLIC;
DO $analytics_privacy$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON TABLE app_private.analytics_events FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON TABLE app_private.analytics_events FROM authenticated;
  END IF;
END;
$analytics_privacy$;

COMMENT ON TABLE app_private.analytics_events IS
  'Private, tenant-scoped query metrics that survive backend restarts and deployments.';

INSERT INTO app_private.app_schema_migrations (version, name, applied_at)
VALUES (5, 'durable_query_analytics', EXTRACT(EPOCH FROM NOW()))
ON CONFLICT (version) DO NOTHING;
