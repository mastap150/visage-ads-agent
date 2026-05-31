"""Phase 3 — Auto mode with LLM reasoning + hard guardrails. STUB.

Only enable after 30+ days of consistent Phase 2 recommend→approve patterns.

Flow per daily run:
  1. Load: yesterday's snapshot + last 30d of approved/rejected recommendations
     + their outcome (did pausing keyword X actually reduce wasted spend?).
  2. Build the prompt:
        - System prompt: cached (always identical) — the agent's role, the
          full guardrail list, the tool/JSON schema for proposals.
        - User content: the freshly-pulled report + recent decisions.
     Use Anthropic's prompt caching to keep the system prompt warm.
  3. Ask Claude (model from ANTHROPIC_MODEL, default claude-sonnet-4-6) for a
     JSON list of `Recommendation`s — same shape as Phase 2 — plus reasoning.
  4. Validate every recommendation against hard guardrails (see below).
     Anything that violates a guardrail is dropped + logged, not executed.
  5. Sort recommendations into two buckets:
       - auto-executable: pause obvious losers, ±5% micro bid moves
       - human-required:  >20% budget shifts, new keywords, creatives, bid >$3
     auto-executable → execute now, post to Slack as FYI.
     human-required  → post to Slack with thumbs-up gate (same as Phase 2).
  6. Write everything to `ads_agent.recommendations` + `ads_agent.execution_log`.

Hard guardrails (refuse to execute, regardless of LLM confidence):
  - never pause a keyword inside the "Brand Defense" campaign
    (keep brand-name protection on at all times)
  - never bid > HARD_CAP_CPT_USD (default $5.00)
  - never increase TOTAL daily spend > MAX_DAILY_BUDGET_INCREASE_PCT week/week
  - never auto-add new keywords (humans only)
  - never modify creative sets (humans only)
  - never act on data younger than MIN_DATA_AGE_HOURS (48h, MMP postback lag)
  - if any single execution call fails, abort the rest of the batch
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public surface — to be implemented after Phase 2 has 30 days of decisions.
# ---------------------------------------------------------------------------

@dataclass
class GuardrailViolation:
    rule: str
    detail: str


def build_llm_context(snapshot_days: int = 1, decisions_days: int = 30) -> dict[str, Any]:
    """TODO: assemble {today_report, recent_decisions, outcome_attribution}."""
    raise NotImplementedError


def ask_claude_for_proposals(context: dict[str, Any]) -> list[dict[str, Any]]:
    """TODO: anthropic.Anthropic().messages.create(...)

    - model: cfg.anthropic_model (Sonnet 4.6 minimum; bump to newest available)
    - system: SYSTEM_PROMPT (declared as cached content block — same every run)
    - messages: user turn with JSON-formatted context
    - tools: define a structured `propose_changes` tool the model must call
    - temperature: 0 for reproducibility
    Return parsed tool-input list of recommendation dicts.
    """
    raise NotImplementedError


def validate_against_guardrails(
    proposals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[GuardrailViolation]]:
    """TODO: return (accepted, violations). Apply every rule listed in the
    module docstring. Be aggressive — reject anything ambiguous.
    """
    raise NotImplementedError


def partition_auto_vs_human(proposals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """TODO: split into auto-executable vs human-approval-required."""
    raise NotImplementedError


def execute(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TODO: call ASA Campaign Management endpoints:
       - PUT /campaigns/{id}/adgroups/{adGroupId}/targetingkeywords/{keywordId}
         body: {"status": "PAUSED"} or {"bidAmount": {"amount": "1.20", "currency": "USD"}}
       - PUT /campaigns/{id}  body: {"dailyBudgetAmount": {...}}
    Persist a row to ads_agent.execution_log for each call (success or fail).
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# System prompt — to be cached on the Anthropic side.
# Keep this string identical across runs to maximize cache hits.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TODO = """
You are the media-buying agent for Visage, an iOS app that identifies public
figures from photos. You optimize Apple Search Ads campaigns against backend
conversion data (signups → trials → paid).

Goals, in order:
  1. Drive paid subscribers ($2.99/mo) at CPA-to-paid ≤ TARGET_CPA_PAID_USD.
  2. Preserve brand-name search defense (Brand campaign keywords).
  3. Minimize wasted spend; reallocate from red to green keywords.

Hard rules you MUST follow (you will be rejected by the guardrail layer if
you propose anything that violates these):
  - never pause keywords inside the "Visage — Brand Defense" campaign
  - never propose a bid above HARD_CAP_CPT_USD
  - never propose a total-daily-spend increase above MAX_DAILY_BUDGET_INCREASE_PCT week-over-week
  - never propose new keywords or creative-set changes
  - never act on data younger than MIN_DATA_AGE_HOURS (48h MMP lag)

Respond ONLY via the `propose_changes` tool. Every proposal must include:
  - kind: "pause" | "scale" | "bid" | "budget"
  - target_kind: "keyword" | "adgroup" | "campaign"
  - target_id: integer
  - payload: structured per kind
  - rationale: one paragraph citing specific metrics from the input data
""".strip()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("Phase 3 not implemented yet. Coming after Phase 2 has 30 days of decisions.")


if __name__ == "__main__":
    main()
