# dejure-agent

De Jure Academy's Facebook Messenger sales bot ("SOJAG AI"), rebuilt on the
[Agno](https://docs.agno.com) agent framework. This is the Python successor to the
Node.js bot in `dejure-fb-bot`; that repo is now the frozen reference implementation.

Full architecture, rationale, and the behaviour-parity contract live in
`dejure-fb-bot/AGNO_REBUILD_PLAN.md`. The short version:

```
Facebook Page ──► FastAPI /webhook ──► per-sender queues ──► Team(route)
                  HMAC · dedupe          batching ·           ├─ KnowledgeAgent
                  blocklist edge-drop    regeneration ·       ├─ SalesAgent (save_lead, report_payment)
                                         humanized pacing     └─ LeadAgent  (save_lead)
                                                              + PaymentVerifier / Vision / Gender agents
Side effects (after the reply is sent): durable outbox ──► Google Sheet · Telegram
Storage: Postgres 16 + pgvector (sessions, customers, leads ledger, outbox, config)
Admin panel: /  /system-prompt  /blocked  /leads (new)
```

Everything left of the team is deliberately *not* Agno — per-sender serialization,
burst batching, regenerate-on-new-message, webhook dedupe, the 2000-char split,
persona-retry sends. That's where the production scar tissue lives; it ports 1:1.

## Run

```sh
cp .env.example .env       # fill in secrets
docker compose up -d       # bot on 127.0.0.1:3006, Postgres on 127.0.0.1:5433
sudo sh deploy/install.sh  # nightly backup + log rotation, then read its checklist
```

**The bot runs as exactly one process.** Per-sender queues, webhook dedupe, sessions, the
blocklist, follow-up timers and the prompt cache all live in its memory. A second replica
or `--workers 2` would answer the same customer twice and interleave one customer's
messages. Scale the single process, never the replica count.

### Before pointing ads at it

| Step | Why |
|---|---|
| Point an external monitor at `/health` (1–5 min) | It returns **503** when lead delivery is stuck, replies are not reaching Facebook, or the model is failing. Nothing else notices a broken bot. |
| Confirm the boot message arrives in Telegram | It reports lead-capture/payment-alert status and any config warning. No message = the team's alerting path is broken. |
| Check `MESSENGER_PAGE_TOKEN` is a **Page** token | The Conversations API refuses System User tokens (#190), and every alert then says `(নাম জানা যায়নি)` with no chat link. The boot self-check probes this. |
| Restore one backup into a scratch database | A backup nobody has restored is not a backup. |
| Review the knowledge base for stale dates | The bot quotes dates verbatim by design; nothing detects a batch that already started. |

### Operating it

- **A human replying from the Page inbox pauses the bot for that customer** (8h, refreshed
  on each human reply) — Meta echoes those messages without an `app_id`, which is how the
  bot tells a colleague's reply from its own. No handover configuration needed.
- **The publish switch** in the admin panel silences the bot for everyone at the webhook
  edge, without stopping the container — the right move during an incident.
- **Undo** on the knowledge-base editor restores the previous saved version; a save that
  would delete every course is refused outright.

Local development:

```sh
uv sync
uv run uvicorn app.main:app --port 3000   # needs DATABASE_URL (compose db works)
uv run pytest                             # no network, no API keys needed
```

## Migrating from the old bot

Stop the old bot first (its 10s state flush would race the copy), then:

```sh
uv run python scripts/migrate_json.py e:/dejure-fb-bot
```

Idempotent; `leads.jsonl` is the ledger of record. Undelivered outbox items resume
delivery on the next boot.

## Optional features (env flags, all off by default)

| Flag | What it does |
|---|---|
| `SHADOW_MODE=true` | Pre-cutover gate: full pipeline on mirrored webhook traffic, zero outward effects; drafts land in `shadow_drafts`. Diff against the live bot with `scripts/shadow_compare.py old-bot.log report.md`. |
| `KNOWLEDGE_RAG_ENABLED=true` | Agentic RAG over books/custom sections via Agno Knowledge + pgvector (hybrid search). The course catalog always stays in the prompt. Needs Postgres + `GEMINI_API_KEY`. |
| `AGENTOS_ENABLED=true` + `OS_SECURITY_KEY` | Mounts Agno's AgentOS control plane at `/os` (bearer-protected; the rest of the app is untouched). Point the AgentOS UI at `https://<host>/os`. |

`scripts/live_spike.py` runs a scripted Bengali conversation against the real model (phase-0
check); `scripts/latency_probe.py` isolates bare vs member vs team latency.

## Notes

- The Agno team runs with `add_history_to_context=False`: committed history is
  injected from our own `sessions` store, so a draft discarded by the regeneration
  loop leaves no trace (deferred-commit parity with the old bot).
- Tools record intent on a per-turn `TurnContext`; the pipeline performs real side
  effects (outbox, alerts) only after the reply is actually sent.
- `tests/test_e2e.py` boots the real app against a stubbed OpenAI-compatible LLM and
  stubbed Graph/Sheet endpoints — the whole lead-capture flow with zero real network.
- `tests/fixtures/kb_expected.md` was generated by the OLD bot's `kb.js`; the catalog
  renderer is pinned byte-identical to it.
