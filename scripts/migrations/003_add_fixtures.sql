-- 003_add_fixtures.sql — Upcoming-match fixtures from getTournamentFixtures.
--
-- Distinct from `matches` because:
--   - matches table holds COMPLETED matches with winner_id, score, stats
--   - fixtures table holds SCHEDULED matches with no result yet
-- Once a fixture finishes, it migrates to `matches` via sync_matches.py and
-- gets dropped from fixtures via the `fetched_at` recency filter (any fixture
-- whose date is in the past gets garbage-collected).
--
-- Odds (odd1/odd2) are NULL on insert; bookmakers may publish lines closer to
-- match time, in which case re-running sync_fixtures backfills them.

CREATE TABLE IF NOT EXISTS fixtures (
  id                 INTEGER PRIMARY KEY,    -- Matchstat fixture id
  tour               TEXT NOT NULL,          -- 'atp' or 'wta'
  tournament_id      TEXT,                   -- catalog id (e.g. 'rome26'), via JOIN
  tournament_api_id  INTEGER NOT NULL,
  date               TEXT,                   -- ISO 8601 datetime
  round_id           INTEGER,                -- Matchstat numeric round (mapped at render time)
  p1_mid             INTEGER,
  p1_name            TEXT,
  p1_country         TEXT,
  p2_mid             INTEGER,
  p2_name            TEXT,
  p2_country         TEXT,
  odd1               REAL,                   -- NULL until bookmakers post lines
  odd2               REAL,
  raw                TEXT,                   -- full JSON for forensics
  fetched_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fixtures_tour_date     ON fixtures(tour, date);
CREATE INDEX IF NOT EXISTS idx_fixtures_tournament_id ON fixtures(tournament_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_p1_mid        ON fixtures(p1_mid);
CREATE INDEX IF NOT EXISTS idx_fixtures_p2_mid        ON fixtures(p2_mid);
