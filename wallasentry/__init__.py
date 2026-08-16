"""Walla Sentry — Wallapop interaction tracker.

Detects (via polling every N minutes on GitHub Actions) changes in your
Wallapop account: profile favorites, product favorites/views/price/status,
incoming reports and messages, and delivers rich Telegram notifications plus
a daily digest (evening + morning) for business analytics.

This package only talks to Wallapop's public API using your own session.
"""

__version__ = "0.1.0"
