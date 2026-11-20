"""Compatibility entry point; installed packages use the module directly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.dataflows.trendradar_collect import main  # noqa: E402

if __name__ == "__main__":
    main()
