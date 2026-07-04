# Honcho Control Dashboard

A local, live dashboard for a self-hosted [Honcho](https://github.com/plastic-labs/honcho)
instance. Polls Honcho's REST API every 2.5s, surfaces deriver queue state,
session / peer / message drilldowns, a "Dream Now" button to schedule
consolidation runs, and proxy-usage telemetry.

```
Sessions: 4        Peers: 4        Messages: 54       Conclusions: 0
Dream WUs: 22/22   Deriver: 0 in progress
─────────────────────────────────────────────────────────────────
[ Deriver Queue ]                            [ Message Activity ]
  ● processing                                /\___/‾‾\___       2m window
  ████████████████  100%
  22 / 22 work units completed  ·  0 pending ·  0 in progress
  [ Run dream now ]  [ Refresh ]
```

## Run

```bash
git clone https://github.com/PraveenKumarSridhar/honcho-control-dashboard.git
cd honcho-control-dashboard
python3 server.py            # http://127.0.0.1:7777
```

That's it. Stdlib only — no `pip install` step. Edit `HONCHO = ...` at the
top of `server.py` if Honcho isn't on `127.0.0.1:8000`.

For a complete from-scratch walkthrough (Honcho + dashboard + .env + first
dream), read **[SELF_HOST.md](./SELF_HOST.md)**.

## What it shows

- **KPIs** — Sessions, Peers, Messages, Conclusions, Dream WUs, Deriver in progress
- **Deriver Queue** — progress bar, pending/in-progress counts, Run Dream Now button
- **Message Activity** — rolling 2-minute chart (total + delta)
- **Sessions** — per-session cards with message count, character count, last actor, summary preview, relative timestamp + freshness chip
- **Peers** — per-peer card with peer-card size, conclusion count
- **Recent Messages** — last message from each session, peer chip + freshness chip
- **Memory State** — character counts per source + an approximate token total (Honcho API doesn't expose real token counts; this is a `chars÷4` surrogate, labeled honestly)
- **Proxy Usage** — calls/sec, latency, error rates per Honcho endpoint, rolling 60s
- **Workspace Totals** — flat list of aggregate counts

Tooltips (ⓘ) on every panel and KPI explain what each metric means.

A yellow **advisor banner** appears automatically when the deriver is
processing but no conclusions are landing — usually means the deriver model
doesn't support structured output, or the API key env var resolved empty.
See the troubleshooting section of `SELF_HOST.md` for the full list.

## Design choices

- **No external font CDN.** System fonts only. Works offline, fast first paint.
- **Light + dark mode via `prefers-color-scheme`.** No theme switcher.
- **Tabular numerals** on every metric so digits don't jitter during polling.
- **Zero framework.** Vanilla JS, Chart.js via CDN, no build step.
- **Stdlib server.** `http.server.ThreadingHTTPServer` is enough.
- **No modifications to Honcho.** The dashboard is a pure consumer of the v3 REST API.

## Differences from the hosted product

The hosted [Honcho](https://honcho.dev) dashboard includes some things this
dashboard can't replicate:

- Real token / cost numbers (the open-source Honcho REST API does not expose them)
- Multi-tenant analytics
- Per-message embedding visualizations

The "Memory State" panel here is a `chars÷4` **surrogate**; it is labeled as
such in the UI and on the panel footer. Don't mistake it for real telemetry.

## Repo layout

```
honcho-control-dashboard/
├── server.py        # 365 lines. Stdlib HTTP server, UsageTracker, Diagnostics.
├── index.html       # ~800 lines. Single page, no build step.
├── SELF_HOST.md     # Complete from-scratch guide.
└── README.md        # This file.
```

## Contributing

Issues and PRs welcome. There is no contribution guide yet; reasonable PRs
that improve clarity or fix real bugs land quickly.

Roadmap items live in the source comments of `server.py`. Open issues for
new ones.

## License

MIT. See Honcho's own license for the upstream API surface we depend on.
