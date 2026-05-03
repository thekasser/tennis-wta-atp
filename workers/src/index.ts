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


// Chunk size matches scripts/push_to_d1.py — D1 caps INSERT statements at
// ~100KB; 40KB chunks leave plenty of headroom for SQL syntax + escaping.
const CHUNK_SIZE = 40_000;


/** Constant-time string compare (avoid timing attacks on the bearer token). */
function constantTimeEq(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}


/** POST /api/admin/sync handler. Accepts a JSON body of materialized blobs,
 *  re-chunks each, and replaces the materialized table contents. Returns a
 *  summary of what was written. */
async function handleAdminSync(request: Request, env: Env): Promise<Response> {
  // 1. Bearer auth
  const expected = env.ADMIN_SYNC_TOKEN;
  if (!expected) {
    return new Response(
      JSON.stringify({ error: "ADMIN_SYNC_TOKEN secret not configured on the worker" }),
      { status: 500, headers: JSON_HEADERS },
    );
  }
  const auth = request.headers.get("authorization") || "";
  const presented = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!constantTimeEq(presented, expected)) {
    return new Response(
      JSON.stringify({ error: "unauthorized" }),
      { status: 401, headers: JSON_HEADERS },
    );
  }

  // 2. Parse body
  let body: { blobs?: Record<string, string> };
  try {
    body = await request.json();
  } catch (e) {
    return new Response(
      JSON.stringify({ error: "invalid JSON body" }),
      { status: 400, headers: JSON_HEADERS },
    );
  }
  const blobs = body?.blobs;
  if (!blobs || typeof blobs !== "object" || Array.isArray(blobs)) {
    return new Response(
      JSON.stringify({ error: "body must have shape { blobs: { name: string, ... } }" }),
      { status: 400, headers: JSON_HEADERS },
    );
  }

  // 3. Build the batch: one DELETE for stale rows + one INSERT per chunk.
  //    D1 batch() commits atomically — if any statement fails, none apply.
  const now = new Date().toISOString();
  const summary: Record<string, { bytes: number; chunks: number }> = {};
  const stmts = [
    env.DB.prepare("DELETE FROM materialized"),
  ];

  for (const [name, payload] of Object.entries(blobs)) {
    if (typeof payload !== "string") {
      return new Response(
        JSON.stringify({ error: `blob '${name}' is not a string` }),
        { status: 400, headers: JSON_HEADERS },
      );
    }
    let chunkNo = 0;
    for (let i = 0; i < payload.length; i += CHUNK_SIZE) {
      const piece = payload.slice(i, i + CHUNK_SIZE);
      stmts.push(env.DB.prepare(
        "INSERT INTO materialized (name, chunk_no, payload, size_bytes, updated_at) " +
        "VALUES (?, ?, ?, ?, ?)"
      ).bind(name, chunkNo, piece, piece.length, now));
      chunkNo++;
    }
    summary[name] = { bytes: payload.length, chunks: chunkNo };
  }

  try {
    await env.DB.batch(stmts);
  } catch (e: any) {
    return new Response(
      JSON.stringify({ error: "D1 batch failed", detail: String(e) }),
      { status: 500, headers: JSON_HEADERS },
    );
  }

  return new Response(
    JSON.stringify({
      ok: true,
      updated_at: now,
      blobs: summary,
      total_chunks: Object.values(summary).reduce((n, b) => n + b.chunks, 0),
    }),
    { headers: JSON_HEADERS },
  );
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

      // POST /api/admin/sync — receive a fresh set of materialized blobs
      // from the cron pipeline and overwrite the materialized table.
      // Auth: Bearer token compared against ADMIN_SYNC_TOKEN secret.
      // Body shape:
      //   { "blobs": { "season_atp": "<json string>", "season_wta": "...", ... } }
      // Each blob is chunked server-side at CHUNK_SIZE and stored as
      // multiple rows (D1's per-statement cap is ~100KB).
      if (path === "/api/admin/sync" && request.method === "POST") {
        return handleAdminSync(request, env);
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
