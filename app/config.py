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
    llm_timeout_seconds: int = 30
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

    # --- Admin panel (optional restricted editor account) ---
    editor_username: str = ""
    editor_password: str = ""

    # --- Infra ---
    database_url: str = "postgresql+asyncpg://dejure:dejure@localhost:5433/dejure"
    port: int = 3000

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
