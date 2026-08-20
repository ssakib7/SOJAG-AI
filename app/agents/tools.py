"""Customer-facing tools for the team members: save_lead and report_payment.

Ported semantics from server.js askLLM():
- save_lead: the number the customer actually TYPED (offered_phone) beats the model's
  echoed copy (models drop digits), falling back to the number already on file. Name
  must be >= 2 chars. Invalid phone -> the model is told to re-ask; nothing is saved.
- report_payment: every call is audited by a separate verifier model before the team is
  alerted (fail-open when the verifier is unreachable). Overruled reports return a
  rejection the model must act on ("do NOT tell the customer anything reached us").

Tools only record intent on the TurnContext scratch; the pipeline performs the real
side effects (outbox, Telegram) after the reply is sent — discarded drafts do nothing.
"""

from __future__ import annotations

import logging

from app.agents.specialists import verify_payment_claim
from app.agents.turn_context import get_turn
from app.utils.text import find_trx_id, normalize_phone, short_note

log = logging.getLogger(__name__)


def save_lead(name: str, phone: str, interest: str, remarks: str) -> dict:
    """Save a customer's contact details so a human representative can follow up. Call this ONLY
    after the customer has provided BOTH their name and their Bangladeshi mobile number in the
    conversation. Never invent or guess these values. Also summarise, for the human
    representative, what this customer wants and how to approach them.

    Args:
        name: The customer's name, exactly as they gave it.
        phone: The customer's Bangladeshi mobile number, e.g. 01712345678.
        interest: One short line (Bengali) on what this customer is interested in — the
            course/batch they asked about and what they actually want, based only on what
            they said in the conversation.
        remarks: One or two short lines (Bengali) advising the representative how to handle
            this customer on the call: unanswered questions, objections or hesitation, how
            urgent they seem, any preferred contact time. Write "তেমন কিছু জানা যায়নি" if
            there is nothing useful to add.
    """
    turn = get_turn()
    clean_name = str(name or "").strip()
    resolved_phone = turn.offered_phone or normalize_phone(phone or "") or turn.known_phone
    valid = len(clean_name) >= 2 and bool(resolved_phone)
    clean_interest = short_note(interest)
    clean_remarks = short_note(remarks)
    log.info(
        'save_lead: name="%s" phone="%s" %s | interest="%s" | remarks="%s"',
        clean_name, resolved_phone or phone or "", "valid" if valid else "REJECTED (bad name/phone)",
        clean_interest, clean_remarks,
    )
    if not valid:
        turn.lead_invalid = True
        return {
            "status": "invalid_phone",
            "note": "Not a valid Bangladeshi mobile number — ask the customer to resend it.",
        }
    turn.lead = {"name": clean_name, "phone": resolved_phone, "interest": clean_interest, "remarks": clean_remarks}
    return {"status": "ok", "note": "Saved. A representative will follow up shortly."}


async def report_payment(
    note: str,
    trx_id: str = "",
    method: str = "",
    amount: str = "",
    for_course: str = "",
    sender_name: str = "",
) -> dict:
    """Report to the team that this customer says they have ALREADY PAID (sent money / shared a
    transaction id / sent a payment screenshot), so a human can verify it against the account
    and confirm their enrollment. Call this as soon as the customer states a payment has been
    made — even without a transaction id or amount, and even if you doubt the claim. Do NOT
    call it for questions about fees, amounts, installments, discounts, or how/where to pay —
    those are ordinary questions to answer from the knowledge base. Never confirm, verify,
    approve or deny the payment yourself, and never invent any of these values: leave a field
    out when the customer did not say it. Call this at most once per claim.

    Args:
        note: One short line (Bengali) for the agent about anything NOT already captured in
            the other fields: what the customer still wants or is waiting for. Do NOT repeat
            the amount, transaction id or method here, and never write any figure the customer
            did not actually state.
        trx_id: The transaction/reference id exactly as the customer wrote it (e.g. bKash
            TrxID '8N7A2B3C4D'). Leave empty if they did not give one.
        method: Payment method if mentioned: bKash, Nagad, Rocket, bank transfer, cash, card.
        amount: Amount if mentioned, digits only, e.g. "22000".
        for_course: Which course/batch the payment is for, if clear from the conversation.
        sender_name: The account/number the money was sent from, if mentioned.
    """
    turn = get_turn()

    # Second opinion before the team is alerted: the conversation model occasionally grabs
    # this tool as a generic "tell the team" button. Unreachable verifier = fail open.
    verdict = await verify_payment_claim(turn.transcript)
    if verdict is not None and not verdict.is_claim:
        log.info("payment claim overruled by verifier: %s", verdict.reason)
        return {
            "status": "rejected_not_a_claim",
            "note": (
                "Review found the customer did NOT state that a payment was made, so nothing was reported. "
                "Do NOT tell the customer their payment information has reached us. Just answer their "
                "message normally from the knowledge base."
            ),
        }

    turn.payment = {
        "trxId": str(trx_id or "").strip() or find_trx_id(turn.combined_text) or "",
        "method": str(method or "").strip(),
        "amount": str(amount or "").strip(),
        "forCourse": str(for_course or "").strip(),
        "senderName": str(sender_name or "").strip(),
        "note": str(note or "").strip(),
    }
    return {
        "status": "reported_for_verification",
        "note": (
            "The team has been notified and will verify this payment against our account. "
            "Tell the customer warmly that their payment information has reached us and a "
            "representative will confirm shortly. You must NOT say the payment is confirmed, "
            "received, or verified, and must NOT confirm their enrollment or seat."
        ),
    }
