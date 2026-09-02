# dejure-agent

De Jure Academy's Facebook Messenger sales bot ("SOJAG AI"), rebuilt on the
[Agno](https://docs.agno.com) agent framework. This is the Python successor to the
Node.js bot in `dejure-fb-bot`; that repo is now the frozen reference implementation.

Full architecture, rationale, and the behaviour-parity contract live in
`dejure-fb-bot/AGNO_REBUILD_PLAN.md`. The short version:

```
Facebook Page ──► FastAPI /webhook ──► per-sender queues ──► SojagAgent
                  HMAC · dedupe          batching ·           one agent, one call per turn
                  blocklist edge-drop    regeneration ·       (save_lead · report_payment ·
                                         humanized pacing      notify_team · end_conversation)
                                                              + PaymentVerifier / Vision / Gender agents
Side effects (after the reply is sent): durable outbox ──► Google Sheet · Telegram
Storage: Postgres 16 + pgvector (sessions, customers, leads ledger, outbox, config)
Admin panel: /  /system-prompt  /blocked  /leads (new)
```

Everything left of the agent is deliberately *not* Agno — per-sender serialization,
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
| Point an external monitor at `/health` (1–5 min) | It returns **503** when the knowledge base is empty, a lead/payment/escalation write has failed, lead delivery is stuck, replies are not reaching Facebook, or the model is failing. Nothing else notices a broken bot. Not `/health/live` — that is the container's own probe and stays 200 through anything a restart cannot fix. |
| Confirm the boot log shows a non-zero knowledge base | `Knowledge base: N course(s) …`. An empty one is a valid-looking prompt with no facts in it: every reply becomes "a representative will contact you". The boot ping and `/health` both flag it. |
| Confirm the boot message arrives in Telegram | It reports lead-capture/payment-alert status and any config warning. No message = the team's alerting path is broken. |
| Check `MESSENGER_PAGE_TOKEN` is a **Page** token | The Conversations API refuses System User tokens (#190), and every alert then says `(নাম জানা যায়নি)` with no chat link. The boot self-check probes this. |
| Restore one backup into a scratch database | A backup nobody has restored is not a backup. |
| Review the knowledge base for stale dates | The bot quotes dates verbatim by design; nothing detects a batch that already started. |

### Operating it

- **A human replying from the Page inbox pauses the bot for that customer** — Meta echoes
  those messages without an `app_id`, which is how the bot tells a colleague's reply from
  its own. No handover *protocol* configuration is
  needed, but the app **must be subscribed to the `message_echoes` webhook field** (app
  level *and* the Page's own subscribed fields). Without it Meta delivers no echoes, the
  bot never learns a person stepped in, and it talks over the team with no error anywhere
  — which is exactly what it did for a week. Every echo now logs one line
  (`echo [psid] app_id=… → …`), so `docker compose logs bot | grep "echo \["` proves
  whether they arrive at all. The pause lasts 2h from the team's **last** message (it is
  pushed forward by every further reply, so a live exchange never expires underneath the
  person having it) and is deliberately shorter than the 4h session TTL — a longer pause
  freezes a thread the bot has already forgotten. **Blocked users → Recent senders** shows
  who owns each conversation and has a *Give back to bot* button to end a pause early.
- **The bot never apologises to a customer for its own machinery.** There is no fallback
  string left for "something went wrong" or "I can't answer right now" — both are deleted.
  When the model returns no text the bot retries once; when the model call fails outright,
  or a turn crashes after the messages were consumed, there is no retry. In every one of
  those cases the customer gets **nothing** and the team gets a Telegram page naming the
  cause, because they are the only people who can end it with the customer having an
  answer. Those two lines used to stack up under a customer's own messages, told them
  nothing they could act on, and made the chat look answered to anyone scrolling the Page
  inbox. Look for `🤐 … staying silent and paging the team` in the logs; for an empty
  reply the line above it (`produced NO reply text — …`) names the cause — `output=` near
  the `cap` with no tools is the model spending its whole budget on reasoning.
- **That apology can no longer be sent at all**, by anything. `graph.send_message` — the
  one function every customer-facing message passes through — drops any reply that both
  apologises and blames a technical failure, and returns "not delivered", so the team is
  paged and nothing enters history. Deleting the canned string was not enough on its own:
  the model can write that sentence itself, from the knowledge base or from an admin-edited
  prompt. Ordinary courtesy is untouched — "দুঃখিত স্যার, এই তথ্যটি আমাদের কাছে নেই" needs
  both halves to trip, and has only one. A block logs `🚫 … BLOCKED an apology-for-a-failure
  reply`; seeing that line means the model is writing them and the prompt needs a look.
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
check); `scripts/latency_probe.py` isolates bare vs prompt-only vs full-turn latency.

## Notes

- **One agent, not a team.** This was a route-mode Team with three members; the leader
  cost an extra model call on every turn, the members differed only by a one-line role
  string, and Agno's route-mode prompt ("respond without delegating" for anything the
  leader can handle) contradicted ours ("never answer the customer yourself") — when it
  took Agno's side the turn came back empty and the customer got an apology. A turn is now
  one model call, two if it uses a tool. See the module docstring in `app/agents/factory.py`
  before adding a router back.
- The agent runs with `add_history_to_context=False`: committed history is injected from
  our own `sessions` store as a rendered transcript in the prompt, so a draft discarded by
  the regeneration loop leaves no trace (deferred-commit parity with the old bot).
- Tools record intent on a per-turn `TurnContext`; the pipeline performs real side
  effects (outbox, alerts) only after the reply is actually sent.
- `tests/test_e2e.py` boots the real app against a stubbed OpenAI-compatible LLM and
  stubbed Graph/Sheet endpoints — the whole lead-capture flow with zero real network.
- `tests/fixtures/kb_expected.md` was generated by the OLD bot's `kb.js`; the catalog
  renderer is pinned byte-identical to it.
