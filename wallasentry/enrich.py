"""Enrich actors with their public profile data.

Uses only public endpoints (/users/{id} and /users/{id}/stats), best-effort.
"""
from __future__ import annotations

import logging

from .client import WallapopAPI
from .models import Actor

log = logging.getLogger("wallasentry.enrich")


def enrich_actor(api: WallapopAPI, actor: Actor) -> Actor:
    if not actor or not actor.user_id:
        return actor

    profile = api.get_public_profile(actor.user_id)
    if isinstance(profile, dict) and profile:
        actor.micro_name = (
            profile.get("micro_name")
            or profile.get("microName")
            or profile.get("name")
            or actor.micro_name
        )
        actor.web_slug = (
            profile.get("web_slug")
            or profile.get("slug")
            or profile.get("url")
            or actor.web_slug
        )
        actor.country = profile.get("country") or actor.country
        actor.profile_url = (
            f"https://es.wallapop.com/user/{actor.web_slug or actor.user_id}"
        )

    stats = api.get_stats(actor.user_id, auth=False)
    if isinstance(stats, dict) and stats:
        counters = {c.get("type"): c.get("value", 0) for c in stats.get("counters") or []}
        actor.rating_average = _float(stats.get("rating_average")) or (
            _float(counters.get("rating_average"))
        )
        actor.sells = _int(counters.get("sells"))
        actor.buys = _int(counters.get("buys"))
        actor.publish = _int(counters.get("publish"))
        actor.reviews = _int(counters.get("reviews"))
    return actor


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0