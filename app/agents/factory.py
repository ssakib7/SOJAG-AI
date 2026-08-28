"""The Agno team: a route-mode leader in front of Knowledge / Sales / Lead members.

Topology (per the approved plan): the leader classifies each turn and hands it to ONE
member, whose reply goes straight back to the customer (route mode — no synthesis hop).

- KnowledgeAgent — course/fee/schedule Q&A from the knowledge base.
- SalesAgent    — selling, enrollment, buying intent, payment claims. Tools: save_lead
                  AND report_payment (it keeps save_lead so a phone number dropped in a
                  price question is captured without a re-route).
- LeadAgent     — contact-details collection turns. Tool: save_lead.

Every member also carries end_conversation, the off-topic force-stop: any of the three
can be handed an off-topic turn, and the router sends "everything else" to Knowledge.

All members share the SAME assembled persona/system prompt (Bengali, স্যার/ম্যাডাম, KB
adherence, guardrails) plus the per-turn dynamic blocks — one voice, three lanes. That
shared half is deliberately emitted FIRST, ahead of the cache breakpoint, so the three
members and every customer hit one prompt cache entry (see _member_system_message). The
deterministic backstops (phone regex, payment-claim regex) run in the pipeline UNDER
this team, so a routing mistake can never drop a lead or a payment alert.

Conversation history is injected explicitly from the committed session history
(add_history_to_context=False): a draft discarded by the regeneration loop leaves no
trace anywhere Agno might read back.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable

from agno.agent import Agent
from agno.models.message import Message
from agno.team import Team
from agno.team.mode import TeamMode

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
    # স্যার / ম্যাডাম only. The system prompt bans kinship terms outright, so knowing the gender
    # picks WHICH of the two honorifics to use when one is used — it never unlocks ভাইয়া/আপু as
    # an alternative, and it is not an instruction to use one this turn. That distinction is the
    # whole point of the wording below: this block is injected on EVERY turn, so phrasing it as
    # "address them as স্যার" read to the model as a per-turn order and produced a bot that
    # opened all but its first reply with "স্যার" — the honorific-frequency rule in the system
    # prompt decides HOW OFTEN, this block only decides WHICH.
    if known_gender:
        name_part = f' ("{profile["name"]}")' if profile.get("name") else ""
        gendered = (
            "a man, so the correct honorific for them is স্যার"
            if known_gender == "male"
            else "a woman, so the correct honorific for them is ম্যাডাম"
        )
        out += (
            f"\n\n=== ADDRESSING THE CUSTOMER ===\nThis customer's Facebook profile{name_part} indicates they "
            f"are {gendered}. Never use a kinship term like ভাইয়া or আপু, whatever the profile suggests. If "
            "the customer themselves say or imply otherwise in the conversation, follow the customer, not "
            "the profile."
        )
    elif profile.get("name"):
        out += (
            f"\n\n=== ADDRESSING THE CUSTOMER ===\nThis customer's Facebook profile name is "
            f"\"{profile['name']}\", which does NOT reliably indicate their gender. Unless the conversation "
            "itself makes their gender clear, the correct honorific for them is স্যার, and never ভাইয়া or আপু."
        )
    else:
        out += (
            "\n\n=== ADDRESSING THE CUSTOMER ===\nNothing is known about this customer's gender. Unless the "
            "conversation itself makes it clear, the correct honorific for them is স্যার, and never ভাইয়া "
            "or আপু."
        )
    out += (
        "\nThis tells you WHICH honorific is correct, not how often to use it — keep using it as "
        "sparingly as the writing-style rules above require, and do not open every reply with it."
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


# Member roles. Each is used twice: the leader reads Agent.role to route, and the member's
# own system message repeats it AFTER the cache breakpoint (see _member_system_message).
KNOWLEDGE_ROLE = (
    "Answers questions about courses, fees, schedules, books, contact details, and the academy "
    "itself, strictly from the knowledge base."
)
SALES_ROLE = (
    "Handles buying interest, enrollment, admissions, discounts, objections, payment questions and "
    "payment claims — and saves the customer's contact details the moment name + phone appear."
)
LEAD_ROLE = (
    "Handles turns whose main content is the customer sharing (or correcting) their name and mobile "
    "number, and turns dedicated to collecting contact details."
)


def _member_system_message(role: str, extra: str = "") -> Callable[[], str]:
    """Build a member's system message, ordered for prompt caching.

    Everything stable goes first — the assembled persona + knowledge base, byte-identical for
    all three members and every customer — then CACHE_BREAK, then everything that varies: the
    member's role and the per-turn dynamic blocks. That way the ~22k-token prefix is ONE cache
    entry rather than one per member per dynamic-block combination.

    This is why members take system_message= instead of instructions=: Agno's default assembly
    emits the agent's role BEFORE the instructions (agent/_messages.py get_system_message), so
    the cached prefix would differ per member from the first byte. Setting system_message makes
    Agno return that string verbatim; role= stays on the Agent purely for the leader's routing
    table, and `extra` carries what the bypassed assembly would have appended (the knowledge
    search instructions, when the RAG store is on).
    """

    def build() -> str:
        parts = [kb_config.system_prompt_full(), CACHE_BREAK, f"\n\n<your_role>\n{role}\n</your_role>"]
        if extra:
            parts.append(f"\n\n{extra}")
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


# end_conversation and notify_team are on every member unconditionally: any of the three
# can be handed an off-topic turn or a customer who needs a human, and an always-present
# tool keeps the cached prefix stable.
BASE_TOOLS = [end_conversation, notify_team]


def _sales_tools(run_context=None):  # noqa: ANN001
    turn = current_turn.get()
    tools = [*BASE_TOOLS]
    if _offer_save_lead(turn):
        tools.append(save_lead)
    if get_settings().payments_on:
        tools.append(report_payment)
    return tools


def _lead_tools(run_context=None):  # noqa: ANN001
    tools = [*BASE_TOOLS]
    if _offer_save_lead(current_turn.get()):
        tools.append(save_lead)
    return tools


@lru_cache
def build_team() -> Team:
    from app.kb.knowledge import get_knowledge

    rag = get_knowledge()  # None unless KNOWLEDGE_RAG_ENABLED (see kb/knowledge.py)
    knowledge = Agent(
        id="knowledge",
        name="KnowledgeAgent",
        role=KNOWLEDGE_ROLE,
        model=build_model(),
        system_message=_member_system_message(
            KNOWLEDGE_ROLE, rag.build_context() if rag is not None else ""
        ),
        tools=BASE_TOOLS,
        knowledge=rag,
        search_knowledge=rag is not None,
        cache_callables=False,
        telemetry=False,
    )
    sales = Agent(
        id="sales",
        name="SalesAgent",
        role=SALES_ROLE,
        model=build_model(),
        system_message=_member_system_message(SALES_ROLE),
        tools=_sales_tools,
        cache_callables=False,
        telemetry=False,
    )
    lead = Agent(
        id="lead",
        name="LeadAgent",
        role=LEAD_ROLE,
        model=build_model(),
        system_message=_member_system_message(LEAD_ROLE),
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
    model produced none — the caller picks the right fallback). Raises on hard LLM failure.

    turn.images (a photo the customer just sent) rides along to the routed member, so the
    member answers the actual picture — a course poster, a book page, a website error —
    rather than a one-line caption of it."""
    history = _history_messages(turn.session)
    turn.transcript = _render_transcript(history, turn.combined_text)
    messages = [*history, Message(role="user", content=turn.combined_text)]

    token = current_turn.set(turn)
    try:
        result = await guarded(
            lambda: build_team().arun(input=messages, images=turn.images or None,
                                      add_history_to_context=False),
            "team-turn",
        )
    finally:
        current_turn.reset(token)

    content = result.content
    return content.strip() if isinstance(content, str) else ""


def reset_for_tests() -> None:
    build_team.cache_clear()
