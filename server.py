"""
Honcho Dashboard — a local live monitor for a self-hosted Honcho stack.

Proxies the Honcho REST API (127.0.0.1:8000) and serves a single-page
dashboard. Run:  python3 server.py  (default :7777)
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HONCHO = "http://127.0.0.1:8000"
PORT = 7777

# ---------- Honcho client ---------------------------------------------------

def h(method: str, path: str, body: dict | None = None):
    """Single Honcho call. Returns (status, body|str|None). Used as a tuple
    elsewhere in the code, so we keep that contract."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(HONCHO + path, data=data, method=method, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            dt = (time.monotonic() - t0) * 1000
            USAGE.record(method, path, r.status, dt, len(raw))
            try:
                return r.status, (json.loads(raw) if raw else None)
            except json.JSONDecodeError:
                return r.status, raw.decode("utf-8", "ignore")[:500] if raw else None
    except urllib.error.HTTPError as e:
        dt = (time.monotonic() - t0) * 1000
        raw = e.read()
        USAGE.record(method, path, e.code, dt, len(raw))
        return e.code, raw.decode(errors="ignore")[:500]
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        dt = (time.monotonic() - t0) * 1000
        USAGE.record(method, path, 0, dt, 0, error=str(e))
        return 0, str(e)


# ---------- usage tracking ---------------------------------------------------
# Honcho's REST API does not expose token counts. The deriver consumes tokens
# internally but never persists them. So this tracker measures what we CAN
# actually observe from the dashboard side:
#   - API call volume per endpoint (method+path template)
#   - status code distribution
#   - latency histogram (ms)
#   - response size (bytes)
# Combined with message/summary char counts (memory_state), these are the
# closest honest proxy for "what is this dashboard spending."

class UsageTracker:
    def __init__(self):
        # key = method + path template (digits + ids collapsed)
        self.calls: dict[str, int] = defaultdict(int)
        self.errors: dict[str, int] = defaultdict(int)
        self.bytes_in: dict[str, int] = defaultdict(int)   # bytes received
        self.lat_ms_sum: dict[str, float] = defaultdict(float)
        self.lat_ms_max: dict[str, float] = defaultdict(float)
        self.status: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.history: list[tuple[float, int]] = []        # (ts_epoch, calls_in_window)
        self.snapshots: int = 0                            # number of /api/snapshot calls
        self.snapshots_last_ts: float = 0.0

    @staticmethod
    def _template(path: str) -> str:
        # collapse UUIDs/ULIDs/long base62 ids into <id>
        # also collapse /v3/workspaces/{id}/... first
        import re
        t = re.sub(r"/v3/workspaces/[^/?]+", "/v3/workspaces/<ws>", path)
        t = re.sub(r"/peers/[^/?]+", "/peers/<peer>", t)
        t = re.sub(r"/sessions/[^/?]+", "/sessions/<sid>", t)
        t = re.sub(r"/messages/[^/?]+", "/messages/<mid>", t)
        t = re.sub(r"/conclusions/[^/?]+", "/conclusions/<id>", t)
        return t

    def record(self, method, path, status, lat_ms, bytes_recv, error=None):
        key = f"{method.upper()} {self._template(path)}"
        self.calls[key] += 1
        self.bytes_in[key] += bytes_recv
        self.lat_ms_sum[key] += lat_ms
        if lat_ms > self.lat_ms_max[key]:
            self.lat_ms_max[key] = lat_ms
        sc = status if status else 0
        self.status[key][sc] += 1
        if sc >= 400 or error:
            self.errors[key] += 1
        now = time.time()
        # rotate history every call (cheap; length is bounded)
        self.history.append((now, 1))

    def snapshot_total(self):
        self.snapshots += 1
        self.snapshots_last_ts = time.time()

    def reset(self):
        self.calls.clear(); self.errors.clear(); self.bytes_in.clear()
        self.lat_ms_sum.clear(); self.lat_ms_max.clear(); self.status.clear()
        self.history.clear(); self.snapshots = 0

    def summarise(self, window_seconds=60):
        now = time.time()
        # prune history
        self.history = [(t, n) for t, n in self.history if now - t <= window_seconds]
        # group by endpoint template
        rows = []
        for key, n in sorted(self.calls.items(), key=lambda kv: -kv[1]):
            avg = self.lat_ms_sum[key] / n if n else 0
            err = self.errors.get(key, 0)
            rows.append({
                "endpoint": key,
                "calls": n,
                "errors": err,
                "avg_lat_ms": round(avg, 1),
                "max_lat_ms": round(self.lat_ms_max.get(key, 0), 1),
                "bytes": self.bytes_in.get(key, 0),
                "status": dict(self.status.get(key, {})),
            })
        cps = sum(n for _, n in self.history) / max(1, window_seconds)
        return {
            "window_seconds": window_seconds,
            "calls_per_sec_avg": round(cps, 3),
            "calls_in_window": sum(n for _, n in self.history),
            "total_calls_recorded": sum(self.calls.values()),
            "snapshots": self.snapshots,
            "endpoints": rows,
        }


USAGE = UsageTracker()


# ---------- snapshot aggregation --------------------------------------------

def _safe_get_messages_summary(ws: str, sid: str):
    _, ms = h("POST", f"/v3/workspaces/{ws}/sessions/{sid}/messages/list", {})
    if isinstance(ms, dict):
        items = ms.get("items", []) or []
        total = ms.get("total", 0)
        last = items[-1] if items else None
        return {
            "count": total,
            "last": last,
            "char_total": sum(len((m.get("content") or "")) for m in items if isinstance(m, dict)),
        }
    return {"count": 0, "last": None, "char_total": 0}


def _safe_get_summary(ws: str, sid: str):
    _, sm = h("GET", f"/v3/workspaces/{ws}/sessions/{sid}/summaries")
    if isinstance(sm, dict):
        ss = sm.get("short_summary") or {}
        ls = sm.get("long_summary") or {}
        return {
            "short": ss.get("content") if isinstance(ss, dict) else None,
            "long": ls.get("content") if isinstance(ls, dict) else None,
            "short_len": len(ss.get("content", "")) if isinstance(ss, dict) and ss.get("content") else 0,
            "long_len": len(ls.get("content", "")) if isinstance(ls, dict) and ls.get("content") else 0,
        }
    return {"short": None, "long": None, "short_len": 0, "long_len": 0}


def dashboard_snapshot(workspace: str | None = None) -> dict:
    """Aggregate everything the dashboard wants in one call."""
    out: dict = {"workspaces": [], "active_workspace": workspace,
                 "queue": None, "sessions": [], "peers": [],
                 "activity": [], "totals": {},
                 "memory_state": {}, "usage": {}}

    # 1) workspaces
    s, body = h("POST", "/v3/workspaces/list", {})
    items = body.get("items", []) if isinstance(body, dict) else []
    out["workspaces"] = [{"id": w["id"], "created_at": w.get("created_at")} for w in items]
    if not out["active_workspace"] and out["workspaces"]:
        out["active_workspace"] = out["workspaces"][0]["id"]
    ws = out["active_workspace"]

    # 2) peers + sessions + queue
    s, body = h("POST", f"/v3/workspaces/{ws}/peers/list", {})
    peers = body.get("items", []) if isinstance(body, dict) else []
    s, body = h("POST", f"/v3/workspaces/{ws}/sessions/list", {})
    sessions = body.get("items", []) if isinstance(body, dict) else []
    s, queue = h("GET", f"/v3/workspaces/{ws}/queue/status")
    queue = queue if isinstance(queue, dict) else {}

    out["peers_base"] = [{"id": p["id"], "created_at": p.get("created_at")} for p in peers]

    # 3) per-session deep dive
    sess_detail = []
    total_msg_chars = 0
    total_short_sum_chars = 0
    total_long_sum_chars = 0
    for sess in sessions:
        sid = sess["id"]
        sd: dict = {"id": sid, "is_active": sess.get("is_active", True)}

        msgs = _safe_get_messages_summary(ws, sid)
        sd["message_count"] = msgs["count"]
        sd["last_message"] = (msgs["last"].get("content") if msgs["last"] else None)
        if sd["last_message"]:
            sd["last_message"] = sd["last_message"][:240]
        sd["last_peer"] = msgs["last"].get("peer_id") if msgs["last"] else None
        sd["last_ts"] = (msgs["last"].get("created_at") or msgs["last"].get("id")) if msgs["last"] else None
        sd["message_chars"] = msgs["char_total"]
        total_msg_chars += msgs["char_total"]

        sm = _safe_get_summary(ws, sid)
        sd["short_summary"] = sm["short"]
        sd["long_summary"] = sm["long"]
        sd["short_summary_chars"] = sm["short_len"]
        sd["long_summary_chars"] = sm["long_len"]
        total_short_sum_chars += sm["short_len"]
        total_long_sum_chars += sm["long_len"]

        sess_detail.append(sd)

    sess_detail.sort(key=lambda sd: sd["last_ts"] or "", reverse=True)
    out["sessions"] = sess_detail

    # 4) recent activity feed = last message per session
    feed = []
    for sd in sess_detail:
        if sd["last_message"]:
            feed.append({"session": sd["id"], "peer": sd["last_peer"],
                         "preview": sd["last_message"], "ts": sd["last_ts"] or ""})
    feed.sort(key=lambda x: x["ts"], reverse=True)
    out["activity"] = feed[:20]

    # 5) per-peer card + conclusion counts
    peer_detail = []
    total_card_chars = 0
    total_conclusions = 0
    _, concl_all = h("POST", f"/v3/workspaces/{ws}/conclusions/list", {})
    concl_items = concl_all.get("items", []) if isinstance(concl_all, dict) else []
    concl_total = concl_all.get("total", 0) if isinstance(concl_all, dict) else 0
    if isinstance(concl_total, (int, float)) and concl_total > len(concl_items):
        # best-effort: pull everything (Honcho pages). capped at 1000.
        more = []
        page = 2
        while len(more) < 1000 and len(concl_items) + len(more) < concl_total:
            _, c2 = h("POST", f"/v3/workspaces/{ws}/conclusions/list", {"page": page})
            if not isinstance(c2, dict) or not c2.get("items"):
                break
            more.extend(c2["items"])
            page += 1
        concl_items = concl_items + more

    for p in out["peers_base"]:
        pd = {"id": p["id"]}
        _, card = h("GET", f"/v3/workspaces/{ws}/peers/{p['id']}/card")
        if isinstance(card, dict):
            pc = card.get("peer_card")
            pd["has_card"] = pc is not None
            pc_str = pc if isinstance(pc, str) else (json.dumps(pc) if pc is not None else "")
            pd["card_size"] = len(pc_str)
            total_card_chars += len(pc_str)
        n = sum(1 for c in concl_items
                if c.get("peer_id") == p["id"] or c.get("peer") == p["id"])
        pd["conclusions"] = n
        total_conclusions += n
        peer_detail.append(pd)
    out["peers"] = peer_detail

    # 6) totals
    total_msgs = sum(sd["message_count"] for sd in sess_detail)
    out["totals"] = {
        "sessions": len(sess_detail), "peers": len(peer_detail),
        "messages": total_msgs,
        "wu_total": queue.get("total_work_units", 0),
        "wu_done": queue.get("completed_work_units", 0),
        "wu_progress": queue.get("in_progress_work_units", 0),
        "wu_pending": queue.get("pending_work_units", 0),
        "conclusions_total": total_conclusions,
    }
    out["queue"] = queue

    # 7) memory-state cost surrogates (Honcho API gives no real token counts)
    out["memory_state"] = {
        "message_chars": total_msg_chars,
        "short_summary_chars": total_short_sum_chars,
        "long_summary_chars": total_long_sum_chars,
        "peer_card_chars": total_card_chars,
        "conclusion_chars": sum(len(json.dumps(c)) for c in concl_items),
        # rough Tiktoken-ish English estimate: ~4 chars/token. NOT exact —
        # this is a surrogate, not a real count.
        "approx_tokens_surrogate": (total_msg_chars + total_short_sum_chars +
                                    total_long_sum_chars + total_card_chars) // 4,
    }

    # 8) usage: just the rolling-window summary (lightweight)
    USAGE.snapshot_total()
    out["usage"] = USAGE.summarise(window_seconds=60)
    return out


# ---------- HTTP server -----------------------------------------------------

INDEX_HTML = open(__file__.replace("server.py", "index.html")).read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
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
        if url.path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        if url.path == "/api/snapshot":
            qs = parse_qs(url.query)
            ws = (qs.get("workspace") or [None])[0]
            data = dashboard_snapshot(ws)
            return self._send(200, json.dumps(data).encode(), "application/json")
        if url.path == "/api/health":
            return self._send(200, b'{"ok":true}', "application/json")
        if url.path == "/api/usage":
            qs = parse_qs(url.query)
            ws = int((qs.get("window") or ["60"])[0])
            return self._send(200, json.dumps(USAGE.summarise(window_seconds=ws)).encode(),
                              "application/json")
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
            ok = 200 <= status < 300
            self._send(200 if ok else 502,
                       json.dumps({"ok": ok, "status": status, "resp": resp,
                                   "request": body}).encode(),
                       "application/json")
            return
        if url.path == "/api/usage/reset":
            USAGE.reset()
            return self._send(200, b'{"ok":true}', "application/json")
        return self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    print(f"Honcho dashboard: http://127.0.0.1:{PORT}/  (proxying {HONCHO})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
