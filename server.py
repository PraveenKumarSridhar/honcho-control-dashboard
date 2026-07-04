"""
Honcho Dashboard — a local live monitor for a self-hosted Honcho stack.

Proxies the Honcho REST API (127.0.0.1:8000) and serves a single-page
dashboard. Run:  python3 server.py  (default :7777)
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HONCHO = "http://127.0.0.1:8000"
PORT = 7777

# ---------- Honcho client ---------------------------------------------------

def h(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(HONCHO + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="ignore")[:500]


def dashboard_snapshot(workspace: str | None = None) -> dict:
    """Aggregate everything the dashboard wants in one call."""
    out: dict = {"workspaces": [], "active_workspace": workspace,
                 "queue": None, "sessions": [], "peers": [],
                 "activity": [], "totals": {}}

    # 1) workspaces
    s, body = h("POST", "/v3/workspaces/list", {})
    items = body.get("items", []) if isinstance(body, dict) else []
    out["workspaces"] = [
        {"id": w["id"], "created_at": w.get("created_at")} for w in items
    ]
    if not out["active_workspace"] and out["workspaces"]:
        out["active_workspace"] = out["workspaces"][0]["id"]
    ws = out["active_workspace"]

    # 2) peers + sessions + queue in parallel-ish (sequential but cheap)
    s, body = h("POST", f"/v3/workspaces/{ws}/peers/list", {})
    peers = body.get("items", []) if isinstance(body, dict) else []
    s, body = h("POST", f"/v3/workspaces/{ws}/sessions/list", {})
    sessions = body.get("items", []) if isinstance(body, dict) else []
    s, body = h("GET", f"/v3/workspaces/{ws}/queue/status")
    queue = body if isinstance(body, dict) else {}

    out["peers"] = [
        {"id": p["id"], "created_at": p.get("created_at")} for p in peers
    ]

    # 3) per-session deep dive: counts, last message, summary
    sess_detail = []
    for sess in sessions:
        sid = sess["id"]
        sd: dict = {"id": sid, "is_active": sess.get("is_active", True),
                    "message_count": 0, "last_message": None,
                    "last_peer": None, "last_ts": None,
                    "short_summary": None, "long_summary": None}

        _, ms = h("POST", f"/v3/workspaces/{ws}/sessions/{sid}/messages/list", {})
        if isinstance(ms, dict):
            sd["message_count"] = ms.get("total", 0)
            items = ms.get("items", []) or []
            if items:
                last = items[-1]
                sd["last_message"] = (last.get("content") or "")[:240]
                sd["last_peer"] = last.get("peer_id")
                sd["last_ts"] = last.get("created_at") or last.get("id")

        _, sm = h("GET", f"/v3/workspaces/{ws}/sessions/{sid}/summaries")
        if isinstance(sm, dict):
            ss = sm.get("short_summary") or {}
            ls = sm.get("long_summary") or {}
            sd["short_summary"] = (ss.get("content") if isinstance(ss, dict) else None)
            sd["long_summary"] = (ls.get("content") if isinstance(ls, dict) else None)

        sess_detail.append(sd)

    # sort by last_ts descending (None last)
    def sort_key(sd):
        return sd["last_ts"] or ""
    sess_detail.sort(key=sort_key, reverse=True)
    out["sessions"] = sess_detail

    # 4) recent activity feed = last message per session, time-ordered
    feed = []
    for sd in sess_detail:
        if sd["last_message"]:
            feed.append({
                "session": sd["id"],
                "peer": sd["last_peer"],
                "preview": sd["last_message"],
                "ts": sd["last_ts"] or "",
            })
    feed.sort(key=lambda x: x["ts"], reverse=True)
    out["activity"] = feed[:20]

    # 5) per-peer card + conclusion counts
    peer_detail = []
    for p in out["peers"]:
        pd = {"id": p["id"]}
        _, card = h("GET", f"/v3/workspaces/{ws}/peers/{p['id']}/card")
        if isinstance(card, dict):
            pc = card.get("peer_card")
            pd["has_card"] = pc is not None
            pd["card_size"] = len(pc) if isinstance(pc, str) else (len(json.dumps(pc)) if pc else 0)
        # conclusions per peer: list with filter (Honcho doesn't expose a per-peer
        # endpoint, so we list all and filter client-side; capped)
        _, concl = h("POST", f"/v3/workspaces/{ws}/conclusions/list", {})
        n = 0
        if isinstance(concl, dict):
            for c in concl.get("items", []):
                if c.get("peer_id") == p["id"] or c.get("peer") == p["id"]:
                    n += 1
        pd["conclusions"] = n
        peer_detail.append(pd)
    out["peers"] = peer_detail

    # 6) totals
    total_msgs = sum(sd["message_count"] for sd in sess_detail)
    out["totals"] = {
        "sessions": len(sess_detail),
        "peers": len(peer_detail),
        "messages": total_msgs,
        "wu_total": queue.get("total_work_units", 0),
        "wu_done": queue.get("completed_work_units", 0),
        "wu_progress": queue.get("in_progress_work_units", 0),
        "wu_pending": queue.get("pending_work_units", 0),
    }
    out["queue"] = queue
    return out


# ---------- HTTP server -----------------------------------------------------

INDEX_HTML = open(__file__.replace("server.py", "index.html")).read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # quieter; one line per request
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        if url.path == "/api/snapshot":
            qs = parse_qs(url.query)
            ws = (qs.get("workspace") or [None])[0]
            data = dashboard_snapshot(ws)
            return self._send(200, json.dumps(data).encode(), "application/json")
        if url.path == "/api/health":
            return self._send(200, b'{"ok":true}', "application/json")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None

        if url.path == "/api/dream":
            qs = parse_qs(url.query)
            ws = (qs.get("workspace") or ["hermes"])[0]
            status, resp = h("POST", f"/v3/workspaces/{ws}/schedule_dream", body or {})
            self._send(200 if status in (200, 201, 202) else 502,
                       json.dumps({"status": status, "resp": resp}).encode(),
                       "application/json")
            return
        return self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    print(f"Honcho dashboard: http://127.0.0.1:{PORT}/  (proxying {HONCHO})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
