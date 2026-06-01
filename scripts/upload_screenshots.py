"""Upload App Store screenshots to App Store Connect via the ASC API.

USAGE
    python -m scripts.upload_screenshots \\
        --dir ~/Desktop/visage/app-store-screenshots/iphone-6.9-final \\
        --display-type APP_IPHONE_69 \\
        [--locale en-US] [--clear-existing] [--dry-run]

What it does
    1. Mints an App Store Connect JWT (ES256, aud=appstoreconnect-v1) with the
       AuthKey_<KEY_ID>.p8 file. NOTE: this is DIFFERENT from the Apple Search
       Ads OAuth key — ASC uses the legacy `.p8` flow and a 20-minute JWT.

KNOWN GOTCHAS
    - Apple has NO `APP_IPHONE_69` enum despite iPhone 6.9" being a real
      display class. 1290x2796 screenshots are filed under `APP_IPHONE_67`
      (the iPhone 6.7" set is what the 6.9" Pro Max also reads from).
    - Once a version is in WAITING_FOR_REVIEW / IN_REVIEW, Apple LOCKS the
      screenshot set: you can't delete, replace, or reorder. The script
      will fail with `STATE_ERROR: Can't Delete Screenshot After Submit
      for review`. Wait for the review to resolve (approve or reject),
      then re-run.
    2. Locates the Visage app by bundle ID, then its in-progress App Store
       version (whatever state Apple has it in — works while WAITING_FOR_REVIEW
       and after READY_FOR_SALE).
    3. Locates the en-US localization on that version.
    4. Finds (or creates) the screenshot set for the requested display type
       (default APP_IPHONE_69 = iPhone 6.9" display).
    5. For each .png in the source directory, in lexical order:
         - POST /v1/appScreenshots → reserves an ID and returns one or more
           `uploadOperations` (presigned multipart PUTs into Apple's S3).
         - For each operation, slice the file bytes per offset/length and PUT
           with the EXACT headers Apple returns (any deviation 403s).
         - PATCH /v1/appScreenshots/{id} with `uploaded=true` and the file's
           MD5 hex digest as `sourceFileChecksum`.
    6. Sets the screenshot order to match the lexical filename order via
       PATCH /v1/appScreenshotSets/{id}/relationships/appScreenshots.

ENV (loaded from .env via the agent's config or os.environ directly):
    ASC_KEY_ID         e.g. NMKX98FQ8B
    ASC_ISSUER_ID      e.g. 3207b23a-16e9-49ca-893b-4dda7d4abfb9
    ASC_PRIVATE_KEY_PATH   path to AuthKey_<KEY_ID>.p8
    OR
    ASC_PRIVATE_KEY_PEM    PEM contents inline (Render-friendly)
    ASC_BUNDLE_ID      defaults to com.pgammedia.visage
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("upload_screenshots")

ASC_API_BASE = "https://api.appstoreconnect.apple.com"
ASC_AUDIENCE = "appstoreconnect-v1"
JWT_LIFETIME_S = 60 * 19  # Apple's hard ceiling is 20 minutes.

DEFAULT_BUNDLE_ID = "com.pgammedia.visage"
DEFAULT_LOCALE = "en-US"


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------

def _load_pem() -> str:
    pem = os.environ.get("ASC_PRIVATE_KEY_PEM", "").strip()
    if pem:
        return pem.replace("\\n", "\n")
    path = os.environ.get("ASC_PRIVATE_KEY_PATH", "").strip()
    if not path:
        raise RuntimeError("Set ASC_PRIVATE_KEY_PATH or ASC_PRIVATE_KEY_PEM")
    return Path(os.path.expanduser(path)).read_text()


def make_jwt() -> str:
    key_id = os.environ.get("ASC_KEY_ID", "").strip()
    issuer = os.environ.get("ASC_ISSUER_ID", "").strip()
    if not key_id or not issuer:
        raise RuntimeError("ASC_KEY_ID and ASC_ISSUER_ID must be set")
    now = int(time.time())
    payload = {
        "iss": issuer,
        "iat": now,
        "exp": now + JWT_LIFETIME_S,
        "aud": ASC_AUDIENCE,
    }
    return jwt.encode(
        payload, _load_pem(), algorithm="ES256",
        headers={"alg": "ES256", "kid": key_id, "typ": "JWT"},
    )


# -----------------------------------------------------------------------------
# Thin ASC client
# -----------------------------------------------------------------------------

class ASCClient:
    def __init__(self):
        self._token = make_jwt()
        self._token_minted_at = time.time()
        self._http = httpx.Client(
            base_url=ASC_API_BASE,
            timeout=60.0,
            headers={"Content-Type": "application/json"},
        )

    def _maybe_refresh(self) -> None:
        if time.time() - self._token_minted_at > JWT_LIFETIME_S - 60:
            self._token = make_jwt()
            self._token_minted_at = time.time()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        self._maybe_refresh()
        h = kw.pop("headers", {}) or {}
        h["Authorization"] = f"Bearer {self._token}"
        r = self._http.request(method, path, headers=h, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"ASC {method} {path} {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._req("GET", path, params=params).json()

    def post(self, path: str, body: dict) -> dict:
        return self._req("POST", path, json=body).json()

    def patch(self, path: str, body: dict) -> dict | None:
        r = self._req("PATCH", path, json=body)
        if not r.content:
            return None
        return r.json()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# All editable / pending states. Apple recently consolidated; this list is
# defensive — if a state we don't list shows up, we'll just see "no editable
# version" and the user can pass --version-id explicitly.
EDITABLE_STATES = (
    "DEVELOPER_REJECTED",
    "REJECTED",
    "METADATA_REJECTED",
    "PREPARE_FOR_SUBMISSION",
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
    "PENDING_DEVELOPER_RELEASE",
    "PROCESSING_FOR_APP_STORE",
    "READY_FOR_SALE",
)


def find_app_id(client: ASCClient, bundle_id: str) -> str:
    body = client.get("/v1/apps", params={"filter[bundleId]": bundle_id, "limit": 1})
    data = body.get("data", [])
    if not data:
        raise RuntimeError(f"No app found with bundleId={bundle_id}")
    return data[0]["id"]


def find_editable_version_id(client: ASCClient, app_id: str) -> str:
    body = client.get(
        f"/v1/apps/{app_id}/appStoreVersions",
        params={"filter[platform]": "IOS", "limit": 50},
    )
    versions = body.get("data", [])
    if not versions:
        raise RuntimeError(f"No App Store versions on app {app_id}")
    # Prefer the most recent editable one.
    for v in versions:
        state = v["attributes"].get("appStoreState")
        if state in EDITABLE_STATES:
            log.info("Using version %s (state=%s, ver=%s)",
                     v["id"], state, v["attributes"].get("versionString"))
            return v["id"]
    raise RuntimeError(
        f"No editable version. States seen: "
        f"{[v['attributes'].get('appStoreState') for v in versions]}"
    )


def find_localization_id(client: ASCClient, version_id: str, locale: str) -> str:
    body = client.get(
        f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
        params={"limit": 200},
    )
    for loc in body.get("data", []):
        if loc["attributes"].get("locale") == locale:
            return loc["id"]
    raise RuntimeError(f"Locale {locale} not found on version {version_id}")


def get_or_create_screenshot_set(
    client: ASCClient, localization_id: str, display_type: str,
) -> str:
    body = client.get(
        f"/v1/appStoreVersionLocalizations/{localization_id}/appScreenshotSets",
        params={"limit": 50},
    )
    for s in body.get("data", []):
        if s["attributes"].get("screenshotDisplayType") == display_type:
            log.info("Reusing existing screenshot set %s (%s)", s["id"], display_type)
            return s["id"]
    log.info("Creating new screenshot set (%s)", display_type)
    body = client.post("/v1/appScreenshotSets", {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": localization_id},
                },
            },
        },
    })
    return body["data"]["id"]


def list_set_screenshots(client: ASCClient, set_id: str) -> list[dict[str, Any]]:
    body = client.get(f"/v1/appScreenshotSets/{set_id}/appScreenshots",
                      params={"limit": 50})
    return body.get("data", [])


def delete_screenshot(client: ASCClient, screenshot_id: str) -> None:
    client._req("DELETE", f"/v1/appScreenshots/{screenshot_id}")


# -----------------------------------------------------------------------------
# Upload protocol
# -----------------------------------------------------------------------------

def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def reserve_screenshot(
    client: ASCClient, set_id: str, file_name: str, file_size: int,
) -> dict[str, Any]:
    """Returns the appScreenshots resource including `uploadOperations`."""
    body = client.post("/v1/appScreenshots", {
        "data": {
            "type": "appScreenshots",
            "attributes": {"fileName": file_name, "fileSize": file_size},
            "relationships": {
                "appScreenshotSet": {
                    "data": {"type": "appScreenshotSets", "id": set_id},
                },
            },
        },
    })
    return body["data"]


def execute_upload(file_bytes: bytes, operations: list[dict[str, Any]]) -> None:
    """Run every presigned PUT Apple returned, in order.

    Each operation has: method, url, length, offset, requestHeaders[{name,value}].
    Apple requires the headers to be echoed verbatim — no extras.
    """
    with httpx.Client(timeout=120.0) as client:
        for op in operations:
            chunk = file_bytes[op["offset"]:op["offset"] + op["length"]]
            headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
            r = client.request(op["method"], op["url"], content=chunk, headers=headers)
            if r.status_code >= 300:
                raise RuntimeError(
                    f"Upload chunk failed ({op['method']} {op['url'][:80]}…): "
                    f"{r.status_code} {r.text[:400]}"
                )


def commit_screenshot(client: ASCClient, screenshot_id: str, checksum: str) -> None:
    client.patch(f"/v1/appScreenshots/{screenshot_id}", {
        "data": {
            "type": "appScreenshots",
            "id": screenshot_id,
            "attributes": {"uploaded": True, "sourceFileChecksum": checksum},
        },
    })


def reorder_set(client: ASCClient, set_id: str, ordered_ids: list[str]) -> None:
    client.patch(f"/v1/appScreenshotSets/{set_id}/relationships/appScreenshots", {
        "data": [{"type": "appScreenshots", "id": sid} for sid in ordered_ids],
    })


# -----------------------------------------------------------------------------
# Main flow
# -----------------------------------------------------------------------------

@dataclass
class Plan:
    files: list[Path]
    bundle_id: str
    display_type: str
    locale: str
    clear_existing: bool
    dry_run: bool


def discover_files(directory: Path) -> list[Path]:
    pngs = sorted(p for p in directory.iterdir()
                  if p.suffix.lower() == ".png" and not p.name.startswith("."))
    if not pngs:
        raise RuntimeError(f"No .png files in {directory}")
    return pngs


def run(plan: Plan) -> int:
    log.info("Plan: %d files, display=%s, locale=%s, bundle=%s, clear=%s, dry=%s",
             len(plan.files), plan.display_type, plan.locale, plan.bundle_id,
             plan.clear_existing, plan.dry_run)
    for p in plan.files:
        log.info("  → %s (%d bytes)", p.name, p.stat().st_size)
    if plan.dry_run:
        log.info("DRY RUN — no API calls.")
        return 0

    c = ASCClient()
    app_id = find_app_id(c, plan.bundle_id)
    log.info("appId = %s", app_id)
    version_id = find_editable_version_id(c, app_id)
    loc_id = find_localization_id(c, version_id, plan.locale)
    log.info("localizationId = %s", loc_id)
    set_id = get_or_create_screenshot_set(c, loc_id, plan.display_type)

    if plan.clear_existing:
        existing = list_set_screenshots(c, set_id)
        log.info("Clearing %d existing screenshot(s) from set", len(existing))
        for s in existing:
            delete_screenshot(c, s["id"])

    uploaded_ids: list[str] = []
    for path in plan.files:
        data = path.read_bytes()
        log.info("Uploading %s (%d bytes)…", path.name, len(data))
        resource = reserve_screenshot(c, set_id, path.name, len(data))
        sid = resource["id"]
        ops = resource["attributes"]["uploadOperations"]
        execute_upload(data, ops)
        commit_screenshot(c, sid, md5_hex(data))
        log.info("  ✓ id=%s", sid)
        uploaded_ids.append(sid)

    log.info("Setting display order on set %s", set_id)
    reorder_set(c, set_id, uploaded_ids)
    log.info("Done. %d screenshot(s) uploaded and ordered.", len(uploaded_ids))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", required=True, help="Directory containing PNGs (sorted lexically)")
    p.add_argument("--display-type", default="APP_IPHONE_67",
                   help="Apple's ScreenshotDisplayType. Default APP_IPHONE_67 — Apple files "
                        "both iPhone 6.7\" and 6.9\" displays (same 1290x2796 resolution) "
                        "under this enum; there is no APP_IPHONE_69.")
    p.add_argument("--locale", default=DEFAULT_LOCALE)
    p.add_argument("--bundle-id", default=os.environ.get("ASC_BUNDLE_ID", DEFAULT_BUNDLE_ID))
    p.add_argument("--clear-existing", action="store_true",
                   help="Delete existing screenshots in the target set before uploading")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    directory = Path(os.path.expanduser(args.dir)).resolve()
    if not directory.is_dir():
        log.error("Not a directory: %s", directory)
        sys.exit(2)
    files = discover_files(directory)
    plan = Plan(files=files, bundle_id=args.bundle_id, display_type=args.display_type,
                locale=args.locale, clear_existing=args.clear_existing, dry_run=args.dry_run)
    sys.exit(run(plan))


if __name__ == "__main__":
    main()
