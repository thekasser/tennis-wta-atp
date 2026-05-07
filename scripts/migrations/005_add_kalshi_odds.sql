-- 005_add_kalshi_odds.sql — Kalshi prediction-market prices.
--
-- Kalshi is a CFTC-regulated event-contract market that prices tennis
-- matches Matchstat's traditional-sportsbook feed often skips (qualifying,
-- low-volume tour matches). This table is a SECOND market benchmark
-- alongside `matches.raw.odd1/odd2`. Used by Model-vs-Market panel and
-- the prospective Brier comparison.
--
-- Markets are mutually exclusive YES contracts (one per player). YES price
-- is in cents 0-100 = implied probability ×100. We store as 0.0-1.0
-- floats for consistency with the rest of the schema. De-vigging is
-- deferred to read time (sum the two YES prices and normalize).
--
-- Multiple snapshots per match across days let us see how Kalshi's price
-- moved leading up to the match — useful for identifying "smart money"
-- price shifts. PRIMARY KEY (match_id, fetched_at) gives us natural
-- de-dup with timestamp resolution.

CREATE TABLE kalshi_odds (
  match_id          TEXT NOT NULL,           -- joins to matches.id / fixtures.id
  kalshi_event      TEXT NOT NULL,           -- e.g. KXATPMATCH-26MAY06TSIMAC
  kalshi_market_p1  TEXT,                    -- ticker for player1's YES contract
  kalshi_market_p2  TEXT,                    -- ticker for player2's YES contract
  fetched_at        TEXT NOT NULL,           -- ISO timestamp of price snapshot

  -- Player slot mirrors our matches/fixtures convention: lower mid → A.
  -- Map to Kalshi's player ordering at write time so this column reflects
  -- "our" p1, not Kalshi's.
  p1_mid            INTEGER,
  p2_mid            INTEGER,
  p1_yes_price      REAL,                    -- 0.0-1.0; cents/100
  p2_yes_price      REAL,
  p1_volume         INTEGER,                 -- contracts traded on this side
  p2_volume         INTEGER,

  -- Pre-match flag for clean-data backtesting. fetched_at < match start
  -- → we got the price before tip-off → safe to use for Brier scoring
  -- without hindsight contamination. Set at write time.
  is_pre_match      INTEGER NOT NULL DEFAULT 0,

  raw               TEXT,                    -- full Kalshi event/markets JSON
  PRIMARY KEY (match_id, fetched_at)
);

CREATE INDEX idx_kalshi_match     ON kalshi_odds(match_id, fetched_at DESC);
CREATE INDEX idx_kalshi_event     ON kalshi_odds(kalshi_event);
CREATE INDEX idx_kalshi_pre_match ON kalshi_odds(is_pre_match, fetched_at DESC);
