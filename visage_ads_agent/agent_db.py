"""Agent's own Postgres tables (separate schema from the backend).

We own this. The backend never reads from here.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import date
from typing import Iterable, Iterator

import psycopg2
import psycopg2.extras

from .config import Config, get_config

log = logging.getLogger(__name__)


@contextmanager
def _conn(cfg: Config) -> Iterator[psycopg2.extensions.connection]:
    if not cfg.agent_database_url:
        raise RuntimeError("AGENT_DATABASE_URL is not set")
    c = psycopg2.connect(cfg.agent_database_url, connect_timeout=15)
    try:
        c.autocommit = False
        yield c
    finally:
        c.close()


def ensure_schema(cfg: Config | None = None) -> None:
    """Create the agent schema + tables if absent. Idempotent."""
    cfg = cfg or get_config()
    schema = cfg.agent_db_schema
    ddl = f"""
        CREATE SCHEMA IF NOT EXISTS {schema};

        CREATE TABLE IF NOT EXISTS {schema}.ads_snapshots (
            id              BIGSERIAL PRIMARY KEY,
            captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            window_start    DATE NOT NULL,
            window_end      DATE NOT NULL,
            campaign_id     BIGINT,
            ad_group_id     BIGINT,
            keyword_id      BIGINT,
            keyword_text    TEXT,
            match_type      TEXT,
            impressions     INTEGER NOT NULL DEFAULT 0,
            taps            INTEGER NOT NULL DEFAULT 0,
            installs        INTEGER NOT NULL DEFAULT 0,
            spend_usd       NUMERIC(12,4) NOT NULL DEFAULT 0,
            avg_cpt_usd     NUMERIC(10,4),
            avg_cpi_usd     NUMERIC(10,4),
            signups         INTEGER NOT NULL DEFAULT 0,
            trials          INTEGER NOT NULL DEFAULT 0,
            paid            INTEGER NOT NULL DEFAULT 0,
            cpa_signup_usd  NUMERIC(10,4),
            cpa_trial_usd   NUMERIC(10,4),
            cpa_paid_usd    NUMERIC(10,4),
            status          TEXT NOT NULL DEFAULT 'green',
            raw             JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_ads_snapshots_window
            ON {schema}.ads_snapshots (window_start, window_end);
        CREATE INDEX IF NOT EXISTS idx_ads_snapshots_keyword
            ON {schema}.ads_snapshots (keyword_id, window_start);

        CREATE TABLE IF NOT EXISTS {schema}.recommendations (
            id              BIGSERIAL PRIMARY KEY,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            kind            TEXT NOT NULL,           -- pause | scale | bid | budget
            target_kind     TEXT NOT NULL,           -- keyword | adgroup | campaign
            target_id       BIGINT NOT NULL,
            payload         JSONB NOT NULL,
            rationale       TEXT,
            slack_ts        TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired | executed
            decided_at      TIMESTAMPTZ,
            executed_at     TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_recos_status
            ON {schema}.recommendations (status, created_at DESC);

        CREATE TABLE IF NOT EXISTS {schema}.execution_log (
            id              BIGSERIAL PRIMARY KEY,
            recommendation_id BIGINT REFERENCES {schema}.recommendations(id),
            executed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            request         JSONB,
            response        JSONB,
            ok              BOOLEAN NOT NULL
        );
    """
    with _conn(cfg) as c, c.cursor() as cur:
        cur.execute(ddl)
        c.commit()
    log.info("agent schema ensured: %s", schema)


def insert_snapshots(
    rows: Iterable[dict],
    *,
    window_start: date,
    window_end: date,
    cfg: Config | None = None,
) -> int:
    cfg = cfg or get_config()
    schema = cfg.agent_db_schema
    sql = f"""
        INSERT INTO {schema}.ads_snapshots (
            window_start, window_end,
            campaign_id, ad_group_id, keyword_id, keyword_text, match_type,
            impressions, taps, installs, spend_usd,
            avg_cpt_usd, avg_cpi_usd,
            signups, trials, paid,
            cpa_signup_usd, cpa_trial_usd, cpa_paid_usd,
            status, raw
        ) VALUES %s
    """
    values = [
        (
            window_start, window_end,
            r.get("campaign_id"), r.get("ad_group_id"), r.get("keyword_id"),
            r.get("keyword_text"), r.get("match_type"),
            r.get("impressions", 0), r.get("taps", 0), r.get("installs", 0),
            r.get("spend_usd", 0),
            r.get("avg_cpt_usd"), r.get("avg_cpi_usd"),
            r.get("signups", 0), r.get("trials", 0), r.get("paid", 0),
            r.get("cpa_signup_usd"), r.get("cpa_trial_usd"), r.get("cpa_paid_usd"),
            r.get("status", "green"),
            json.dumps(r.get("raw") or {}),
        )
        for r in rows
    ]
    if not values:
        return 0
    with _conn(cfg) as c, c.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=200)
        c.commit()
    return len(values)
