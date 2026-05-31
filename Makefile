.PHONY: install report recommend execute migrate auth-check shell clean

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
