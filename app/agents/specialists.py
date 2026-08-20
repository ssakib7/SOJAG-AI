"""Forced, non-conversational specialist agents (outside the team):

- PaymentVerifier — second opinion auditing every report_payment call (fail-open).
- VisionClassifier — is this image actually a payment receipt? (fail-open at call site)
- GenderClassifier — one strict, cached classification per customer profile name.

Each uses output_schema so the verdict is a validated object, never free text.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from agno.agent import Agent
from agno.media import Image
from pydantic import BaseModel, Field

from app.agents.model import build_model, guarded
from app.utils.text import short_note

log = logging.getLogger(__name__)


# --- Payment claim verifier --------------------------------------------------
class PaymentVerdict(BaseModel):
    is_claim: bool = Field(
        description=(
            "TRUE only if the customer is stating that a payment has ALREADY been made or is being made right "
            "now — they sent money, shared a transaction id, said \"টাকা পাঠিয়েছি\"/\"payment korechi\", or are "
            "answering yes to whether they paid. FALSE for everything else: giving their name or phone number, "
            "stating which course they want, asking about fees/amounts/installments/discounts, asking how or "
            "where to pay, or saying they WILL pay later. If unsure, FALSE."
        )
    )
    reason: str = Field(description="One short sentence explaining the verdict.")


@lru_cache
def _verifier() -> Agent:
    return Agent(
        name="PaymentVerifier",
        model=build_model(),
        instructions=(
            "You audit a sales chatbot for a Bangladeshi law-coaching academy. Read the conversation and "
            "judge ONLY the customer's latest message. Be strict: reporting a payment that was never claimed "
            "wastes the team's time."
        ),
        output_schema=PaymentVerdict,
        telemetry=False,
    )


async def verify_payment_claim(transcript: str) -> PaymentVerdict | None:
    """None = verifier unreachable → caller fails open (alerts anyway)."""
    try:
        result = await guarded(lambda: _verifier().arun(input=transcript), "payment-verifier")
        verdict = result.content
        if isinstance(verdict, PaymentVerdict):
            verdict.reason = short_note(verdict.reason, 160)
            return verdict
    except Exception as err:
        log.warning("payment-claim verification failed, letting the report through: %s", err)
    return None


# --- Attachment (vision) classifier ------------------------------------------
class AttachmentVerdict(BaseModel):
    is_payment_proof: bool = Field(
        description=(
            "TRUE only if this image is evidence of a money transfer the customer made — a bKash/Nagad/Rocket "
            "transaction receipt or SMS, a bank transfer slip or deposit slip, a card payment confirmation, or a "
            "screenshot of a transaction history entry. Such an image shows an amount of money together with a "
            "transaction/reference id, a recipient number, or a 'successful/সফল' confirmation. "
            "FALSE for everything else, including our own course advertisement or poster, a class routine, an exam "
            "notice or result, a book or notes page, a screenshot of a conversation, an ID card, a selfie or any "
            "other photo of a person, and a price list or fee chart the customer is merely asking about. An image "
            "that only MENTIONS a fee, price or amount is NOT proof that anything was paid. If unsure, answer FALSE."
        )
    )
    description: str = Field(
        description=(
            "One short Bengali sentence describing what the image shows, for the team's alert and for replying "
            'to the customer. E.g. "২০শ বিজেএস আলফা ব্যাচের কোর্স বিজ্ঞাপনের ছবি".'
        )
    )


@lru_cache
def _vision() -> Agent:
    return Agent(
        name="VisionClassifier",
        model=build_model(),
        instructions=(
            "You inspect images customers send to a Bangladeshi law-coaching academy's Messenger page and report "
            "what they show. You classify only — you never talk to the customer. Be strict: most images customers "
            "send are not payment receipts."
        ),
        output_schema=AttachmentVerdict,
        telemetry=False,
    )


async def classify_image(mime_type: str, data: bytes) -> AttachmentVerdict:
    """Classify an already-downloaded image. Raises when the model can't be reached —
    the caller treats that as "don't know" and fails open."""
    result = await guarded(
        lambda: _vision().arun(
            input="The customer just sent this image with no message text. Describe what it shows.",
            images=[Image(content=data, mime_type=mime_type)],
        ),
        "vision-classifier",
    )
    verdict = result.content
    if not isinstance(verdict, AttachmentVerdict):
        raise RuntimeError("model returned no attachment verdict")
    verdict.description = short_note(verdict.description, 200)
    return verdict


# --- Profile-name gender classifier -------------------------------------------
class GenderVerdict(BaseModel):
    gender: str = Field(
        description=(
            "male or female ONLY when the name makes it certain (Md./Mohammad/Abdul/Hossain → male; "
            "Mst./Mosammat/Khatun/Akter/Begum/Sultana/Jannatun → female; common unambiguous given names count "
            "too). unknown for initials, nicknames, shop/page-like names, English words, or any name a careful "
            "reader would hesitate on. When in doubt: unknown."
        )
    )


@lru_cache
def _gender() -> Agent:
    return Agent(
        name="GenderClassifier",
        model=build_model(),
        instructions=(
            "You classify Bangladeshi Facebook profile names by the gender a local reader would infer. "
            "Be conservative: answer unknown unless the name leaves no real doubt."
        ),
        output_schema=GenderVerdict,
        telemetry=False,
    )


async def classify_name_gender(name: str) -> str:
    """Returns "male" | "female" | "unknown". Raises on model failure — the caller leaves
    the guess unset so the next message retries, and this turn goes neutral."""
    result = await guarded(lambda: _gender().arun(input=f'Profile name: "{name}".'), "gender-classifier")
    verdict = result.content
    g = (verdict.gender if isinstance(verdict, GenderVerdict) else "").lower()
    return g if g in ("male", "female") else "unknown"


def reset_for_tests() -> None:
    _verifier.cache_clear()
    _vision.cache_clear()
    _gender.cache_clear()
