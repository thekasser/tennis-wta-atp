-- 004_add_predictions.sql — prospective prediction log.
--
-- Records the model's prediction for each match at the time the prediction
-- was made. Once the match completes (matches.winner_id IS NOT NULL),
-- predictions can be JOINed against outcomes for a clean prospective Brier
-- that doesn't rely on PiT reconstruction.
--
-- Multiple rows per match (one per cron run while the match is upcoming)
-- let us see prediction drift as new data arrives ahead of the match.

CREATE TABLE predictions (
  -- Identity
  match_id        TEXT NOT NULL,            -- joins to matches.id once played; for fixtures
                                            -- this is the fixture_id (Matchstat treats them
                                            -- as the same int across both tables).
  predicted_at    TEXT NOT NULL,            -- ISO timestamp when prediction was logged.
                                            -- (match_id, predicted_at) is unique.

  -- Model fingerprint — lets us segment prediction logs by model version,
  -- so a tuning change (e.g. SHARPEN bump) doesn't pollute prior data.
  model_version   TEXT NOT NULL,            -- e.g. "sharpen=1.25/comp=games-v1"

  -- Match context (denormalized so prediction analytics don't need joins)
  tour            TEXT,                     -- 'atp' | 'wta'
  surface         TEXT,                     -- 'H' | 'C' | 'G'
  date            TEXT,                     -- match date (when known)
  tournament_id   TEXT,                     -- fk-ish to tournaments.id

  -- Slot assignment uses lower-mid → A (matches backtest convention)
  p1_mid          INTEGER NOT NULL,         -- lower mid (slot A)
  p2_mid          INTEGER NOT NULL,
  p1_name         TEXT,
  p2_name         TEXT,

  -- The prediction
  p_pred          REAL NOT NULL,            -- prob slot A wins ∈ [0.05, 0.95]

  -- All inputs at prediction time (lets us reproduce the prediction or do
  -- per-signal analysis without re-fetching from rankings_snapshots etc.)
  pts_p1          INTEGER,
  ytd_p1          INTEGER,
  pts_p2          INTEGER,
  ytd_p2          INTEGER,
  form_p1         TEXT,
  form_p2         TEXT,
  h2h_p1          INTEGER,
  h2h_p2          INTEGER,
  comp_p1         REAL,
  comp_p2         REAL,

  -- Decomposition (per-signal probabilities + weights — reads as JSON)
  signals_json    TEXT,
  weights_json    TEXT,

  PRIMARY KEY (match_id, predicted_at)
);

CREATE INDEX idx_predictions_date         ON predictions(date);
CREATE INDEX idx_predictions_predicted_at ON predictions(predicted_at DESC);
CREATE INDEX idx_predictions_match        ON predictions(match_id, predicted_at DESC);
CREATE INDEX idx_predictions_model        ON predictions(model_version, predicted_at DESC);
