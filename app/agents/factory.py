"""ONE agent answers every customer turn.

This was a route-mode Team — a leader that classified each turn and delegated to one of
KnowledgeAgent / SalesAgent / LeadAgent. It is a single agent now, and the reasons are
worth keeping, because "add a router" is a tempting thing to put back:

- The three members were never really three. They shared one persona, one knowledge base
  and one voice; they differed by a one-line <your_role> string and by which tools they
  carried. Sales already held every tool the other two did, so collapsing them loses no
  capability — only the routing decision itself.
- The leader cost a whole extra model call on every single turn, for a decision whose
  wrong answer was nearly free.
- Worst, the leader could silently swallow a turn. Agno's own route-mode instruction tells
  the leader "for requests you can handle directly … respond without delegating", while
  ours said "never answer the customer yourself" — a contradiction inside one 2.2k-char
  prompt, on a model given 512 output tokens. When it took Agno's side it produced no
  member response and no text, which reached the pipeline as an empty reply and reached
  the customer as an apology. Deleting the leader deletes that failure mode outright.

What the agent carries: the assembled persona + knowledge base + guardrails, then
CACHE_BREAK, then the per-turn dynamic blocks. The stable half comes FIRST so every
customer and every turn hits one prompt cache entry (see _system_message). The
deterministic backstops (phone regex, payment-claim regex) run in the pipeline UNDER it,
so a missed tool call can never drop a lead or a payment alert.

Conversation history is injected explicitly, as a rendered transcript inside the prompt
(add_history_to_context=False, and the turn's own message is the only thing on the input):
a draft discarded by the regeneration loop leaves no trace anywhere Agno might read back.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable

from agno.agent import Agent
from agno.models.message import Message

from app.agents.model import CACHE_BREAK, build_model, guarded
from app.agents.tools import end_conversation, notify_team, report_payment, save_lead
from app.agents.turn_context import TurnContext, current_turn
from app.config import get_settings
from app.kb import config as kb_config
from app.utils.text import real_name

log = logging.getLogger(__name__)


# --- Per-turn dynamic instruction blocks (port of askLLM's instruction assembly) ---
def _dynamic_blocks(turn: TurnContext) -> str:
    session = turn.session
    lead = session.get("lead") or {}
    out = ""

    # The conversation, written into the prompt — because it does NOT arrive any other way.
    # run_turn sends only this turn's words as the input; the history is here, rendered,
    # and that is deliberate. It began as a workaround (the old route-mode Team collapsed
    # the input list into one user turn and dropped every assistant reply before the member
    # saw it, so the model could not tell it had already greeted, already introduced the
    # academy, already answered — and re-did all three every turn, which is what customers
    # saw). It stays because it is strictly better than the alternative: the transcript
    # carries the read-it-first instructions with it, costs the same tokens as real message
    # turns would, and keeps what the model sees under our control rather than Agno's.
    # It lands after CACHE_BREAK, so the cached prefix is untouched.
    #
    # The greeting is suppressed on turn.replied_turns, NOT on the transcript being
    # non-empty: history can now hold the customer's messages with no reply beside them
    # (a turn the model could not answer commits their half alone), and a customer whose
    # very first message we failed to answer must still be welcomed when they try again.
    if turn.history_turns and turn.transcript:
        out += "\n\n=== CONVERSATION SO FAR ===\n"
        if turn.replied_turns:
            out += (
                "This is NOT the first message of this conversation. Everything you and the "
                "customer have already said is below; the customer's newest message is the last "
                "line, and also arrives as your user turn.\nRead it before you reply: do NOT "
                "greet, do NOT welcome, do NOT say স্বাগতম, do NOT re-introduce De Jure Academy, "
                "and do not repeat a fact, a price, or a question you have already given.\n\n"
            )
        else:
            out += (
                "The customer has written to us before, but we have NOT answered them yet — a "
                "technical failure ate our reply, and they have had silence from us so far. "
                "Their earlier messages are below and their newest one is the last line.\nAnswer "
                "ALL of it in this one reply, including whatever they asked before. This IS your "
                "first reply, so greet them as you normally would. Do not apologise for the "
                "delay, do not mention a problem, and do not draw attention to the silence.\n\n"
            )
        out += turn.transcript

    if session.get("returning") and real_name(session.get("customer_name")):
        out += (
            f"\n\n=== RETURNING CUSTOMER ===\nThis customer has contacted us before; their name is "
            f"{session['customer_name']}, and we already have their contact details. You may warmly address "
            "them by name when it feels natural, you must NOT ask for their name or phone number again, and "
            "you must NOT call save_lead for them."
        )
    elif turn.ask_contact:
        out += (
            "\n\n=== ASK FOR CONTACT THIS TURN ===\nThe customer has engaged enough. After answering their "
            "message — and only if you have not already asked earlier in this conversation — follow the "
            "lead-capture guidance above: proactively offer a representative callback and ask them to send "
            "their name and mobile number together in one message. Do not call save_lead until they actually "
            "provide both."
        )
    elif lead.get("captured") and not real_name(lead.get("name")):
        out += (
            "\n\n=== NAME STILL UNKNOWN ===\nWe already have this customer's mobile number on file, but not "
            "their name. Do NOT ask for their number again. If they tell you their name (or you can clearly "
            "see it in their message), call save_lead with that name and the mobile number they gave earlier "
            "in this conversation."
        )

    # Which honorific — one short line. Everything that EXPLAINS the honorific rule lives in
    # HONORIFIC_POLICY, inside the cached prefix: it is byte-identical for every customer and
    # every turn, and paying full input rate for it on every single call was pure waste.
    # What genuinely varies is one word, and that is all that is left here.
    profile = turn.profile or {}
    known_gender = profile.get("gender") or (
        profile.get("gender_guess") if profile.get("gender_guess") in ("male", "female") else None
    )
    if known_gender == "female":
        which = "ম্যাডাম (Madam in a Banglish reply) — their Facebook profile indicates a woman"
    elif known_gender == "male":
        which = "স্যার (Sir in a Banglish reply) — their Facebook profile indicates a man"
    else:
        which = "স্যার (Sir in a Banglish reply) — their gender is not known"
    out += f"\n\n=== THIS CUSTOMER'S HONORIFIC ===\n{which}."
    return out


# --- Static instruction blocks: same bytes for every customer and every turn ---
#
# These sit in the CACHED prefix rather than in _dynamic_blocks. They were written as
# dynamic blocks because they are assembled per turn, but nothing in them actually varies:
# emitting them after CACHE_BREAK meant paying full input rate for ~900 identical characters
# on every member call, and twice on any turn with a tool call. Only the one line that
# genuinely differs per customer (which honorific) stays in the dynamic tail.

# The honorific rule. Knowing a customer's gender picks WHICH of স্যার/ম্যাডাম is correct when
# one is used — it never unlocks ভাইয়া/আপু, and it is not an instruction to use one this turn.
# That distinction is the whole point of the wording: phrased as "address them as স্যার" the
# model read it as a per-turn order and opened all but its first reply with "স্যার".
HONORIFIC_POLICY = """

=== HONORIFICS ===
The block below headed "THIS CUSTOMER'S HONORIFIC" tells you WHICH honorific is correct for the person \
you are talking to, not how often to use it — keep using it as sparingly as the writing-style rules \
require, and do not open every reply with it. Never use a kinship term like ভাইয়া or আপু, whatever the \
customer's profile suggests. If the customer themselves say or imply a different gender in the \
conversation, follow the customer, not the profile."""

PAYMENTS_POLICY = """

=== PAYMENTS ===
When the customer states a payment has ALREADY been made — they sent money, shared a transaction id, \
said "টাকা পাঠিয়েছি" / "payment korechi", or answered yes to whether they paid — call report_payment. Do \
not call it for questions about fees, amounts, installments, or how/where to pay, or for promises to pay \
later. Never tell the customer their payment information has reached our team unless you are calling \
report_payment this very turn, and never say a payment is confirmed, received, or verified."""


def _static_policy_blocks() -> str:
    """The stable half's tail: policies that belong in the cache, not in the per-turn tail.

    payments_on is read from settings, which do not change while the process runs, so this
    stays byte-identical across turns — the property the prompt cache is prefix-matched on.
    """
    return HONORIFIC_POLICY + (PAYMENTS_POLICY if get_settings().payments_on else "")


def _system_message(extra: str = "") -> Callable[[], str]:
    """Build the agent's system message, ordered for prompt caching.

    Everything stable goes first — the assembled persona, knowledge base and policy blocks,
    byte-identical for every customer and every turn — then CACHE_BREAK, then the per-turn
    dynamic blocks. That way the ~24k-token prefix is ONE cache entry instead of one per
    dynamic-block combination.

    This takes system_message= rather than instructions= because Agno's default assembly
    emits the agent's name/role and its own scaffolding BEFORE the instructions
    (agent/_messages.py get_system_message), which would put varying text ahead of the
    cached prefix. Setting system_message makes Agno return this string verbatim, so
    `extra` carries what the bypassed assembly would have appended — the knowledge-search
    instructions, when the RAG store is switched on.
    """

    def build() -> str:
        parts = [kb_config.system_prompt_full(), _static_policy_blocks()]
        if extra:
            parts.append(f"\n\n{extra}")
        parts.append(CACHE_BREAK)
        turn = current_turn.get()
        if turn:
            parts.append(_dynamic_blocks(turn))
        return "".join(parts)

    return build


def _offer_save_lead(turn: TurnContext | None) -> bool:
    s = get_settings()
    if not s.leads_on:
        return False
    lead = (turn.session.get("lead") if turn else None) or {}
    # Offered while we still need any of this customer's details: the phone, or the
    # name after a phone-only backstop capture.
    return not lead.get("captured") or not real_name(lead.get("name"))


# end_conversation and notify_team are unconditional: any turn can turn out to be off-topic
# or to need a human, and an always-present tool keeps the tool list stable.
BASE_TOOLS = [end_conversation, notify_team]


def _tools(run_context=None):  # noqa: ANN001
    """Resolved per run. save_lead disappears once we have everything we need from this
    customer, so the model is not tempted to re-ask; report_payment follows the switch."""
    tools = [*BASE_TOOLS]
    if _offer_save_lead(current_turn.get()):
        tools.append(save_lead)
    if get_settings().payments_on:
        tools.append(report_payment)
    return tools


@lru_cache
def build_agent() -> Agent:
    from app.kb.knowledge import get_knowledge

    rag = get_knowledge()  # None unless KNOWLEDGE_RAG_ENABLED (see kb/knowledge.py)
    return Agent(
        id="sojag",
        name="SojagAgent",
        model=build_model(),
        system_message=_system_message(rag.build_context() if rag is not None else ""),
        tools=_tools,
        knowledge=rag,
        search_knowledge=rag is not None,
        cache_callables=False,  # the system message and tool list are resolved per run
        telemetry=False,
    )


def _history_messages(session: dict) -> list[Message]:
    """Committed history (legacy Gemini shape) -> Agno messages.

    Consecutive same-role entries are merged into one message. History is no longer
    strictly alternating: a turn the bot could not answer commits the customer's words
    ALONE (state.remember_unanswered), so two customer messages in a row is now a normal
    shape — and back-to-back user turns are a shape some providers reject outright.
    """
    out: list[Message] = []
    for h in session.get("history") or []:
        text = "".join(p.get("text") or "" for p in (h.get("parts") or []))
        role = "assistant" if h.get("role") == "model" else "user"
        if out and out[-1].role == role:
            out[-1].content = f"{out[-1].content}\n{text}"
        else:
            out.append(Message(role=role, content=text))
    return out


def _render_transcript(history: list[Message], user_text: str) -> str:
    lines = [f"{'Assistant' if m.role == 'assistant' else 'Customer'}: {m.content}" for m in history]
    lines.append(f"Customer (latest message): {user_text}")
    return "\n".join(lines)


def _empty_reply_diagnosis(result) -> str:  # noqa: ANN001 — Agno RunOutput
    """Why did this run come back with nothing to say?

    By the time the pipeline sees an empty string every clue is gone, and an empty reply is
    a failure the CUSTOMER sees — so the one line we log about it has to tell the causes
    apart without a reproduction: the model spending its whole output budget on reasoning
    (output tokens at the cap, no text), and the model replying with tool calls only.
    """
    bits: list[str] = []
    status = getattr(result, "status", None)
    if status is not None:
        bits.append(f"status={getattr(status, 'value', status)}")
    tools = [t.tool_name for t in (getattr(result, "tools", None) or []) if getattr(t, "tool_name", None)]
    bits.append(f"tools={','.join(tools) or '-'}")
    metrics = getattr(result, "metrics", None)
    if metrics is not None:
        bits.append(
            f"tokens: input={getattr(metrics, 'input_tokens', 0)} "
            f"output={getattr(metrics, 'output_tokens', 0)} "
            f"reasoning={getattr(metrics, 'reasoning_tokens', 0)} "
            f"(cap {get_settings().llm_max_tokens})"
        )
    return " ".join(bits)


async def run_turn(turn: TurnContext) -> str:
    """Run one conversational turn. Returns the reply text ('' when the model produced none
    — the caller retries, then withholds). Raises on hard LLM failure.

    turn.images (a photo the customer just sent) rides along, so the agent answers the
    actual picture — a course poster, a book page, a website error — rather than a
    one-line caption of it.

    The conversation reaches the model as the rendered transcript inside the system prompt,
    NOT as prior messages on the input: only this turn's words are sent. Sending both would
    put the whole history on the wire twice for exactly one reading of it.
    """
    history = _history_messages(turn.session)
    turn.transcript = _render_transcript(history, turn.combined_text)
    turn.history_turns = len(history)
    turn.replied_turns = sum(1 for m in history if m.role == "assistant")

    token = current_turn.set(turn)
    try:
        result = await guarded(
            lambda: build_agent().arun(input=turn.combined_text, images=turn.images or None,
                                       add_history_to_context=False),
            "turn" if not turn.attempt else f"turn-retry-{turn.attempt}",
        )
    finally:
        current_turn.reset(token)

    content = result.content
    text = content.strip() if isinstance(content, str) else ""
    if not text:
        log.error("[%s] the model produced NO reply text — %s",
                  turn.sender_id, _empty_reply_diagnosis(result))
    return text


def reset_for_tests() -> None:
    build_agent.cache_clear()
