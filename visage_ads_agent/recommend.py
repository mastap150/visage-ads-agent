"""Phase 2 — Recommender (review mode). STUB.

Reads the last N daily snapshots from `ads_agent.ads_snapshots` and proposes
changes. Posts each as a Slack message with thumbs-up / thumbs-down reaction
prompts. Persists each proposal to `ads_agent.recommendations`.

NOTHING IN THIS MODULE WRITES TO ASA. Only human-approved recommendations
ever reach `agent.py` for execution.

Decision rules (initial):
  - pause_candidate:  CPA-to-paid > 1.5× target for 7+ consecutive days
                      AND spend_7d >= $10 (avoid acting on tiny samples)
  - scale_candidate:  CPA-to-paid < 0.7× target for 7+ consecutive days
                      AND impressions_7d >= 500
  - bid_down:         CTR < 1% AND CPT > 0.8 × HARD_CAP_CPT for 3+ days
  - bid_up:           top-impression-share keyword with CPA < target and
                      taps/impressions ratio dropping (signal: under-bid)
  - budget_shift:     a campaign with green ad groups under-spending its cap
                      while a red campaign overspends → propose -15% / +15%

Each rule emits a `Recommendation` dataclass — never a direct ASA call.

To wire up Slack-reaction approval:
  1. Cron job posts each recommendation message (we record `slack_ts`).
  2. A separate small worker polls `conversations.reactions.get` every N min.
  3. 👍 → status='approved'; 👎 → status='rejected'; 24h no reaction → 'expired'.
  4. Phase 3's `agent.py` reads `status='approved' AND executed_at IS NULL`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Recommendation:
    kind: str            # "pause" | "scale" | "bid" | "budget"
    target_kind: str     # "keyword" | "adgroup" | "campaign"
    target_id: int
    payload: dict[str, Any]   # e.g. {"new_bid_usd": 1.20} or {"delta_pct": -15}
    rationale: str


# ---------------------------------------------------------------------------
# Public surface — to be implemented next session.
# ---------------------------------------------------------------------------

def load_recent_snapshots(days: int = 7) -> list[dict[str, Any]]:
    """TODO: SELECT * FROM ads_agent.ads_snapshots
    WHERE window_end >= current_date - <days> AND window_end < current_date
    ORDER BY window_end, campaign_id, ad_group_id, keyword_id;
    Aggregate per keyword.
    """
    raise NotImplementedError


def detect_pause_candidates(snapshots: list[dict[str, Any]]) -> list[Recommendation]:
    """TODO: keywords with sustained CPA-to-paid > 1.5× target."""
    raise NotImplementedError


def detect_scale_candidates(snapshots: list[dict[str, Any]]) -> list[Recommendation]:
    """TODO: keywords with sustained CPA-to-paid < 0.7× target AND impressions_7d >= 500."""
    raise NotImplementedError


def detect_bid_adjustments(snapshots: list[dict[str, Any]]) -> list[Recommendation]:
    """TODO: ±15% bid moves based on CTR / CPT signals."""
    raise NotImplementedError


def detect_budget_shifts(snapshots: list[dict[str, Any]]) -> list[Recommendation]:
    """TODO: redistribute daily budget across campaigns."""
    raise NotImplementedError


def persist_recommendations(recs: list[Recommendation]) -> list[int]:
    """TODO: INSERT into ads_agent.recommendations and return new IDs."""
    raise NotImplementedError


def post_to_slack(recs: list[Recommendation]) -> None:
    """TODO: one Slack message per recommendation, capture `ts`, store in row."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Reaction polling — separate Render cron (every 15 min)
# ---------------------------------------------------------------------------

def poll_slack_reactions() -> None:
    """TODO: for each pending recommendation, call Slack's reactions.get,
    update status to approved/rejected. Expire after 24h.

    Requires SLACK_BOT_TOKEN (not the webhook URL) — note in env.example.
    """
    raise NotImplementedError


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("Phase 2 not implemented yet. Coming after 7 days of Phase 1 data.")


if __name__ == "__main__":
    main()
