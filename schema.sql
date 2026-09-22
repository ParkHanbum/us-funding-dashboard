
CREATE TABLE IF NOT EXISTS metrics (
  source TEXT NOT NULL,
  metric TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  raw_json TEXT,
  PRIMARY KEY (metric, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_metrics_metric_time
ON metrics(metric, observed_at DESC);

CREATE TABLE IF NOT EXISTS auctions (
  cusip TEXT NOT NULL,
  security_type TEXT,
  security_term TEXT,
  auction_date TEXT,
  issue_date TEXT,
  maturity_date TEXT,
  offering_amt REAL,
  total_accepted REAL,
  soma_accepted REAL,
  price_per_100 REAL,
  status TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  raw_json TEXT,
  PRIMARY KEY (cusip, auction_date)
);
CREATE INDEX IF NOT EXISTS idx_auctions_issue_date
ON auctions(issue_date);
CREATE INDEX IF NOT EXISTS idx_auctions_auction_date
ON auctions(auction_date);

CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  message TEXT
);
