-- Additive only. Existing auctions, metrics, secrets and schedules remain unchanged.
CREATE TABLE IF NOT EXISTS sox_daily_inputs (
  date TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sox_input_revisions (
  date TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  collected_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (date, input_hash)
);
CREATE TABLE IF NOT EXISTS sox_reports (
  name TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
