.PHONY: install report recommend execute migrate auth-check shell clean upload-screenshots bootstrap-campaigns bootstrap-campaigns-dry

PYTHON ?= python3

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

# Phase 1 — pull yesterday's data, post Slack digest, log snapshot
report:
	$(PYTHON) -m visage_ads_agent.report

# Phase 2 — analyze last 7 days, post recommendations to Slack (no writes to ASA)
recommend:
	$(PYTHON) -m visage_ads_agent.recommend

# Phase 3 — execute approved recommendations against ASA (guardrailed)
execute:
	$(PYTHON) -m visage_ads_agent.agent

# One-time: create the ads_agent schema + tables in AGENT_DATABASE_URL
migrate:
	$(PYTHON) -m visage_ads_agent.migrate

# Sanity check: hit ASA and print the org list (proves auth works)
auth-check:
	$(PYTHON) -m visage_ads_agent.asa_client --check

shell:
	$(PYTHON)

clean:
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov build dist *.egg-info

# -----------------------------------------------------------------------------
# One-shot launch tooling (run after Apple approves the app, OR for screenshots
# even before approval — App Store metadata is editable in WAITING_FOR_REVIEW).
# -----------------------------------------------------------------------------

# Upload the 5 marketing-overlay screenshots in iphone-6.9-final/ to ASC.
# Requires ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY_PATH in .env (or env).
SCREENSHOTS_DIR ?= ~/Desktop/visage/app-store-screenshots/iphone-6.9-final
upload-screenshots:
	$(PYTHON) -m scripts.upload_screenshots --dir $(SCREENSHOTS_DIR) --clear-existing

# Create the 3 ASA campaigns from APPLE_SEARCH_ADS.md in PAUSED state.
# Run --dry first to eyeball the plan, then drop the suffix to actually create.
bootstrap-campaigns-dry:
	$(PYTHON) -m scripts.bootstrap_campaigns --dry-run

bootstrap-campaigns:
	$(PYTHON) -m scripts.bootstrap_campaigns
