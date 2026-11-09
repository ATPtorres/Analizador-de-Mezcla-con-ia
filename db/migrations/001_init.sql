BEGIN;
CREATE TYPE mix_stage AS ENUM ('mix', 'master', 'instrumental');
CREATE TYPE upload_state AS ENUM ('pending', 'verified', 'expired', 'deleted');
CREATE TYPE job_state AS ENUM ('queued', 'running', 'succeeded', 'failed', 'canceled');
CREATE TYPE member_role AS ENUM ('owner', 'editor', 'viewer');

CREATE TABLE app_user (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_subject text UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workspace (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);
CREATE TABLE membership (
  workspace_id uuid REFERENCES workspace ON DELETE CASCADE,
  user_id uuid REFERENCES app_user ON DELETE CASCADE,
  role member_role NOT NULL,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX membership_by_user ON membership(user_id, workspace_id);
CREATE TABLE project (
  workspace_id uuid REFERENCES workspace ON DELETE CASCADE,
  id uuid DEFAULT gen_random_uuid(),
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 150),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  PRIMARY KEY (workspace_id, id)
);
CREATE INDEX project_history ON project (workspace_id, created_at DESC, id DESC) WHERE deleted_at IS NULL;
CREATE TABLE audio_asset (
  workspace_id uuid REFERENCES workspace ON DELETE CASCADE,
  id uuid DEFAULT gen_random_uuid(),
  object_key text UNIQUE NOT NULL,
  object_version text,
  expected_bytes bigint NOT NULL CHECK (expected_bytes BETWEEN 1 AND 83886080),
  sha256 bytea CHECK (octet_length(sha256) = 32),
  state upload_state NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  upload_expires_at timestamptz NOT NULL,
  purge_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, id),
  CHECK (state <> 'verified' OR (object_version IS NOT NULL AND sha256 IS NOT NULL)),
  CHECK (purge_at >= upload_expires_at)
);
CREATE INDEX asset_purge ON audio_asset (purge_at) WHERE state <> 'deleted';
CREATE TABLE mix_version (
  workspace_id uuid,
  id uuid DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL,
  asset_id uuid NOT NULL,
  ordinal integer NOT NULL CHECK (ordinal > 0),
  stage mix_stage NOT NULL,
  genre text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, project_id, ordinal),
  FOREIGN KEY (workspace_id, project_id) REFERENCES project ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, asset_id) REFERENCES audio_asset
);
CREATE TABLE reference_profile (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  genre text NOT NULL,
  stage mix_stage NOT NULL,
  revision integer NOT NULL CHECK (revision > 0),
  sample_count integer NOT NULL CHECK (sample_count > 0),
  engine_version text NOT NULL,
  source_manifest_sha256 bytea NOT NULL CHECK (octet_length(source_manifest_sha256) = 32),
  features jsonb NOT NULL CHECK (jsonb_typeof(features) = 'object'),
  published_at timestamptz,
  UNIQUE (genre, stage, revision)
);
CREATE TABLE analysis (
  workspace_id uuid,
  id uuid DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL,
  reference_id uuid REFERENCES reference_profile,
  engine_version text NOT NULL,
  rules_version text NOT NULL,
  model_version text,
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
  request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
  status job_state NOT NULL DEFAULT 'queued',
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  lease_until timestamptz,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, idempotency_key),
  FOREIGN KEY (workspace_id, version_id) REFERENCES mix_version ON DELETE CASCADE,
  CHECK ((status = 'running') = (lease_until IS NOT NULL)),
  CHECK ((status IN ('succeeded', 'failed', 'canceled')) = (completed_at IS NOT NULL))
);
CREATE INDEX analysis_history ON analysis (workspace_id, version_id, created_at DESC, id DESC);
CREATE INDEX analysis_expired_leases ON analysis (lease_until) WHERE status = 'running';
CREATE TABLE analysis_report (
  workspace_id uuid,
  analysis_id uuid,
  schema_version integer NOT NULL CHECK (schema_version > 0),
  metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
  findings jsonb NOT NULL CHECK (jsonb_typeof(findings) = 'array'),
  score jsonb CHECK (jsonb_typeof(score) = 'object'),
  artifact_key text UNIQUE,
  PRIMARY KEY (workspace_id, analysis_id),
  FOREIGN KEY (workspace_id, analysis_id) REFERENCES analysis ON DELETE CASCADE
);
CREATE TABLE outbox_event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid REFERENCES workspace ON DELETE CASCADE NOT NULL,
  topic text NOT NULL,
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX outbox_pending ON outbox_event (created_at, id) WHERE published_at IS NULL;
CREATE TABLE subscription (
  workspace_id uuid PRIMARY KEY REFERENCES workspace ON DELETE CASCADE,
  plan_code text NOT NULL CHECK (plan_code IN ('free','pro','studio')),
  provider_subscription_id text UNIQUE,
  status text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE billing_event (
  provider_event_id text PRIMARY KEY,
  processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE quota_counter (
  workspace_id uuid REFERENCES workspace ON DELETE CASCADE,
  period_start date,
  reserved integer NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  consumed integer NOT NULL DEFAULT 0 CHECK (consumed >= 0),
  PRIMARY KEY (workspace_id, period_start)
);
-- Defense in depth. Run the API as a role without ownership or BYPASSRLS.
-- After verifying membership, set_config('mixai.workspace_id', verified_id, true)
-- inside the request transaction. Relay/worker privileges must be provisioned separately.
DO $rls$
DECLARE tenant_table text;
BEGIN
  FOREACH tenant_table IN ARRAY ARRAY['project','audio_asset','mix_version','analysis','analysis_report','outbox_event','subscription','quota_counter']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tenant_table);
    EXECUTE format('CREATE POLICY tenant_scope ON %I USING (workspace_id = nullif(current_setting(''mixai.workspace_id'', true), '''')::uuid) WITH CHECK (workspace_id = nullif(current_setting(''mixai.workspace_id'', true), '''')::uuid)', tenant_table);
  END LOOP;
END
$rls$;
COMMIT;
