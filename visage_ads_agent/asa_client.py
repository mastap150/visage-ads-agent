"""Apple Search Ads API client.

Authentication flow (different from App Store Connect):
  1. Sign a client-secret JWT with the ASC-style .p8 key:
       iss = team/issuer ID
       sub = client (key) ID
       aud = https://appleid.apple.com
       exp = up to 180 days
  2. POST it to https://appleid.apple.com/auth/oauth2/token to receive a
     short-lived (~1h) access_token used as `Authorization: Bearer ...`.
  3. Every API call also requires the `X-AP-Context: orgId=<orgId>` header,
     which scopes the request to a specific Search Ads org.

Apple's docs: https://developer.apple.com/documentation/apple_search_ads
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
import jwt

from .config import Config, get_config

log = logging.getLogger(__name__)

ASA_TOKEN_URL = "https://appleid.apple.com/auth/oauth2/token"
ASA_API_BASE = "https://api.searchads.apple.com/api/v5"
ASA_AUDIENCE = "https://appleid.apple.com"
ASA_SCOPE = "searchadsorg"
DEFAULT_JWT_LIFETIME_S = 60 * 60 * 24 * 30  # 30 days; Apple max is 180


@dataclass
class _Token:
    access_token: str
    expires_at: float  # epoch seconds


class ASAClient:
    """Async client. Lazily refreshes the OAuth token."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or get_config()
        self._token: _Token | None = None
        self._http = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> "ASAClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self._http.aclose()

    # ----- auth -------------------------------------------------------------

    def _sign_client_secret(self) -> str:
        now = int(time.time())
        payload = {
            "sub": self.cfg.asa_key_id,
            "aud": ASA_AUDIENCE,
            "iat": now,
            "exp": now + DEFAULT_JWT_LIFETIME_S,
            "iss": self.cfg.asa_issuer_id,
        }
        headers = {"alg": "ES256", "kid": self.cfg.asa_key_id}
        return jwt.encode(
            payload,
            self.cfg.asa_private_key_pem,
            algorithm="ES256",
            headers=headers,
        )

    async def _refresh_token(self) -> None:
        client_secret = self._sign_client_secret()
        data = {
            "grant_type": "client_credentials",
            "client_id": self.cfg.asa_key_id,
            "client_secret": client_secret,
            "scope": ASA_SCOPE,
        }
        r = await self._http.post(
            ASA_TOKEN_URL,
            data=data,
            headers={"Host": "appleid.apple.com", "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"ASA token exchange failed: {r.status_code} {r.text}")
        body = r.json()
        self._token = _Token(
            access_token=body["access_token"],
            expires_at=time.time() + int(body.get("expires_in", 3600)) - 60,
        )
        log.info("ASA token refreshed; expires in %ss", int(body.get("expires_in", 3600)))

    async def _bearer(self) -> str:
        if self._token is None or self._token.expires_at < time.time():
            await self._refresh_token()
        assert self._token is not None
        return self._token.access_token

    def _headers(self, *, with_org: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if with_org and self.cfg.asa_org_id:
            h["X-AP-Context"] = f"orgId={self.cfg.asa_org_id}"
        return h

    # ----- low-level request with retry/backoff ----------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        with_org: bool = True,
    ) -> Any:
        url = f"{ASA_API_BASE}{path}"
        bearer = await self._bearer()
        headers = self._headers(with_org=with_org)
        headers["Authorization"] = f"Bearer {bearer}"

        backoff = 1.0
        for attempt in range(5):
            r = await self._http.request(method, url, headers=headers, json=json_body, params=params)
            if r.status_code == 401 and attempt == 0:
                # Token may have just expired; force refresh and retry once.
                self._token = None
                headers["Authorization"] = f"Bearer {await self._bearer()}"
                continue
            if r.status_code == 429 or 500 <= r.status_code < 600:
                log.warning("ASA %s %s -> %s; retrying in %.1fs", method, path, r.status_code, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"ASA {method} {path} failed: {r.status_code} {r.text}")
            if not r.content:
                return None
            return r.json()
        raise RuntimeError(f"ASA {method} {path} exhausted retries")

    # ----- public surface --------------------------------------------------

    async def list_orgs(self) -> list[dict[str, Any]]:
        """GET /api/v5/acls — returns the orgs this API user can see."""
        body = await self._request("GET", "/acls", with_org=False)
        return body.get("data", []) if body else []

    async def list_campaigns(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/campaigns", params={"limit": 1000})
        return body.get("data", []) if body else []

    async def keyword_report(
        self,
        campaign_id: int,
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        """POST /api/v5/reports/campaigns/{id}/adgroups/{adGroupId}/keywords

        Pulls keyword-level rows for a single campaign across all its ad groups
        for the [start, end] window (inclusive). Apple groups the response by
        keywordId with daily granularity rolled up to totals when granularity
        is omitted.
        """
        body = {
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "selector": {
                "orderBy": [{"field": "localSpend", "sortOrder": "DESCENDING"}],
                "pagination": {"offset": 0, "limit": 1000},
            },
            "groupBy": ["countryCode"],
            "timeZone": self.cfg.asa_time_zone,
            "returnRowTotals": True,
            "returnGrandTotals": True,
            "returnRecordsWithNoMetrics": False,
        }
        path = f"/reports/campaigns/{campaign_id}/keywords"
        resp = await self._request("POST", path, json_body=body)
        if not resp:
            return []
        # Shape: { data: { reportingDataResponse: { row: [ { keywordId, adGroupId, metadata, granularity[] | total } ] } } }
        rows = (
            resp.get("data", {})
            .get("reportingDataResponse", {})
            .get("row", [])
        )
        # Annotate every row with the campaign it came from for downstream joins.
        for row in rows:
            row["_campaignId"] = campaign_id
        return rows


# ----- CLI: `python -m visage_ads_agent.asa_client --check` ---------------


async def _amain(args: argparse.Namespace) -> int:
    cfg = get_config()
    async with ASAClient(cfg) as client:
        if args.check:
            orgs = await client.list_orgs()
            print(json.dumps(orgs, indent=2, default=str))
            return 0
        if args.campaigns:
            camps = await client.list_campaigns()
            print(json.dumps(camps, indent=2, default=str))
            return 0
    print("nothing to do; try --check or --campaigns")
    return 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="GET /acls and print orgs")
    p.add_argument("--campaigns", action="store_true", help="list campaigns")
    args = p.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Helper used by report.py — synchronous façade over the async client.
# ---------------------------------------------------------------------------

def report_window(lookback_hours: int) -> tuple[date, date]:
    """Return (start_date, end_date) for the lookback window, in UTC."""
    end_dt = datetime.now(timezone.utc) - timedelta(hours=2)  # 2h Apple lag
    start_dt = end_dt - timedelta(hours=lookback_hours)
    return start_dt.date(), end_dt.date()


async def pull_all_keyword_rows(
    client: ASAClient, start: date, end: date
) -> list[dict[str, Any]]:
    """Fan-out across all campaigns; one request per campaign (rate-limit aware)."""
    campaigns = await client.list_campaigns()
    rows: list[dict[str, Any]] = []
    for c in campaigns:
        cid = c["id"]
        try:
            r = await client.keyword_report(cid, start, end)
            rows.extend(r)
        except Exception as e:  # noqa: BLE001
            log.exception("keyword_report failed for campaign %s: %s", cid, e)
    return rows


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """ASA report rows nest the metrics under `total` or `granularity[i]`.
    Return a flat dict with keywordId/adGroupId/campaignId + metric scalars.
    """
    meta = row.get("metadata", {}) or {}
    total = row.get("total") or (row.get("granularity", [{}])[0] if row.get("granularity") else {})

    def _money(d: dict[str, Any] | None) -> float:
        if not d:
            return 0.0
        return float(d.get("amount") or 0)

    return {
        "campaign_id": row.get("_campaignId") or meta.get("campaignId"),
        "ad_group_id": meta.get("adGroupId"),
        "keyword_id": meta.get("keywordId") or meta.get("keyword"),
        "keyword_text": meta.get("keyword") or meta.get("keywordDisplayText"),
        "match_type": meta.get("matchType"),
        "impressions": int(total.get("impressions") or 0),
        "taps": int(total.get("taps") or 0),
        "installs": int(total.get("installs") or 0),
        "new_downloads": int(total.get("newDownloads") or 0),
        "redownloads": int(total.get("redownloads") or 0),
        "local_spend_usd": _money(total.get("localSpend")),
        "avg_cpa_usd": _money(total.get("avgCPA")),
        "avg_cpt_usd": _money(total.get("avgCPT")),
        "ttr": float(total.get("ttr") or 0),
        "conversion_rate": float(total.get("conversionRate") or 0),
    }
