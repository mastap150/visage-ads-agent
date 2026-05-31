-- ---------------------------------------------------------------------------
-- Suggested migration for the Visage BACKEND (visage-backend/app/db.py),
-- NOT for the ads agent. Apply this manually on Neon; the agent will then
-- start joining conversion rows automatically.
--
-- Rationale: the agent attributes installs by parsing this column. The
-- iOS client should set it on first signup using Apple's AdServices token
-- (https://developer.apple.com/documentation/adservices) once the token is
-- attributed by Apple's MMP (~24-48h).
--
-- Format: 'asa:<campaignId>:<adGroupId>:<keywordId>'
-- Missing pieces written as empty segments — e.g. 'asa::adGroupId:'
-- ---------------------------------------------------------------------------

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS acquisition_source TEXT;

CREATE INDEX IF NOT EXISTS idx_users_acquisition_source_asa
  ON users (acquisition_source)
  WHERE acquisition_source LIKE 'asa:%';
