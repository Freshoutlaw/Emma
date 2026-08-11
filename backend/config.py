"""Application configuration — pydantic-settings backed by .env / env vars.

Env vars use the `EMMA_` prefix (e.g. EMMA_CONSENT_MODE), with a few well-known
exceptions aliased for convenience: GROQ_API_KEY / EMMA_GROQ_API_KEY.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EMMA_", extra="ignore")

    # --- app
    app_name: str = "emma-ai"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: Optional[str] = None  # EMMA_API_KEY — when set, all /api/* need X-API-Key
    domain: str = "localhost"  # EMMA_DOMAIN — used for LLM routing

    # --- paths
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    data_dir: Path = Field(default_factory=_default_data_dir)

    # --- LLM
    groq_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "EMMA_GROQ_API_KEY"),
    )
    deepgram_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DEEPGRAM_API_KEY", "EMMA_DEEPGRAM_API_KEY"),
    )
    deepgram_stt_model: str = "nova-2"
    deepgram_tts_model: str = "aura-asteria-en"
    deepgram_tts_voice: str = "aura-asteria-en"
    ollama_url: str = "http://localhost:11434"
    local_model: str = "qwen3:5.4b"
    cloud_model: str = "llama-3.3-70b-versatile"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 384

    # --- security
    consent_mode: str = "once"      # auto | once | strict
    approval_ttl: int = 3600        # seconds an approved action stays approved
    audit_max_entries: int = 5000
    network_gate_open: bool = True
    master_key: Optional[str] = None

    # --- memory (Supabase)
    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    supabase_service_key: Optional[str] = None

    # --- read-only query DSN (asyncpg, IPv4 shared-pooler)
    supabase_query_dsn: Optional[str] = None

    # --- MQTT
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_user: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_topic_prefix: str = "emma"

    # --- voice
    tts_voice: str = "en-US-JennyNeural"

    # ------------------------------------------------------------ derived paths
    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.log"

    @property
    def kill_switch_path(self) -> Path:
        return self.data_dir / "kill_switch"

    @property
    def memory_db_path(self) -> Path:
        return self.data_dir / "memory.db"

    @property
    def usage_db_path(self) -> Path:
        return self.data_dir / "usage.db"

    @property
    def network_gate_path(self) -> Path:
        return self.data_dir / "network_gate"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def master_key_path(self) -> Path:
        return self.data_dir / "master.key"

    @property
    def hud_dir(self) -> Path:
        return self.project_root / "interfaces" / "hud"
