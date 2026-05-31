"""Phase 1 — daily reporter.

Pulls last-N-hours of ASA performance, joins backend conversions, computes
funnel CPAs per keyword, posts a Slack digest, persists a snapshot.

Read-only with respect to ASA. Read-only with respect to the backend.
Only writes to the agent's own Postgres schema (`ads_agent.ads_snapshots`).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, time, timezone
from typing import Any

from .agent_db import ensure_schema, insert_snapshots
from .asa_client import (
    ASAClient,
    flatten_row,
    pull_all_keyword_rows,
    report_window,
)
from .backend_db import fetch_conversions, window_for_lookback
from .config import get_config
from .slack import build_digest_blocks, post_digest

log = logging.getLogger(__name__)


def _classify(cpa_paid: float | None, target: float) -> str:
    if cpa_paid is None:
        return "n/a"
    if cpa_paid <= target * 0.7:
        return "🟢"
    if cpa_paid <= target * 1.5:
        return "🟡"
    return "🔴"


async def _amain() -> int:
    cfg = get_config()

    # 1. ASA — pull keyword rows for every campaign.
    start_d, end_d = report_window(cfg.report_lookback_hours)
    log.info("ASA window: %s .. %s", start_d, end_d)

    async with ASAClient(cfg) as client:
        # Fail fast on missing org.
        if not cfg.asa_org_id:
            orgs = await client.list_orgs()
            log.error(
                "ASA_ORG_ID not set. /acls returned: %s",
                [{"orgId": o.get("orgId"), "orgName": o.get("orgName")} for o in orgs],
            )
            return 2
        raw_rows = await pull_all_keyword_rows(client, start_d, end_d)

    asa_rows = [flatten_row(r) for r in raw_rows]
    log.info("ASA rows: %d", len(asa_rows))

    # 2. Backend conversions for the same window (UTC datetimes).
    since = datetime.combine(start_d, time(0, 0, tzinfo=timezone.utc))
    until = datetime.combine(end_d, time(23, 59, 59, tzinfo=timezone.utc))
    conv_rows = fetch_conversions(since=since, until=until, cfg=cfg)
    conv_index: dict[tuple[Any, Any, Any], dict[str, int]] = {}
    for c in conv_rows:
        conv_index[(c.campaign_id, c.ad_group_id, c.keyword_id)] = {
            "signups": c.signups, "trials": c.trials, "paid": c.paid,
        }

    # 3. Join + compute funnel CPAs per keyword.
    joined: list[dict[str, Any]] = []
    for r in asa_rows:
        key = (r["campaign_id"], r["ad_group_id"], r["keyword_id"])
        conv = conv_index.get(key, {"signups": 0, "trials": 0, "paid": 0})
        spend = r["local_spend_usd"]
        installs = r["installs"]
        cpa_signup = spend / conv["signups"] if conv["signups"] else None
        cpa_trial = spend / conv["trials"] if conv["trials"] else None
        cpa_paid = spend / conv["paid"] if conv["paid"] else None
        cpi = spend / installs if installs else None
        joined.append({
            **r,
            "spend_usd": spend,
            "avg_cpi_usd": cpi,
            "signups": conv["signups"],
            "trials": conv["trials"],
            "paid": conv["paid"],
            "cpa_signup_usd": cpa_signup,
            "cpa_trial_usd": cpa_trial,
            "cpa_paid_usd": cpa_paid,
            "status": _classify(cpa_paid, cfg.target_cpa_paid_usd),
            "raw": r,
        })

    joined.sort(key=lambda x: x["spend_usd"], reverse=True)

    totals = {
        "spend_usd": sum(r["spend_usd"] for r in joined),
        "installs": sum(r["installs"] for r in joined),
        "signups": sum(r["signups"] for r in joined),
        "trials": sum(r["trials"] for r in joined),
        "paid": sum(r["paid"] for r in joined),
    }

    # 4. Persist (own schema).
    ensure_schema(cfg)
    n = insert_snapshots(joined, window_start=start_d, window_end=end_d, cfg=cfg)
    log.info("persisted %d snapshot rows", n)

    # 5. Slack digest.
    notes: list[str] = []
    if not conv_rows:
        notes.append(
            "_No conversion rows joined — backend `users.acquisition_source` may be unset; "
            "CPI shown, CPA-to-paid unavailable. See README §Backend integration._"
        )
    notes.append(f"Target CPA-to-paid: ${cfg.target_cpa_paid_usd:.2f}. 🟢 ≤ 0.7×, 🟡 ≤ 1.5×, 🔴 above.")
    notes.append(f"Window: {start_d} → {end_d} ({cfg.report_lookback_hours}h, tz {cfg.asa_time_zone})")

    blocks, fallback = build_digest_blocks(
        window_label=f"{start_d} → {end_d}",
        totals=totals,
        rows=joined,
        notes=notes,
    )
    post_digest(cfg.slack_webhook_url, blocks, fallback_text=fallback)
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
