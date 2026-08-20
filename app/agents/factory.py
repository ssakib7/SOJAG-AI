"""The Agno team: a route-mode leader in front of Knowledge / Sales / Lead members.

Topology (per the approved plan): the leader classifies each turn and hands it to ONE
member, whose reply goes straight back to the customer (route mode — no synthesis hop).

- KnowledgeAgent — course/fee/schedule Q&A from the knowledge base. No tools.
- SalesAgent    — selling, enrollment, buying intent, payment claims. Tools: save_lead
                  AND report_payment (it keeps save_lead so a phone number dropped in a
                  price question is captured without a re-route).
- LeadAgent     — contact-details collection turns. Tool: save_lead.

All members share the SAME assembled persona/system prompt (Bengali, স্যার/ম্যাডাম, KB
adherence, guardrails) plus the per-turn dynamic blocks — one voice, three lanes. The
deterministic backstops (phone regex, payment-claim regex) run in the pipeline UNDER
this team, so a routing mistake can never drop a lead or a payment alert.

Conversation history is injected explicitly from the committed session history
(add_history_to_context=False): a draft discarded by the regeneration loop leaves no
trace anywhere Agno might read back.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from agno.agent import Agent
from agno.models.message import Message
from agno.team import Team
from agno.team.mode import TeamMode

from app.agents.model import build_model, guarded
from app.agents.tools import report_payment, save_lead
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

    profile = turn.profile or {}
    known_gender = profile.get("gender") or (
        profile.get("gender_guess") if profile.get("gender_guess") in ("male", "female") else None
    )
    if known_gender:
        name_part = f' ("{profile["name"]}")' if profile.get("name") else ""
        gendered = (
            "a man — use the male form of address (e.g. ভাইয়া or স্যার, per the style guidelines above)"
            if known_gender == "male"
            else "a woman — use the female form of address (e.g. আপু or ম্যাডাম, per the style guidelines above)"
        )
        out += (
            f"\n\n=== ADDRESSING THE CUSTOMER ===\nThis customer's Facebook profile{name_part} indicates they "
            f"are {gendered}. If the customer themselves say or imply otherwise in the conversation, follow "
            "the customer, not the profile."
        )
    elif profile.get("name"):
        out += (
            f"\n\n=== ADDRESSING THE CUSTOMER ===\nThis customer's Facebook profile name is "
            f"\"{profile['name']}\", which does NOT reliably indicate their gender. Unless the conversation "
            "itself makes their gender clear, follow the default address style from the guidelines above and "
            "do not use gendered kinship terms like ভাইয়া or আপু — a wrong one offends."
        )
    else:
        out += (
            "\n\n=== ADDRESSING THE CUSTOMER ===\nNothing is known about this customer's gender. Unless the "
            "conversation itself makes it clear, follow the default address style from the guidelines above "
            "and do not guess a gendered term like ভাইয়া or আপু."
        )

    if get_settings().payments_on:
        out += (
            "\n\n=== PAYMENTS ===\nWhen the customer states a payment has ALREADY been made — they sent money, "
            "shared a transaction id, said \"টাকা পাঠিয়েছি\" / \"payment korechi\", or answered yes to whether "
            "they paid — call report_payment. Do not call it for questions about fees, amounts, installments, "
            "or how/where to pay, or for promises to pay later. Never tell the customer their payment "
            "information has reached our team unless you are calling report_payment this very turn, and never "
            "say a payment is confirmed, received, or verified."
        )
    return out


def _member_instructions(run_context=None) -> str:  # noqa: ANN001 — Agno may pass a RunContext
    turn = current_turn.get()
    base = kb_config.system_prompt_full()
    return base + (_dynamic_blocks(turn) if turn else "")


def _offer_save_lead(turn: TurnContext | None) -> bool:
    s = get_settings()
    if not s.leads_on:
        return False
    lead = (turn.session.get("lead") if turn else None) or {}
    # Offered while we still need any of this customer's details: the phone, or the
    # name after a phone-only backstop capture.
    return not lead.get("captured") or not real_name(lead.get("name"))


def _sales_tools(run_context=None):  # noqa: ANN001
    turn = current_turn.get()
    tools = []
    if _offer_save_lead(turn):
        tools.append(save_lead)
    if get_settings().payments_on:
        tools.append(report_payment)
    return tools


def _lead_tools(run_context=None):  # noqa: ANN001
    return [save_lead] if _offer_save_lead(current_turn.get()) else []


@lru_cache
def build_team() -> Team:
    from app.kb.knowledge import get_knowledge

    rag = get_knowledge()  # None unless KNOWLEDGE_RAG_ENABLED (see kb/knowledge.py)
    knowledge = Agent(
        id="knowledge",
        name="KnowledgeAgent",
        role=(
            "Answers questions about courses, fees, schedules, books, contact details, and the academy "
            "itself, strictly from the knowledge base."
        ),
        model=build_model(),
        instructions=_member_instructions,
        knowledge=rag,
        search_knowledge=rag is not None,
        cache_callables=False,
        telemetry=False,
    )
    sales = Agent(
        id="sales",
        name="SalesAgent",
        role=(
            "Handles buying interest, enrollment, admissions, discounts, objections, payment questions and "
            "payment claims — and saves the customer's contact details the moment name + phone appear."
        ),
        model=build_model(),
        instructions=_member_instructions,
        tools=_sales_tools,
        cache_callables=False,
        telemetry=False,
    )
    lead = Agent(
        id="lead",
        name="LeadAgent",
        role=(
            "Handles turns whose main content is the customer sharing (or correcting) their name and mobile "
            "number, and turns dedicated to collecting contact details."
        ),
        model=build_model(),
        instructions=_member_instructions,
        tools=_lead_tools,
        cache_callables=False,
        telemetry=False,
    )
    return Team(
        name="SojagTeam",
        model=build_model(fast=True),  # routing only — no thinking budget, small output
        members=[knowledge, sales, lead],
        mode=TeamMode.route,
        determine_input_for_members=False,  # members see the customer's words unchanged
        instructions=(
            "You route each incoming Messenger turn from a De Jure Academy customer to exactly one member. "
            "Route to LeadAgent when the turn is mainly the customer sharing or correcting their name/mobile "
            "number. Route to SalesAgent when there is buying intent, enrollment/admission/payment talk, a "
            "claim of having paid, price negotiation, or contact details mixed into a sales question. Route "
            "to KnowledgeAgent for everything else: course, fee, schedule, book, eligibility and general "
            "questions. When unsure, prefer SalesAgent. Never answer the customer yourself."
        ),
        cache_callables=False,
        telemetry=False,
    )


def _history_messages(session: dict) -> list[Message]:
    """Committed history (legacy Gemini shape) -> Agno messages."""
    out: list[Message] = []
    for h in session.get("history") or []:
        text = "".join(p.get("text") or "" for p in (h.get("parts") or []))
        out.append(Message(role="assistant" if h.get("role") == "model" else "user", content=text))
    return out


def _render_transcript(history: list[Message], user_text: str) -> str:
    lines = [f"{'Assistant' if m.role == 'assistant' else 'Customer'}: {m.content}" for m in history]
    lines.append(f"Customer (latest message): {user_text}")
    return "\n".join(lines)


async def run_turn(turn: TurnContext) -> str:
    """Run one conversational turn through the team. Returns the reply text ('' when the
    model produced none — the caller picks the right fallback). Raises on hard LLM failure."""
    history = _history_messages(turn.session)
    turn.transcript = _render_transcript(history, turn.combined_text)
    messages = [*history, Message(role="user", content=turn.combined_text)]

    token = current_turn.set(turn)
    try:
        result = await guarded(
            lambda: build_team().arun(input=messages, add_history_to_context=False),
            "team-turn",
        )
    finally:
        current_turn.reset(token)

    content = result.content
    return content.strip() if isinstance(content, str) else ""


def reset_for_tests() -> None:
    build_team.cache_clear()
