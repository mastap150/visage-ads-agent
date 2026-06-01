"""Bootstrap the 3 ASA campaigns defined in ~/Desktop/visage/APPLE_SEARCH_ADS.md.

Creates everything in PAUSED state — nothing spends until you flip it to
ENABLED in the dashboard. Saves the resulting campaign/ad-group/keyword IDs
to `scripts/campaigns.json` so Phase 2 (recommend.py) knows which entities
to monitor.

USAGE
    # Preview the API calls without making them:
    python -m scripts.bootstrap_campaigns --dry-run

    # Actually create:
    python -m scripts.bootstrap_campaigns

    # Re-run safely after a partial failure:
    python -m scripts.bootstrap_campaigns --resume

IDEMPOTENCY
    Without --resume, the script aborts if ANY of the 3 campaigns already
    exist (name match), to avoid creating duplicates. With --resume, it
    skips campaigns that already exist and tries to fill in missing pieces
    (ad groups, keywords, negatives) on the existing ones.

NOTE on app eligibility
    Apple gates campaign creation on the app being in "Ready for Sale".
    Until then this script will likely fail with an `INVALID_ADAM_ID` or
    similar error. Re-run after Apple flips the app's state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow `python -m scripts.bootstrap_campaigns` to find the agent package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from visage_ads_agent.asa_client import ASAClient  # noqa: E402
from visage_ads_agent.config import get_config     # noqa: E402

log = logging.getLogger("bootstrap_campaigns")

# -----------------------------------------------------------------------------
# Plan — exact structure from APPLE_SEARCH_ADS.md
# -----------------------------------------------------------------------------

ADAM_ID = 6775249776   # Visage iOS App Store ID
COUNTRY = "US"          # week 1-2 per the launch doc

NEGATIVE_KEYWORDS_CAMPAIGN_LEVEL = [
    # tire-kickers / wrong intent
    ("free", "BROAD"),
    ("dating", "BROAD"),
    # piracy / never-search-on-App-Store-anyway but defensive
    ("crack", "BROAD"),
    ("mod", "BROAD"),
    ("hack", "BROAD"),
    ("apk", "BROAD"),
    # disambiguation — Visage medical-imaging product
    ("visage medical", "BROAD"),
    ("vsr", "EXACT"),
    # wrong category
    ("face filter", "BROAD"),
    ("face swap", "BROAD"),
    ("selfie filter", "BROAD"),
]


@dataclass
class KeywordSpec:
    text: str
    match_type: str          # "EXACT" or "BROAD"
    bid_usd: str             # decimal string, ASA wants string amounts


@dataclass
class AdGroupSpec:
    name: str
    default_bid_usd: str
    keywords: list[KeywordSpec]


@dataclass
class CampaignSpec:
    name: str
    daily_budget_usd: str
    ad_groups: list[AdGroupSpec]


def k(text: str, mt: str, bid: str) -> KeywordSpec:
    return KeywordSpec(text=text, match_type=mt, bid_usd=bid)


CAMPAIGNS: list[CampaignSpec] = [
    CampaignSpec(
        name="Visage — Brand Defense",
        daily_budget_usd="8",
        ad_groups=[
            AdGroupSpec(
                name="Brand exact",
                default_bid_usd="0.50",
                keywords=[k("visage", "EXACT", "0.50")],
            ),
            AdGroupSpec(
                name="Brand variants",
                default_bid_usd="0.40",
                keywords=[
                    k("visage app", "BROAD", "0.40"),
                    k("hellovisage", "BROAD", "0.40"),
                    k("visage ai", "BROAD", "0.40"),
                ],
            ),
        ],
    ),
    CampaignSpec(
        name="Category — Discovery",
        daily_budget_usd="25",
        ad_groups=[
            AdGroupSpec(
                name="Celeb ID",
                default_bid_usd="1.80",
                keywords=[
                    k("celebrity identifier", "BROAD", "1.80"),
                    k("identify celebrity", "BROAD", "1.80"),
                    k("who is that", "BROAD", "1.80"),
                    k("who is this person", "BROAD", "1.80"),
                    k("face recognition celebrity", "BROAD", "1.80"),
                ],
            ),
            AdGroupSpec(
                name="Photo lookup",
                default_bid_usd="1.60",
                keywords=[
                    k("photo lookup", "BROAD", "1.60"),
                    k("reverse image search", "BROAD", "1.60"),
                    k("picture identifier", "BROAD", "1.60"),
                    k("image to name", "BROAD", "1.60"),
                    k("face match", "BROAD", "1.60"),
                ],
            ),
            AdGroupSpec(
                name="Famous people",
                default_bid_usd="1.50",
                keywords=[
                    k("famous people quiz", "BROAD", "1.50"),
                    k("famous people identifier", "BROAD", "1.50"),
                    k("celebrity quiz", "BROAD", "1.50"),
                    k("who is this celebrity", "BROAD", "1.50"),
                    k("famous face guess", "BROAD", "1.50"),
                ],
            ),
        ],
    ),
    CampaignSpec(
        name="Competitors",
        daily_budget_usd="5",
        ad_groups=[
            AdGroupSpec(
                name="Competitive",
                default_bid_usd="1.20",
                keywords=[
                    k("picthis", "BROAD", "1.20"),
                    k("shazam", "BROAD", "1.20"),
                    k("google lens", "BROAD", "1.20"),
                ],
            ),
        ],
    ),
]

# -----------------------------------------------------------------------------
# ASA wire format helpers
# -----------------------------------------------------------------------------

def usd(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "USD"}


def iso_now() -> str:
    # ASA wants ISO 8601 in UTC.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# -----------------------------------------------------------------------------
# State that we save out
# -----------------------------------------------------------------------------

@dataclass
class CreatedRefs:
    org_id: int
    campaigns: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "org_id": self.org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "campaigns": self.campaigns,
        }, indent=2)


# -----------------------------------------------------------------------------
# API calls
# -----------------------------------------------------------------------------

async def list_existing_campaigns(client: ASAClient) -> dict[str, dict[str, Any]]:
    resp = await client.request_json("GET", "/campaigns", params={"limit": 1000})
    by_name = {c["name"]: c for c in (resp.get("data") or [])}
    return by_name


async def create_campaign(client: ASAClient, spec: CampaignSpec, org_id: int) -> dict[str, Any]:
    body = {
        "orgId": org_id,
        "name": spec.name,
        "adamId": ADAM_ID,
        "dailyBudgetAmount": usd(spec.daily_budget_usd),
        "supplySources": ["APPSTORE_SEARCH_RESULTS"],
        "adChannelType": "SEARCH",
        "billingEvent": "TAPS",
        "paymentModel": "PAYG",
        "countriesOrRegions": [COUNTRY],
        "status": "PAUSED",
    }
    resp = await client.request_json("POST", "/campaigns", body=body)
    return resp["data"]


async def list_existing_adgroups(client: ASAClient, campaign_id: int) -> dict[str, dict[str, Any]]:
    resp = await client.request_json(
        "GET", f"/campaigns/{campaign_id}/adgroups", params={"limit": 1000},
    )
    return {a["name"]: a for a in (resp.get("data") or [])}


async def create_adgroup(client: ASAClient, campaign_id: int, spec: AdGroupSpec) -> dict[str, Any]:
    body = {
        "name": spec.name,
        "defaultBidAmount": usd(spec.default_bid_usd),
        "startTime": iso_now(),
        "endTime": None,
        "automatedKeywordsOptIn": False,
        "status": "PAUSED",
        "pricingModel": "CPC",
        "cpaGoal": None,
        "targetingDimensions": {
            "deviceClass": {"included": ["IPHONE", "IPAD"]},
            "customerType": {"included": ["NEW_USERS"]},
        },
    }
    resp = await client.request_json(
        "POST", f"/campaigns/{campaign_id}/adgroups", body=body,
    )
    return resp["data"]


async def bulk_add_keywords(
    client: ASAClient, campaign_id: int, adgroup_id: int, kws: list[KeywordSpec],
) -> list[dict[str, Any]]:
    body = [
        {"text": kw.text, "matchType": kw.match_type, "bidAmount": usd(kw.bid_usd), "status": "ACTIVE"}
        for kw in kws
    ]
    resp = await client.request_json(
        "POST",
        f"/campaigns/{campaign_id}/adgroups/{adgroup_id}/targetingkeywords/bulk",
        body=body,
    )
    return resp.get("data") or []


async def bulk_add_negative_keywords(
    client: ASAClient, campaign_id: int, kws: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    body = [{"text": t, "matchType": mt} for t, mt in kws]
    resp = await client.request_json(
        "POST", f"/campaigns/{campaign_id}/negativekeywords/bulk", body=body,
    )
    return resp.get("data") or []


# -----------------------------------------------------------------------------
# Main flow
# -----------------------------------------------------------------------------

async def run(dry_run: bool, resume: bool) -> int:
    cfg = get_config()
    if not cfg.asa_org_id:
        log.error("ASA_ORG_ID is not set in env")
        return 2
    org_id = int(cfg.asa_org_id)

    log.info("orgId=%s  adamId=%s  country=%s  paused=%s",
             org_id, ADAM_ID, COUNTRY, True)

    # Preview.
    total_daily = sum(int(c.daily_budget_usd) for c in CAMPAIGNS)
    total_keywords = sum(len(ag.keywords) for c in CAMPAIGNS for ag in c.ad_groups)
    log.info("Plan: %d campaigns, %d ad groups, %d keywords, %d negatives,"
             " total $%s/day",
             len(CAMPAIGNS),
             sum(len(c.ad_groups) for c in CAMPAIGNS),
             total_keywords,
             len(NEGATIVE_KEYWORDS_CAMPAIGN_LEVEL),
             total_daily)
    for c in CAMPAIGNS:
        log.info("  %s — $%s/day", c.name, c.daily_budget_usd)
        for ag in c.ad_groups:
            log.info("    %s — bid $%s — %d kw", ag.name, ag.default_bid_usd, len(ag.keywords))

    if dry_run:
        log.info("DRY RUN — no API calls.")
        return 0

    refs = CreatedRefs(org_id=org_id)
    async with ASAClient(cfg) as client:
        existing = await list_existing_campaigns(client)
        names_in_use = set(existing) & {c.name for c in CAMPAIGNS}
        if names_in_use and not resume:
            log.error(
                "Refusing to create — these campaigns already exist by name: %s. "
                "Pass --resume to fill in missing pieces, or delete the duplicates "
                "in the ASA dashboard first.",
                sorted(names_in_use),
            )
            return 3

        for c in CAMPAIGNS:
            if c.name in existing:
                camp = existing[c.name]
                log.info("• campaign exists: %s (id=%s) — resuming", c.name, camp["id"])
            else:
                log.info("• creating campaign: %s", c.name)
                camp = await create_campaign(client, c, org_id)
                log.info("  ✓ id=%s", camp["id"])

            campaign_id = camp["id"]
            campaign_record: dict[str, Any] = {
                "id": campaign_id,
                "name": c.name,
                "daily_budget_usd": c.daily_budget_usd,
                "ad_groups": [],
                "negative_keywords": [],
            }

            ag_existing = await list_existing_adgroups(client, campaign_id) if resume else {}

            for ag in c.ad_groups:
                if ag.name in ag_existing:
                    ag_data = ag_existing[ag.name]
                    log.info("    • adgroup exists: %s (id=%s) — skipping keyword bulk",
                             ag.name, ag_data["id"])
                    kw_results: list[dict[str, Any]] = []
                else:
                    log.info("    • creating adgroup: %s", ag.name)
                    ag_data = await create_adgroup(client, campaign_id, ag)
                    log.info("      ✓ id=%s", ag_data["id"])
                    kw_results = await bulk_add_keywords(
                        client, campaign_id, ag_data["id"], ag.keywords,
                    )
                    log.info("      ✓ added %d keywords", len(kw_results))

                campaign_record["ad_groups"].append({
                    "id": ag_data["id"],
                    "name": ag.name,
                    "default_bid_usd": ag.default_bid_usd,
                    "keywords": [
                        {"id": r.get("id"), "text": r.get("text"), "matchType": r.get("matchType")}
                        for r in kw_results
                    ],
                })

            log.info("    • adding %d negative keywords to campaign",
                     len(NEGATIVE_KEYWORDS_CAMPAIGN_LEVEL))
            neg_results = await bulk_add_negative_keywords(
                client, campaign_id, NEGATIVE_KEYWORDS_CAMPAIGN_LEVEL,
            )
            campaign_record["negative_keywords"] = [
                {"id": r.get("id"), "text": r.get("text"), "matchType": r.get("matchType")}
                for r in neg_results
            ]
            refs.campaigns.append(campaign_record)

    out_path = Path(__file__).resolve().parent / "campaigns.json"
    out_path.write_text(refs.to_json())
    log.info("\nWrote %s", out_path)
    log.info("All campaigns created in PAUSED state. Review in the ASA dashboard, "
             "then flip to ENABLED when ready to spend.")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Print the plan, make no API calls")
    p.add_argument("--resume", action="store_true",
                   help="Tolerate existing campaigns/ad-groups by name; only create what's missing")
    args = p.parse_args()
    sys.exit(asyncio.run(run(dry_run=args.dry_run, resume=args.resume)))


if __name__ == "__main__":
    main()
