"""Self-contained hotlist/RSS collection for TradingAgents.

An independent implementation of the public source interfaces and the existing
news evidence database contract. No external TrendRadar checkout is imported.
"""

from .collector import collect_once

__all__ = ["collect_once"]
