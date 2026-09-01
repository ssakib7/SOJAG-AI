"""Per-turn context shared between the pipeline, the team's dynamic instructions, and
the tools — carried in a ContextVar so it reaches member agents and tool calls no matter
how Agno routes the run internally.

The scratch fields (lead / payment / lead_invalid) are how tool calls hand their
side-effect *intents* back to the pipeline: tools never write to the outbox themselves.
The pipeline executes the side effects only after the reply is actually sent — and a
regenerated (discarded) draft's scratch is thrown away with it, so a discarded draft
has zero side effects. (Parity with the old deferred-commit design.)
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnContext:
    sender_id: str
    session: dict[str, Any]
    combined_text: str = ""            # the whole burst, joined — used by trx-id fallback
    transcript: str = ""               # rendered conversation: payment verifier + the member's own prompt
    history_turns: int = 0             # committed messages before this one; 0 = nothing said yet
    replied_turns: int = 0             # of those, how many are OURS; 0 = we have never answered
    profile: dict[str, Any] | None = None
    ask_contact: bool = False          # deterministic contact-ask fires this turn
    offered_phone: str | None = None   # valid BD number the customer actually typed
    known_phone: str | None = None     # number already on file
    images: list[Any] = field(default_factory=list)  # agno Images the customer sent this turn
    # 0 on the first try, 1 on the retry after an empty reply. Carried on the context
    # rather than as a run_turn() argument so the pipeline's call signature stays
    # one-argument (every test stub for run_turn takes exactly the turn).
    attempt: int = 0

    # Scratch: set by tools during the run, read by the pipeline after the send.
    lead: dict[str, Any] | None = None
    payment: dict[str, Any] | None = None
    lead_invalid: bool = False         # save_lead was called with an unusable phone
    end_conversation: str | None = None  # off-topic force-stop, with the reason given
    notice: dict[str, Any] | None = None  # notify_team: {category, reason}

    extras: dict[str, Any] = field(default_factory=dict)


current_turn: ContextVar[TurnContext | None] = ContextVar("current_turn", default=None)


def get_turn() -> TurnContext:
    turn = current_turn.get()
    if turn is None:
        raise RuntimeError("no TurnContext bound — run_turn() must set it")
    return turn
