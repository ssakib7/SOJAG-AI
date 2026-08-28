"""Application settings, loaded from environment / .env.

Single-tenant today, but everything tenant-specific (page tokens, sinks, KB,
behaviour knobs) lives on this one object so the bot can later become tenant #1
of the multi-tenant platform with minimal surgery.
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

FALSEY = re.compile(r"^(false|0|off|no)$", re.IGNORECASE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Required secrets (validated at boot in validate()) ---
    page_access_token: str = ""      # Send API token
    app_secret: str = ""             # webhook HMAC + signed_request verification
    verify_token: str = ""           # webhook subscription handshake
    admin_username: str = ""
    admin_password: str = ""
    session_secret: str = ""

    # --- Meta ---
    fb_page_id: str = ""             # page asset id: Send target + Conversations API + inbox links
    # Page-type token for the Conversations/Personas APIs (a system-user token is refused
    # with #190 there). Falls back to page_access_token when unset.
    messenger_page_token: str = ""
    persona_id: str = ""             # optional Messenger persona ("SOJAG AI")
    graph_api_base: str = "https://graph.facebook.com/v21.0"  # test stub override
    public_url: str = "https://bot.dejureacademy.net"

    # --- LLM ---
    llm_provider: str = "gemini"     # gemini | openrouter
    llm_model: str = "gemini-3.1-flash-lite"
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_api_base: str = ""    # test stub override; production leaves it unset
    # Measured 12-28.5s per turn on gemini-3.x through the team (leader hop + member +
    # sometimes the payment verifier). 30s was cutting off legitimate generations, which
    # then retried, paid twice, and still fell back to the error line.
    llm_timeout_seconds: int = 60
    llm_max_concurrent: int = 25
    # Bengali + Gemini 3.x thinking tokens share this budget; 1024 truncated replies.
    llm_max_tokens: int = 4096

    # --- Sinks ---
    google_sheet_webapp_url: str = ""
    sheet_shared_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_base: str = "https://api.telegram.org"  # test stub override
    payment_alerts: str = ""         # "false" disables payment claim alerts
    lead_digest_time: str = "21:00"  # Asia/Dhaka; "off" disables

    # --- Behaviour ---
    lead_msg_threshold: int = 3      # proactive contact-ask after N turns
    followup_delay_minutes: int = 120  # 0 disables; keep under 1440 (24h window)
    reply_min_seconds: int = 5
    reply_max_seconds: int = 10
    memory_enabled: str = ""         # "false" pauses persistence + customer recall

    # --- Knowledge RAG (optional growth path; catalog stays in the prompt) ---
    knowledge_rag_enabled: bool = False  # needs Postgres + the active provider's key (embeddings)

    # --- AgentOS control plane (optional, post-cutover observability) ---
    # Mounts Agno's AgentOS API on top of our FastAPI app (traces, sessions, runs).
    # Refused unless OS_SECURITY_KEY is set: its routes would otherwise be public.
    agentos_enabled: bool = False
    os_security_key: str = ""

    # --- Admin panel (optional restricted editor account) ---
    editor_username: str = ""
    editor_password: str = ""

    # --- Infra ---
    database_url: str = "postgresql+asyncpg://dejure:dejure@localhost:5433/dejure"
    port: int = 3000
    # Shadow mode (pre-cutover quality gate): receive mirrored webhook traffic, run the
    # full pipeline, but suppress every outward effect — no Graph sends, no sender
    # actions, and outbox sinks resolve as skipped. Drafts are recorded in
    # shadow_drafts for scripts/shadow_compare.py to diff against the live bot.
    shadow_mode: bool = False
    # Skips the boot-time Telegram ping and Graph token probe. Set in tests; in production
    # the self-check is the thing that catches a silent misconfiguration on day one.
    selfcheck_disabled: bool = False

    # ------------------------------------------------------------------
    @property
    def public_origin(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def send_target(self) -> str:
        # A Page token resolves "me" to the Page, but a System User token does not
        # (code 100 / subcode 33) — so prefer the explicit page id.
        return self.fb_page_id or "me"

    @property
    def lookup_token(self) -> str:
        return self.messenger_page_token.strip() or self.page_access_token

    @property
    def leads_on(self) -> bool:
        return bool(self.google_sheet_webapp_url or (self.telegram_bot_token and self.telegram_chat_id))

    @property
    def payments_on(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id) and not FALSEY.match(
            self.payment_alerts.strip() or "on"
        )

    @property
    def memory_on(self) -> bool:
        return not FALSEY.match(self.memory_enabled.strip() or "on")

    @property
    def followup_delay_ms(self) -> int:
        return max(self.followup_delay_minutes, 0) * 60 * 1000

    def validate_required(self) -> None:
        """Fail fast when a required secret is missing — clearer than a runtime error later."""
        required = {
            "PAGE_ACCESS_TOKEN": self.page_access_token,
            "APP_SECRET": self.app_secret,
            "VERIFY_TOKEN": self.verify_token,
            "ADMIN_USERNAME": self.admin_username,
            "ADMIN_PASSWORD": self.admin_password,
            "SESSION_SECRET": self.session_secret,
        }
        for name, value in required.items():
            if not value:
                print(f"Missing required env var: {name}. Copy .env.example to .env and fill it in.")
                sys.exit(1)
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            print("LLM_PROVIDER=gemini requires GEMINI_API_KEY.")
            sys.exit(1)
        if self.llm_provider == "openrouter" and not self.openrouter_api_key:
            print("LLM_PROVIDER=openrouter requires OPENROUTER_API_KEY.")
            sys.exit(1)

    def startup_warnings(self) -> list[str]:
        """Configuration that is not fatal but WILL cost leads, returned so the caller can
        log it loudly at boot.

        Every one of these used to be silent: the bot chatted away 24/7 while capturing
        nothing, and the only symptom was an empty sheet nobody checked for a week.
        """
        warnings: list[str] = []
        if not self.leads_on:
            warnings.append(
                "LEAD CAPTURE IS OFF — no sheet and no Telegram configured. The bot will "
                "answer customers but every lead will be thrown away. Set "
                "GOOGLE_SHEET_WEBAPP_URL, or both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            )
        else:
            if not self.google_sheet_webapp_url:
                warnings.append("No GOOGLE_SHEET_WEBAPP_URL — leads reach Telegram only, not the sheet.")
            if not (self.telegram_bot_token and self.telegram_chat_id):
                warnings.append(
                    "No Telegram configured — leads reach the sheet only. Payment claims and "
                    "escalations (angry customers, human handoff requests) go to Telegram ONLY, "
                    "so they will reach nobody."
                )
        if not self.payments_on:
            warnings.append("PAYMENT ALERTS ARE OFF — a customer saying they paid will not notify anyone.")
        if not self.fb_page_id:
            warnings.append(
                "FB_PAGE_ID is empty — sends fall back to /me, which a System User token cannot "
                "resolve (error 100/33). If the bot appears silent, this is why."
            )
        if not self.messenger_page_token.strip():
            warnings.append(
                "MESSENGER_PAGE_TOKEN is empty — customer-name lookup and conversation links fall "
                "back to PAGE_ACCESS_TOKEN. If that is a System User token the Conversations API "
                "refuses it (#190) and every alert will say '(নাম জানা যায়নি)' with no chat link."
            )
        if not self.memory_on:
            warnings.append("MEMORY_ENABLED=false — every chat starts fresh and no customer is recalled.")
        if self.shadow_mode:
            warnings.append("SHADOW_MODE=true — the bot will NOT reply to anyone and no lead will be delivered.")
        if self.followup_delay_minutes >= 1440:
            warnings.append(
                f"FOLLOWUP_DELAY_MINUTES={self.followup_delay_minutes} is at or past Meta's 24h "
                "messaging window — the nudge will be rejected. Keep it under 1440."
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
