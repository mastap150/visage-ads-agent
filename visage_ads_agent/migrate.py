"""CLI: ensure the agent's schema + tables exist."""
from __future__ import annotations

import logging

from .agent_db import ensure_schema


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ensure_schema()
    print("ok")


if __name__ == "__main__":
    main()
