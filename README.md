# Honcho Control Dashboard

A local live dashboard for a self-hosted [Honcho](https://github.com/honcho-memory/honcho) instance.
Proxies the Honcho REST API and renders a single-page dashboard with live KPIs,
deriver queue state, session / peer / message drilldowns, and a "Dream Now" button.

## Why

The hosted Honcho dashboard shows dream progress and per-peer memory at a glance.
The open-source stack ships only the API. This project fills the gap with a
self-hostable equivalent you can run on the same machine as the Honcho server.

## What it shows

- Workspace tabs (auto-discovered via `POST /v3/workspaces/list`)
- KPIs: sessions, peers, messages, dream WUs done/total, in-progress count
- Deriver queue card with progress bar, status pill, animated when busy
- Per-session cards: message count, last actor, summary preview, last message
- Per-peer cards: peer-card populated? byte size, conclusions count
- Recent message feed (last-message-per-session, time-ordered)
- Activity chart (Chart.js): rolling 150s window of total messages + per-tick delta
- "Dream Now" button: schedules `omni` dream for the active workspace, viewer
  watches the queue fill and clear
- Live log of dashboard events

## Screenshot

```
HONCHO · LIVE          [pk-local] [hermes]    updated 19:54:01

[Sessions] [Peers] [Messages] [Dream WUs 2/2] [In Progress 0]
─────────────────────────────────────────────────────────────────
[ Deriver Queue                  ] [ Activity Stream             ]
  ● idle — awaiting input        ]   /\___/\___                  
  ████████████████████ 100%      ]   0  1m  total 40              
  2 / 2 work units completed     ]                               
  [DREAM NOW]  [Force Refresh]   ]                               
  ───────────────────────────    ]                               
  19:54 dream scheduled · ...    ]                               
```

(Visual treatment is dark with a neon grid background and pulsing dots; see
`index.html` for the full styling.)

## Run

```bash
python3 server.py            # default: http://127.0.0.1:7777
PORT=8888 python3 server.py  # any port you like
```

That's it. No dependencies beyond the Python stdlib. The only outbound
connection is to `http://127.0.0.1:8000` (your Honcho server). Edit the
`HONCHO = ...` constant at the top of `server.py` if Honcho is elsewhere.

## API proxy surface

- `GET  /`                    — dashboard HTML
- `GET  /api/snapshot?workspace=<id>` — aggregated dashboard payload
- `POST /api/dream?workspace=<id>`    — proxy to `POST /v3/workspaces/{id}/schedule_dream`,
   defaults `observer` to a non-`hermes` peer and `dream_type=omni`

## Roadmap / open questions

- Streaming event feed (Honcho supports SSE on some endpoints — wire one up)
- Embeddings panel once `EMBED_MESSAGES=true` in Honcho's `.env`
- Per-session message timeline (currently just first/last)
- Token / cost estimate (would need Honcho to surface token counts per message)
- "Pin a workspace" / multi-window mode for monitoring >1 ws at once
- Theme switcher (the dark neon is the default but a light alt would help readability)

## Stack

- Backend: Python stdlib `http.server.ThreadingHTTPServer` + `urllib.request`
- Frontend: vanilla JS, Chart.js via CDN, no build step
- Total LOC: ~700 across two files
