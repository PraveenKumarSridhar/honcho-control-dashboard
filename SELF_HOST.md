# Self-Hosting Honcho + This Dashboard

This document is a from-scratch guide for running Honcho (Honcho's memory
layer) locally and pointing the dashboard at it. It is *not* required reading
if you already have Honcho running — but if you're new to either, start here.

## TL;DR

```bash
# 1. Get Honcho
git clone https://github.com/plastic-labs/honcho.git ~/honcho
cd ~/honcho

# 2. Add your LLM provider's API key (any OpenAI-compatible endpoint works)
echo "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://api.your-provider.com/v1"  >> .env
echo "DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=YOUR_KEY_ENV_VAR_NAME"          >> .env

# 3. Start it
docker compose up -d

# 4. Get this dashboard
git clone https://github.com/PraveenKumarSridhar/honcho-control-dashboard.git
cd honcho-control-dashboard
python3 server.py
# → http://127.0.0.1:7777
```

## What is this for

Honcho's hosted dashboard shows deriver progress and per-peer memory state
at a glance. The open-source stack ships only the REST API. This dashboard
fills that gap.

What you see in the UI:

- A live view of how many messages, sessions, peers, and conclusions Honcho
  has accumulated
- The deriver queue depth — pending, in-progress, and completed work units
- A "Dream Now" button that schedules an `omni` dream (a memory
  consolidation run) and watches the queue move
- A per-session drilldown (latest message, summary preview, message count)
- A per-peer card (peer card size, conclusion count)
- A live event log of what you've done
- Proxy usage metrics: latency, error rates, throughput per endpoint

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser (you)                                            │
│      ↓ GET /api/snapshot                                  │
│      ↓ GET /api/dream                                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │  honcho-control-dashboard/server.py                │  │
│  │  stdlib HTTP server on 127.0.0.1:7777              │  │
│  │  UsageTracker wraps every Honcho call              │  │
│  │  Diagnostics: banners when state looks wrong       │  │
│  └────────────────────┬───────────────────────────────┘  │
│                       ↓                                    │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Honcho (your local stack, untouched)              │  │
│  │  FastAPI on 127.0.0.1:8000                         │  │
│  │      ├─ Deriver worker                             │  │
│  │      ├─ pgvector / pgvector:pg16 (PostgreSQL)      │  │
│  │      └─ redis:7-alpine                             │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**Zero modifications to Honcho.** The dashboard is a pure consumer of Honcho's
REST API. If Honcho's API changes, this dashboard will keep running with
empty panels and visible usage errors — but the source will not crash.

## Detailed setup

### 1. Honcho stack

If you're already running Honcho (e.g. from
[elkimek/honcho-self-hosted](https://github.com/elkimek/honcho-self-hosted)
or your own compose file), skip to step 2.

Minimum Honcho `.env` for the dashboard to be useful:

```bash
# Database / cache
DATABASE_URL=postgresql+asyncpg://honcho:honcho@database:5432/honcho
REDIS_URL=redis://redis:6379

# Auth off for local dev (dashboard assumes no auth)
AUTH_USE_AUTH=false

# Deriver
DERIVER_ENABLED=true
DERIVER__FLUSH_ENABLED=true
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=your-model-name
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://api.your-provider.com/v1
DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY=sk-...

# Summary / Dialectic similar pattern
```

Critical: the model you pick for `DERIVER_MODEL_CONFIG__MODEL` **must support
structured output / tool calling**. Honcho's deriver uses tool use to extract
conclusions from messages. If your model rejects structured output, the
deriver will appear to run (queue WUs tick up) but no conclusions will
ever be written — exactly what the dashboard's diagnostic banner
warns you about.

### 2. Verify Honcho is up

```bash
curl -s http://127.0.0.1:8000/health
# {"detail":"Not Found"} or similar is fine — that means the API answered

curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8000/v3/workspaces/list | python3 -m json.tool
```

If the workspaces list returns, you're good.

### 3. Run the dashboard

```bash
git clone https://github.com/PraveenKumarSridhar/honcho-control-dashboard.git
cd honcho-control-dashboard
python3 server.py
```

Opens at http://127.0.0.1:7777. Edit `HONCHO = "http://..."` at the top of
`server.py` if your Honcho runs somewhere other than `127.0.0.1:8000`.

## Troubleshooting

### "Dream completed" but the memory state panel doesn't change

The dashboard *does* update on every poll. The likely cause is upstream:
Honcho's deriver is processing work units but not extracting conclusions.

Three causes, in order of frequency:

1. **Deriver model doesn't support structured output.**
   Check your Honcho `.env` for `DERIVER_MODEL_CONFIG__MODEL` and confirm the
   model supports tool use. M2.5-highspeed, for example, does not. M3 does.

2. **API key env var resolves to empty.**
   Run `docker inspect honcho-deriver --format '{{range .Config.Env}}{{println .}}{{end}}'`
   and check that `LLM_*_API_KEY` resolves to a real value (not an empty
   string). Honcho silently retries on auth errors, so you only see the
   failure in `docker logs honcho-deriver`.

3. **Batch gate too high for your message volume.**
   Set `DERIVER__REPRESENTATION_BATCH_MAX_TOKENS` to a smaller value
   (e.g. 512) if your sessions are short and you're not seeing summaries.

The dashboard surfaces a yellow banner when this state is detected so you
don't have to dig through Honcho logs to find it.

### Cards are populated but the "Proxy Usage" panel shows errors

The dashboard tracks every call to Honcho. If you see `5✗` next to an
endpoint, that endpoint is returning ≥400. Check Honcho logs first, then
this dashboard's `server.log` for the upstream status code.

### "Polling failed" in the event log

The dashboard itself can't reach Honcho. Verify:
- `curl http://127.0.0.1:8000/health` works
- That `HONCHO = ...` at the top of `server.py` points at the right host
- That no firewall has dropped traffic between them

## Differences from elkimek/honcho-self-hosted

The `elkimek/honcho-self-hosted` repo is a similar project focused on running
Honcho itself — it ships a setup script, model tier recommendations, and a
production-friendly compose layout. This dashboard is complementary:

|                                  | elkimek/honcho-self-hosted | honcho-control-dashboard |
|----------------------------------|----------------------------|--------------------------|
| Ships Honcho                     | yes                        | no (consumer only)       |
| Ships a UI                       | no                         | yes                      |
| Modifies Honcho source           | no                         | no                       |
| Production deployment guide      | yes                        | no                       |
| Polling/proxy telemetry          | no                         | yes                      |
| Self-host guide                  | yes                        | yes (this document)      |

You can run this dashboard on top of an elkimek-installed Honcho, or any
other Honcho deployment. There is no dependency or required pairing.

## Project layout

```
honcho-control-dashboard/
├── server.py        # Stdlib HTTP server. Aggregates Honcho data, tracks proxy usage.
├── index.html       # Single-page frontend. No build step. No external font CDN.
├── SELF_HOST.md     # This document.
└── README.md        # Repo readme with screenshots and dev workflow.
```

## Development

```bash
git clone https://github.com/PraveenKumarSridhar/honcho-control-dashboard.git
cd honcho-control-dashboard

# Edit server.py or index.html, then:
python3 server.py
# Edit a file → refresh browser → it just works (no build step).

# When ready:
git add -A
git commit -m "feat: ..."
git push
```
