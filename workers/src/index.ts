/**
 * Tennis Dashboard Worker — Phase 2.
 *
 * Serves the static dashboard at /, /wta_analytics, /wta_analytics.html,
 * and JSON API endpoints under /api/* backed by D1.
 *
 * Read endpoints (open):
 *   GET /api/season/:tour                tour ∈ {atp, wta}
 *   GET /api/players/:tour
 *   GET /api/tournaments
 *   GET /api/recent-matches
 *   GET /api/h2h
 *   GET /api/tournament-history
 *   GET /api/trapezoid
 *
 * Admin endpoints (Day 3, bearer token via ADMIN_SYNC_TOKEN secret):
 *   POST /api/admin/sync                 trigger pipeline run
 *
 * All read endpoints SELECT a single JSON blob from the `materialized`
 * table and return it. The blobs are produced by the Python materializer
 * + scripts/push_to_d1.py. Aggregation logic stays in Python; this Worker
 * is a thin read tier.
 */

interface Env {
  DB: D1Database;
  ASSETS: Fetcher;          // static asset binding (from [assets])
  ADMIN_SYNC_TOKEN?: string;
}

const CORS_HEADERS = {
  "access-control-allow-origin":  "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  ...CORS_HEADERS,
};


/** Fetch a materialized JSON blob by name. Each blob is chunked across rows
 *  (D1 caps SQL statements at ~100KB; some blobs are 800KB+), so we SELECT
 *  ordered by chunk_no and concatenate. Returns null if missing. */
async function getBlob(env: Env, name: string): Promise<string | null> {
  const { results } = await env.DB
    .prepare("SELECT payload FROM materialized WHERE name = ? ORDER BY chunk_no")
    .bind(name)
    .all<{ payload: string }>();
  if (!results || results.length === 0) return null;
  return results.map(r => r.payload).join("");
}


/** Wrap a blob fetch in a Response, with proper headers + 404 fallback. */
async function blobResponse(env: Env, name: string): Promise<Response> {
  const payload = await getBlob(env, name);
  if (payload === null) {
    return new Response(JSON.stringify({ error: `not found: ${name}` }), {
      status: 404,
      headers: JSON_HEADERS,
    });
  }
  return new Response(payload, {
    headers: {
      ...JSON_HEADERS,
      // Browser caches for 60s; CF edge caches for 5 min.
      "cache-control": "public, max-age=60, s-maxage=300",
    },
  });
}


export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // ─── /api/* read endpoints ───────────────────────────────────────────
    if (path.startsWith("/api/")) {
      // GET /api/season/{atp|wta}
      const seasonMatch = path.match(/^\/api\/season\/(atp|wta)\/?$/);
      if (seasonMatch) {
        return blobResponse(env, `season_${seasonMatch[1]}`);
      }

      // GET /api/players/{atp|wta}
      const playersMatch = path.match(/^\/api\/players\/(atp|wta)\/?$/);
      if (playersMatch) {
        return blobResponse(env, `players_${playersMatch[1]}`);
      }

      switch (path.replace(/\/$/, "")) {
        case "/api/tournaments":         return blobResponse(env, "tournaments");
        case "/api/recent-matches":      return blobResponse(env, "recent_matches");
        case "/api/h2h":                 return blobResponse(env, "h2h");
        case "/api/tournament-history":  return blobResponse(env, "tournament_history");
        case "/api/trapezoid":           return blobResponse(env, "trapezoid");
        case "/api/health": {
          // Quick liveness probe — counts blobs + reports last update.
          const stats = await env.DB
            .prepare("SELECT COUNT(*) AS n, MAX(updated_at) AS latest FROM materialized")
            .first<{ n: number; latest: string }>();
          return new Response(
            JSON.stringify({ ok: true, blobs: stats?.n ?? 0, latest: stats?.latest ?? null }),
            { headers: JSON_HEADERS },
          );
        }
      }

      // POST /api/admin/sync — Day 3 placeholder. Returns 501 for now.
      if (path === "/api/admin/sync" && request.method === "POST") {
        return new Response(
          JSON.stringify({ error: "not implemented yet (Phase 2 Day 3)" }),
          { status: 501, headers: JSON_HEADERS },
        );
      }

      return new Response(JSON.stringify({ error: "not found", path }), {
        status: 404,
        headers: JSON_HEADERS,
      });
    }

    // ─── Static assets (existing behavior) ──────────────────────────────
    return env.ASSETS.fetch(request);
  },
};
