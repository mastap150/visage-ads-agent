"""Slack incoming-webhook digest."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


def post_digest(webhook_url: str, blocks: list[dict[str, Any]], *, fallback_text: str) -> None:
    """Post a Block Kit message. No-op when webhook_url is empty (local runs)."""
    if not webhook_url:
        log.info("SLACK_WEBHOOK_URL unset — printing digest locally:\n%s", fallback_text)
        print(fallback_text)
        return
    payload = {"text": fallback_text, "blocks": blocks}
    r = httpx.post(webhook_url, json=payload, timeout=15.0)
    if r.status_code >= 300:
        raise RuntimeError(f"Slack webhook failed: {r.status_code} {r.text}")


def build_digest_blocks(
    *,
    window_label: str,
    totals: dict[str, Any],
    rows: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (block_kit_blocks, plain_text_fallback).

    `rows` must be pre-sorted (e.g. spend DESC). Each row should have keys:
      keyword_text, spend_usd, taps, installs, signups, trials, paid,
      cpa_paid_usd (None-OK), status.
    """
    header = f"*Visage ASA — {window_label}*"
    summary = (
        f"Spend ${totals.get('spend_usd', 0):.2f}  •  "
        f"Installs {totals.get('installs', 0)}  •  "
        f"Signups {totals.get('signups', 0)}  •  "
        f"Trials {totals.get('trials', 0)}  •  "
        f"Paid {totals.get('paid', 0)}"
    )

    # Markdown table. Slack renders fixed-width in code blocks.
    header_row = f"{'kw':<22} {'spend':>7} {'inst':>4} {'sgn':>3} {'trl':>3} {'paid':>4} {'cpa$paid':>9} {'st':>3}"
    lines = [header_row, "-" * len(header_row)]
    for r in rows[:30]:
        kw = (r.get("keyword_text") or f"kw#{r.get('keyword_id')}")[:22]
        cpa = r.get("cpa_paid_usd")
        cpa_s = f"{cpa:.2f}" if cpa is not None else "    -"
        line = (
            f"{kw:<22} "
            f"{r.get('spend_usd', 0):>7.2f} "
            f"{r.get('installs', 0):>4} "
            f"{r.get('signups', 0):>3} "
            f"{r.get('trials', 0):>3} "
            f"{r.get('paid', 0):>4} "
            f"{cpa_s:>9} "
            f"{r.get('status', '?'):>3}"
        )
        lines.append(line)
    table = "```\n" + "\n".join(lines) + "\n```"

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {"type": "section", "text": {"type": "mrkdwn", "text": table}},
    ]
    if notes:
        notes_md = "\n".join(f"• {n}" for n in notes)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": notes_md}})

    text_fallback = f"{header}\n{summary}\n\n{table}"
    if notes:
        text_fallback += "\n\nNotes:\n" + "\n".join(f"- {n}" for n in notes)
    return blocks, text_fallback
