// Portolan IRC proxy — one shared, stateless Cloudflare Worker that fronts ANY
// static Portolan catalog so auth-mandating warehouses (Snowflake, Oracle) can
// consume it.
//
// It does two things a dumb CDN can't:
//   1. Absorbs the warehouse's mandatory Authorization header (object stores
//      reject unknown bearer tokens; this Worker just doesn't forward it).
//   2. Serves a fake POST /v1/oauth/tokens so OAuth clients (Oracle, Snowflake
//      OAUTH) complete their token handshake — the piece pure-static + CDN lacks.
//
// Stateless, path-encoded origin — no per-creator setup, no `create` call:
//   CATALOG_URI = https://<worker>/gcs/<bucket>/<prefix>
//             or  https://<worker>/s3/<region>/<bucket>/<prefix>
// The IRC client appends /v1/..., the Worker maps it back to the object store.
//
// Only catalog (/v1/...) traffic flows through here — warehouses read the actual
// data files directly from their own storage (external volume), never via the
// proxy. So bandwidth is tiny.

const ORIGIN = {
  // /gcs/<bucket>/<key...>  -> https://storage.googleapis.com/<bucket>/<key...>
  gcs: (rest) => `https://storage.googleapis.com/${rest}`,
  // /s3/<region>/<bucket>/<key...> -> https://s3.<region>.amazonaws.com/<bucket>/<key...>
  s3: (rest) => {
    const i = rest.indexOf("/");
    const region = rest.slice(0, i);
    return `https://s3.${region}.amazonaws.com/${rest.slice(i + 1)}`;
  },
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
  });

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/+/, "");

    // Fake OAuth token endpoint (any catalog's …/v1/oauth/tokens). The catalog
    // is public; the token is meaningless — we just satisfy the handshake.
    if (path.endsWith("/v1/oauth/tokens")) {
      return json({ access_token: "portolan-public", token_type: "bearer", expires_in: 3600 });
    }

    const slash = path.indexOf("/");
    const scheme = slash === -1 ? path : path.slice(0, slash);
    const rest = slash === -1 ? "" : path.slice(slash + 1);
    const map = ORIGIN[scheme];
    if (!map) return json({ error: "unknown origin scheme; use /gcs/… or /s3/…" }, 400);

    // SSRF guard: only proxy IRC catalog reads (paths containing /v1/). Data
    // files are read by the engine directly from storage, never through here.
    if (!rest.includes("/v1/") && !rest.endsWith("/v1/config")) {
      return json({ error: "only /v1/ catalog paths are proxied" }, 403);
    }

    const originUrl = map(rest) + (url.search || "");
    // Fetch anonymously — deliberately NOT forwarding Authorization, so the
    // object store serves the public object instead of rejecting a bad token.
    const upstream = await fetch(originUrl, { method: "GET" });
    const body = await upstream.arrayBuffer();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type": "application/json",
        "cache-control": "public, max-age=60",
        "access-control-allow-origin": "*",
      },
    });
  },
};
