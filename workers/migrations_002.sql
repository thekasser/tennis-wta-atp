-- 002_add_materialized.sql — table that stores pre-computed JSON blobs
-- for the Worker to serve via /api/* endpoints, chunked across rows
-- because D1 caps individual SQL statements at ~100KB.
--
-- Why chunked: recent_matches.js JSON is ~880KB; encoding it as a single
-- INSERT with a giant string literal exceeds D1's per-statement limit.
-- We split each blob into ~40KB chunks (UTF-8 safe boundaries) and the
-- Worker concatenates them on read:
--
--   SELECT payload FROM materialized WHERE name = ? ORDER BY chunk_no
--
-- Why: The Python materializer (`scripts/materialize.py`) already produces
-- exactly the JSON shapes the dashboard needs. Rather than port 600+ LOC
-- of aggregation logic to TypeScript, we push those JSON blobs into D1
-- after each cron run and the Worker just SELECTs + concatenates them.

CREATE TABLE materialized (
  name        TEXT    NOT NULL,         -- e.g. 'season_atp', 'recent_matches'
  chunk_no    INTEGER NOT NULL,         -- 0, 1, 2, … (read order)
  payload     TEXT    NOT NULL,         -- chunked JSON fragment
  size_bytes  INTEGER NOT NULL,         -- chunk byte length
  updated_at  TEXT    NOT NULL,         -- ISO timestamp of upload
  PRIMARY KEY (name, chunk_no)
);
CREATE INDEX idx_materialized_updated ON materialized(updated_at DESC);
