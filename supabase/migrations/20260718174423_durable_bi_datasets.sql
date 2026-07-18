CREATE TABLE IF NOT EXISTS app_private.bi_datasets (
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload BYTEA NOT NULL,
  size_bytes BIGINT NOT NULL,
  row_count INTEGER NOT NULL,
  columns_json TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (user_id, name),
  CHECK (kind IN ('csv', 'excel'))
);

CREATE INDEX IF NOT EXISTS idx_bi_datasets_user_updated
  ON app_private.bi_datasets(user_id, updated_at DESC);

REVOKE ALL ON SCHEMA app_private FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE app_private.bi_datasets FROM PUBLIC, anon, authenticated;

COMMENT ON TABLE app_private.bi_datasets IS
  'Private, durable BI uploads scoped to the authenticated application user.';

INSERT INTO app_private.app_schema_migrations (version, name, applied_at)
VALUES (4, 'durable_bi_datasets', EXTRACT(EPOCH FROM NOW()))
ON CONFLICT (version) DO NOTHING;
