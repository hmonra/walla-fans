"""PubNub history fetch (without the SDK).

PubNub keeps a channel history server-side. A GitHub Actions job that runs
every N minutes can fetch messages published while it was offline, which is
the only viable way to learn *who* favorited you (if the event payload carries
the actor id).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import endpoints as E
from . import models as M
from .config import settings

log = logging.getLogger("wallafans.pubnub")

UUID = "wallafans-gha"


def fetch_history(
    subscribe_key: str,
    channel: str,
    auth_token: str,
    start_timetoken: int = 0,
    count: int = 100,
    timeout: int = 20,
) -> tuple[list[dict], int]:
    """Return (messages, new_start_timetoken). Messages are dicts with
    'message', 'timetoken' and optional 'publisher'."""
    url = f"{E.PUBNUB_PS}/v3/history/sub-key/{subscribe_key}/channel/{channel}"
    params: dict[str, Any] = {
        "count": count,
        "auth": auth_token,
        "uuid": UUID,
        "pnsdk": "WallaFans/0.1",
    }
    if start_timetoken:
        params["start"] = start_timetoken
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        log.warning("PubNub history request failed: %s", exc)
        return [], start_timetoken

    if resp.status_code == 403:
        log.warning("PubNub history forbidden (PAM token invalid or channel wrong)")
        return [], start_timetoken
    if resp.status_code != 200:
        log.warning("PubNub history HTTP %s: %s", resp.status_code, resp.text[:200])
        return [], start_timetoken

    try:
        payload = resp.json()
    except ValueError:
        return [], start_timetoken

    channels = payload.get("channels") or {}
    items = channels.get(channel, [])
    messages = [i for i in items if isinstance(i, dict)]
    new_start = max([int(i.get("timetoken", 0)) for i in messages] + [start_timetoken])
    return messages, new_start


def parse_favorite_events(messages: list[dict]) -> list[M.Event]:
    """Turn raw PubNub messages into favorite events with actor identity.

    Event schemas are unknown until the capture experiment; this parser is
    deliberately permissive and looks for favorite/favorito markers and any
    plausible actor id field. Raw payloads are preserved for later tuning.
    """
    events: list[M.Event] = []
    for item in messages:
        msg = item.get("message")
        if not isinstance(msg, dict):
            continue
        raw_str = str(msg)
        is_favorite = (
            "favorite" in raw_str.lower()
            or "favorito" in raw_str.lower()
            or "favourit" in raw_str.lower()
        )
        if not is_favorite:
            continue

        actor_id = (
            msg.get("actorId")
            or msg.get("actor_id")
            or msg.get("userId")
            or msg.get("user_id")
            or msg.get("sourceUserId")
            or msg.get("from")
            or msg.get("publisher")
            or item.get("publisher")
            or ""
        )
        item_id = (
            msg.get("itemId")
            or msg.get("item_id")
            or msg.get("item")
            or msg.get("productId")
            or msg.get("listingId")
            or ""
        )
        target = msg.get("target") or msg.get("type") or ""
        actor = M.Actor(user_id=str(actor_id)) if actor_id else None
        product = M.Product(id=str(item_id)) if item_id else None
        events.append(M.Event(
            kind=M.PUBNUB_FAVORITE,
            ts=iso_from_timetoken(item.get("timetoken", 0)),
            source=M.SOURCE_PUBNUB,
            actor=actor,
            product=product,
            raw=msg,
            dedup_key=f"pubnub:{item.get('timetoken', '')}",
        ))
    return events


def iso_from_timetoken(tt: str | int) -> str:
    """PubNub timetokens are microseconds since epoch."""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(tt) / 1_000_000)) + "Z"
    except (ValueError, OSError):
        return ""


def poll_history() -> tuple[list[M.Event], int]:
    """Convenience wrapper using configured settings. Returns (events, cursor)."""
    if not (settings.pubnub_auth_token and settings.pubnub_channel and settings.pubnub_subscribe_key):
        return [], settings.pubnub_last_timetoken
    messages, new_start = fetch_history(
        settings.pubnub_subscribe_key,
        settings.pubnub_channel,
        settings.pubnub_auth_token,
        settings.pubnub_last_timetoken,
    )
    events = parse_favorite_events(messages)
    return events, new_start
