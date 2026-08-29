"""Print the eight-area coverage table. No API key, no network.

    uv run python scripts/coverage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.coverage import render
from cre_agent.store import Store
from cre_agent.watchlist import Watchlist

if __name__ == "__main__":
    print(render(Store.load(), Watchlist.load()))
