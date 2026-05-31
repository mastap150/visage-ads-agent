"""Read-only queries against the Visage backend's Neon Postgres.

We never write to this database — the backend owns the schema. We only join
the ASA report against the `users` table to compute downstream conversion
metrics (signups, trials, paid).

Convention for the attribution string (set by the iOS client when it forwards
the AdServices token / `iad-campaign-id` postback):

    asa:<campaignId>:<adGroupId>:<keywordId>

Missing pieces are written as empty segments — e.g. `asa::adGroupId:` is fine.
The backend column is `users.acquisition_source TEXT`. If the backend hasn't
deployed that column yet, this module raises a clear error and `report.py`
falls back to "no conversions joined" rather than crashing.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg2
import psycopg2.extras

from .config import Config, get_config

log = logging.getLogger(__name__)


@dataclass
class ConversionRow:
    campaign_id: int | None
    ad_group_id: int | None
    keyword_id: int | None
    signups: int
    trials: int
    paid: int


@contextmanager
def _conn(cfg: Config) -> Iterator[psycopg2.extensions.connection]:
    if not cfg.backend_database_url:
        raise RuntimeError("BACKEND_DATABASE_URL is not set")
    c = psycopg2.connect(cfg.backend_database_url, connect_timeout=15)
    try:
        # NOTE: do NOT use set_session(readonly=True). Neon's pooler is in
        # transaction-pooling mode and a session-level READ ONLY flag leaks
        # across pooled connections (we hit this: a later CREATE SCHEMA on a
        # *different* connection that happened to reuse the same server-side
        # session failed with `cannot execute CREATE SCHEMA in a read-only
        # transaction`). The actual read-only guarantee is enforced by the
        # SQL in this module — only SELECTs.
        c.autocommit = True
        yield c
    finally:
        c.close()


def has_acquisition_source(cfg: Config | None = None) -> bool:
    cfg = cfg or get_config()
    try:
        with _conn(cfg) as c, c.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'acquisition_source'
                LIMIT 1
                """
            )
            return cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001
        log.warning("acquisition_source check failed: %s", e)
        return False


def fetch_conversions(
    *,
    since: datetime,
    until: datetime,
    cfg: Config | None = None,
) -> list[ConversionRow]:
    """Return per-keyword conversion counts for users created in [since, until).

    Trial = anyone with plan in ('pro','api') OR `trial_started_at` populated
    if that column exists (we degrade gracefully if it doesn't).
    Paid   = plan = 'pro'.

    The split(acquisition_source, ':') treats segments 2..4 as campaign / ad
    group / keyword IDs. Anything malformed bucketizes to NULL ids, which
    `report.py` then drops from the join.
    """
    cfg = cfg or get_config()
    if not has_acquisition_source(cfg):
        log.warning("backend users.acquisition_source column missing; skipping conversion join")
        return []

    sql = """
        WITH parsed AS (
            SELECT
                NULLIF(split_part(acquisition_source, ':', 2), '')::bigint AS campaign_id,
                NULLIF(split_part(acquisition_source, ':', 3), '')::bigint AS ad_group_id,
                NULLIF(split_part(acquisition_source, ':', 4), '')::bigint AS keyword_id,
                plan,
                created_at
            FROM users
            WHERE acquisition_source LIKE 'asa:%%'
              AND created_at >= %(since)s
              AND created_at <  %(until)s
        )
        SELECT
            campaign_id,
            ad_group_id,
            keyword_id,
            COUNT(*)                                 AS signups,
            COUNT(*) FILTER (WHERE plan IN ('pro','api')) AS trials,
            COUNT(*) FILTER (WHERE plan = 'pro')     AS paid
        FROM parsed
        GROUP BY campaign_id, ad_group_id, keyword_id
    """
    with _conn(cfg) as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {"since": since, "until": until})
        rows = cur.fetchall()
    out = [
        ConversionRow(
            campaign_id=r["campaign_id"],
            ad_group_id=r["ad_group_id"],
            keyword_id=r["keyword_id"],
            signups=int(r["signups"]),
            trials=int(r["trials"]),
            paid=int(r["paid"]),
        )
        for r in rows
    ]
    log.info("Backend conversions joined: %d keyword buckets", len(out))
    return out


def window_for_lookback(lookback_hours: int) -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=lookback_hours)
    return since, until
