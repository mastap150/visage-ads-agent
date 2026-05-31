"""Environment-driven configuration. Loaded once at import."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _opt(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    raw = _opt(name)
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = _opt(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    # ASA auth (modern self-managed-cert OAuth flow)
    #   client_id: 'SEARCHADS.<uuid>' — JWT `sub` AND form `client_id`
    #   team_id:   'SEARCHADS.<uuid>' — JWT `iss` (often == client_id for self-managed)
    #   key_id:    plain uuid — JWT header `kid`
    asa_client_id: str
    asa_team_id: str
    asa_key_id: str
    asa_private_key_pem: str
    asa_org_id: str
    asa_time_zone: str

    # Databases
    backend_database_url: str
    agent_database_url: str
    agent_db_schema: str

    # Slack
    slack_webhook_url: str

    # Anthropic (Phase 3)
    anthropic_api_key: str
    anthropic_model: str

    # Behavior
    report_lookback_hours: int
    min_data_age_hours: int
    currency: str

    # Guardrails
    target_cpa_paid_usd: float
    hard_cap_cpt_usd: float
    max_daily_budget_increase_pct: float

    @classmethod
    def load(cls) -> "Config":
        pem = _opt("ASA_PRIVATE_KEY_PEM")
        if not pem:
            path = _opt("ASA_PRIVATE_KEY_PATH")
            if path:
                pem = Path(os.path.expanduser(path)).read_text()
        # Render-style escaped newlines.
        pem = pem.replace("\\n", "\n")

        return cls(
            asa_client_id=_req("ASA_CLIENT_ID"),
            asa_team_id=_opt("ASA_TEAM_ID") or _req("ASA_CLIENT_ID"),
            asa_key_id=_req("ASA_KEY_ID"),
            asa_private_key_pem=pem,
            asa_org_id=_opt("ASA_ORG_ID"),
            asa_time_zone=_opt("ASA_TIME_ZONE", "UTC"),
            backend_database_url=_opt("BACKEND_DATABASE_URL"),
            agent_database_url=_opt("AGENT_DATABASE_URL"),
            agent_db_schema=_opt("AGENT_DB_SCHEMA", "ads_agent"),
            slack_webhook_url=_opt("SLACK_WEBHOOK_URL"),
            anthropic_api_key=_opt("ANTHROPIC_API_KEY"),
            anthropic_model=_opt("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            report_lookback_hours=_int("REPORT_LOOKBACK_HOURS", 24),
            min_data_age_hours=_int("MIN_DATA_AGE_HOURS", 48),
            currency=_opt("CURRENCY", "USD"),
            target_cpa_paid_usd=_float("TARGET_CPA_PAID_USD", 15.0),
            hard_cap_cpt_usd=_float("HARD_CAP_CPT_USD", 5.0),
            max_daily_budget_increase_pct=_float("MAX_DAILY_BUDGET_INCREASE_PCT", 25.0),
        )


def get_config() -> Config:
    global _CACHED
    try:
        return _CACHED  # type: ignore[name-defined]
    except NameError:
        _CACHED = Config.load()  # noqa: F841
        return _CACHED
