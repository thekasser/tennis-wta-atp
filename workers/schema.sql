-- 001_initial.sql — first schema for the SQLite-backed tennis dashboard.
-- See ~/.claude/plans/the-match-history-for-async-pie.md for the rebuild context.
--
-- Conventions:
--   * `bio_id` = our local per-tour player id (matches data/players_{tour}.js).
--   * `mid`    = Matchstat global player id (we treat it as globally unique
--                across tours since Matchstat assigns one per person).
--   * Every JSON column stores a TEXT blob; queryable via SQLite JSON1.
--   * Dates are TEXT in ISO YYYY-MM-DD format (sortable as strings).

PRAGMA foreign_keys = ON;

-- ─── Players ─────────────────────────────────────────────────────────────────
-- Our curated bio set (top-100ish per tour). One row per (tour, bio_id).
-- `mid` is the join key into the matches table.
CREATE TABLE players (
  mid       INTEGER PRIMARY KEY,                       -- Matchstat global id
  tour      TEXT    NOT NULL CHECK (tour IN ('atp', 'wta')),
  bio_id    INTEGER NOT NULL,                          -- our local id (data/players_*.js)
  sid       TEXT,                                      -- Sackmann id (legacy 2024 only)
  name      TEXT    NOT NULL,
  ab        TEXT,                                      -- short label (e.g. "Andreeva")
  country   TEXT,
  age       INTEGER,
  meta      TEXT                                       -- JSON: surf, form, inj, etc.
);
CREATE UNIQUE INDEX idx_players_tour_bio ON players(tour, bio_id);
CREATE INDEX        idx_players_name     ON players(name);

-- ─── Tournaments ─────────────────────────────────────────────────────────────
-- Calendar entries. `active=1` flags tournaments currently underway and is
-- the trigger that bumps fetch frequency in sync_matches.py.
CREATE TABLE tournaments (
  id            TEXT PRIMARY KEY,                      -- 'madrid26', 'usopen26', …
  name          TEXT NOT NULL,
  short         TEXT,                                  -- compact label
  tour          TEXT NOT NULL CHECK (tour IN ('atp', 'wta', 'both')),
  type          TEXT,                                  -- 'GS' | 'M1000' | 'W1000' | 'M500' | …
  surface       TEXT CHECK (surface IN ('H', 'C', 'G', NULL)),
  draw_size     INTEGER,
  start_date    TEXT,
  end_date      TEXT,
  api_id_atp    INTEGER,                               -- Matchstat tournament id (men)
  api_id_wta    INTEGER,                               -- Matchstat tournament id (women)
  active        INTEGER DEFAULT 0,
  points_table  TEXT                                   -- JSON: {round: pts}
);
CREATE INDEX idx_tournaments_active ON tournaments(active) WHERE active = 1;
CREATE INDEX idx_tournaments_dates  ON tournaments(start_date, end_date);

-- ─── Matches ────────────────────────────────────────────────────────────────
-- Append-only-ish: INSERT OR IGNORE on every fetch keeps idempotency.
-- A match can be re-fetched and its `fetched_at` updated, but core fields
-- (id, date, p1_id, p2_id) are immutable. p1_id / p2_id are loose refs
-- (no FK) because doubles partners and qualifiers may not be in our players
-- table.
CREATE TABLE matches (
  id                  TEXT PRIMARY KEY,                -- Matchstat match id
  tournament_id       TEXT,                            -- FK-ish: tournaments.id (nullable)
  tournament_api_id   INTEGER,                         -- raw Matchstat tournament id
  tournament_name     TEXT,                            -- denormalized for unresolved tourneys
  date                TEXT NOT NULL,                   -- YYYY-MM-DD
  round               TEXT,                            -- raw API string ('1/4', 'Final', …)
  surface             TEXT,
  tour                TEXT CHECK (tour IN ('atp', 'wta', NULL)),
  p1_id               INTEGER NOT NULL,                -- Matchstat mid
  p2_id               INTEGER NOT NULL,
  winner_id           INTEGER,                         -- Matchstat mid; NULL = unknown / W/O / RET
  score               TEXT,
  best_of             INTEGER,
  stat_p1             TEXT,                            -- JSON: per-side stat block
  stat_p2             TEXT,                            -- JSON
  fetched_at          TEXT NOT NULL,                   -- ISO timestamp of last fetch touching this row
  raw                 TEXT                             -- JSON: full API row (for debugging)
);
CREATE INDEX idx_matches_p1_date         ON matches(p1_id, date DESC);
CREATE INDEX idx_matches_p2_date         ON matches(p2_id, date DESC);
CREATE INDEX idx_matches_tournament_date ON matches(tournament_id, date DESC);
CREATE INDEX idx_matches_date            ON matches(date DESC);
CREATE INDEX idx_matches_tour_date       ON matches(tour, date DESC);

-- ─── Rankings snapshots ──────────────────────────────────────────────────────
-- One row per (tour, date, player). Append-only — preserves history for
-- rankMove / movement charts. snapshot_date is the API's ranking date.
CREATE TABLE rankings_snapshots (
  tour          TEXT    NOT NULL CHECK (tour IN ('atp', 'wta')),
  snapshot_date TEXT    NOT NULL,                      -- YYYY-MM-DD
  bio_id        INTEGER NOT NULL,                      -- our local id
  rank          INTEGER,
  pts           INTEGER,
  ytd_pts       INTEGER,
  PRIMARY KEY (tour, snapshot_date, bio_id)
);
CREATE INDEX idx_rankings_latest ON rankings_snapshots(tour, snapshot_date DESC);
CREATE INDEX idx_rankings_player ON rankings_snapshots(tour, bio_id, snapshot_date DESC);

-- ─── API fetch log ───────────────────────────────────────────────────────────
-- Audit trail: every Matchstat API call. Useful for debugging stale data,
-- API budget tracking, and proving that "we asked but the API didn't have it."
CREATE TABLE api_fetch_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  fetched_at      TEXT    NOT NULL,                    -- ISO timestamp
  endpoint        TEXT    NOT NULL,                    -- e.g. 'wta/player/past-matches/47742'
  params          TEXT,                                -- JSON of query params
  http_status     INTEGER,                             -- 200, 429, 500, etc.
  rows_returned   INTEGER,                             -- payload size
  rows_inserted   INTEGER,                             -- after INSERT OR IGNORE dedup
  ms_elapsed      INTEGER,                             -- request duration
  error           TEXT                                 -- error message if status != 200
);
CREATE INDEX idx_fetch_log_time     ON api_fetch_log(fetched_at DESC);
CREATE INDEX idx_fetch_log_endpoint ON api_fetch_log(endpoint, fetched_at DESC);

-- ─── Misc key-value store ───────────────────────────────────────────────────
-- For one-off pipeline state: 'last_full_sync_at', 'snapshot_version', etc.
-- Schema versions are tracked separately in `_migrations` (managed by db.py).
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
