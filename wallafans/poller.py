"""Poller: the diff engine.

Every run it fetches a snapshot of your account (stats counters, your items,
conversations) and compares it with the previous snapshot, producing Event
objects for every detected change.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import models as M
from .client import WallapopAPI
from .store import StateStore

log = logging.getLogger("wallafans.poller")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Poller:
    def __init__(self, api: WallapopAPI, store: StateStore):
        self.api = api
        self.store = store
        self.state: dict = {}
        self.events: list[M.Event] = []

    # ── snapshot builders ───────────────────────────────────────────
    def _fetch_counters(self) -> dict[str, int]:
        stats = self.api.get_stats(settings_user_id())
        out: dict[str, int] = {}
        for counter in stats.get("counters") or []:
            if isinstance(counter, dict) and "type" in counter:
                out[counter["type"]] = _num(counter.get("value"))
        return out

    def _fetch_items(self) -> list[dict]:
        """List my items and enrich each with the full detail (counters).

        List endpoint (verified): GET /api/v3/users/{uid}/items -> basic fields.
        Detail endpoint (verified): GET /api/v3/items/{id} -> counters with
        favorites/views/conversations, price.cash, images[].urls, share_url.
        """
        basic = self.api.list_my_items(settings_user_id())
        out: list[dict] = []
        for raw in basic:
            item_id = str(raw.get("id", ""))
            merged = dict(raw)
            if item_id:
                detail = self.api.get_item(item_id)
                if isinstance(detail, dict) and detail:
                    merged = {**raw, **detail}
            out.append(merged)
        return out

    @staticmethod
    def _item_model(raw: dict) -> M.Product:
        title = raw.get("title")
        if isinstance(title, dict):
            title = title.get("original", "")

        price: float | None = None
        currency = "EUR"
        p = raw.get("price")
        if isinstance(p, dict):
            cash = p.get("cash") if isinstance(p.get("cash"), dict) else p
            price = _float(cash.get("amount"))
            currency = str(cash.get("currency", "EUR") or "EUR")
        elif p is not None:
            price = _float(p)
        if price is not None and price > 1000:  # cents vs euros heuristics
            price = price / 100.0

        images = raw.get("images") or []
        image_url = ""
        if images and isinstance(images[0], dict):
            im = images[0]
            urls = im.get("urls") if isinstance(im.get("urls"), dict) else {}
            image_url = (
                urls.get("small") or im.get("small")
                or urls.get("medium") or im.get("medium")
                or urls.get("large") or im.get("url") or ""
            )

        counters = raw.get("counters") if isinstance(raw.get("counters"), dict) else {}
        views = _num(counters.get("views")) or _num(raw.get("views"))
        favorites = _num(counters.get("favorites")) or _num(
            raw.get("favorite_count")
            or raw.get("favorites_count")
            or raw.get("favourites_count")
        )

        url = (
            str(raw.get("share_url", "") or raw.get("url_share", "") or "")
            or str(raw.get("web_slug", "") or "")
            or f"https://es.wallapop.com/item/{raw.get('id')}"
        )
        return M.Product(
            id=str(raw.get("id", "")),
            title=str(title or ""),
            price=price,
            currency=currency,
            status=str(raw.get("sale_status", "") or raw.get("status", "") or ""),
            category_id=str(raw.get("category_id", "") or ""),
            views=views,
            favorites=favorites,
            image_url=image_url,
            url=url,
            published_at=str(raw.get("creation_date", "") or ""),
        )

    def _fetch_conversations(self) -> list[dict]:
        return self.api.list_conversations()

    def _fetch_unread_messages(self) -> int:
        status, body = self.api.get(
            "/api/v3/instant-messaging/messages/unread", auth=True
        )
        if isinstance(body, dict):
            return _num(body.get("unread_counter"))
        return 0

    # ── diff ────────────────────────────────────────────────────────
    def _diff_messages(self, prev: int, curr: int) -> None:
        if curr <= prev:
            return
        self.events.append(M.Event(
            kind=M.MESSAGE_NEW, ts=utcnow(), source=M.SOURCE_POLL,
            delta=curr - prev,
            totals={"unread_messages": curr},
            dedup_key=f"msg:{prev}:{curr}",
        ))
    def _diff_counters(self, prev: dict, curr: dict) -> None:
        p_fav = prev.get("profileFavoritedReceived", 0)
        c_fav = curr.get("profileFavoritedReceived", p_fav)
        p_rep = prev.get("reports_received", 0)
        c_rep = curr.get("reports_received", p_rep)

        if c_fav != p_fav:
            kind = M.PROFILE_FAVORITE_ADDED if c_fav > p_fav else M.PROFILE_FAVORITE_REMOVED
            self.events.append(M.Event(
                kind=kind,
                ts=utcnow(),
                source=M.SOURCE_POLL,
                delta=c_fav - p_fav,
                totals={"profile_favorites": c_fav},
                dedup_key=f"profile_fav:{p_fav}:{c_fav}",
            ))
        if c_rep != p_rep:
            self.events.append(M.Event(
                kind=M.REPORT_RECEIVED,
                ts=utcnow(),
                source=M.SOURCE_POLL,
                delta=c_rep - p_rep,
                totals={"reports_received": c_rep},
                dedup_key=f"report:{p_rep}:{c_rep}",
            ))

    def _diff_items(self, prev_items: dict, curr_items: list[dict]) -> None:
        for raw in curr_items:
            item = self._item_model(raw)
            if not item.id:
                continue
            prev = prev_items.get(item.id)
            if prev is None:
                # New product: nothing to notify, but keep it tracked.
                self._upsert_item(item, notify=False)
                continue
            deltas: list[M.Event] = []
            if item.favorites != prev.favorites:
                deltas.append(M.Event(
                    kind=M.PRODUCT_FAVORITE_DELTA,
                    ts=utcnow(), source=M.SOURCE_POLL,
                    product=item, delta=item.favorites - prev.favorites,
                    totals={"favorites": item.favorites},
                    dedup_key=f"item_fav:{item.id}:{prev.favorites}:{item.favorites}",
                ))
            if item.views != prev.views:
                deltas.append(M.Event(
                    kind=M.PRODUCT_VIEWS, ts=utcnow(), source=M.SOURCE_POLL,
                    product=item, delta=item.views - prev.views,
                    totals={"views": item.views},
                    dedup_key=f"item_views:{item.id}:{prev.views}:{item.views}",
                ))
            if item.price != prev.price:
                deltas.append(M.Event(
                    kind=M.PRODUCT_PRICE_CHANGE, ts=utcnow(), source=M.SOURCE_POLL,
                    product=item, delta=int((item.price or 0) - (prev.price or 0)),
                    totals={"price": item.price},
                    dedup_key=f"item_price:{item.id}:{prev.price}:{item.price}",
                ))
            if (item.status or "") != (prev.status or ""):
                deltas.append(M.Event(
                    kind=M.PRODUCT_STATUS_CHANGE, ts=utcnow(), source=M.SOURCE_POLL,
                    product=item, totals={"status": item.status},
                    dedup_key=f"item_status:{item.id}:{prev.status}:{item.status}",
                ))
            for event in deltas:
                self.events.append(event)
            self._upsert_item(item, notify=False)

    def _upsert_item(self, item: M.Product, notify: bool = False) -> None:
        # Merge into the running snapshot (kept in memory until run() saves it).
        items = self._running_items()
        items[item.id] = item.to_dict()
        self._set_items(items)

    # running snapshot helpers (kept simple: build on each run)
    def _running_items(self) -> dict:
        return self.state.get("items", {})

    def _set_items(self, items: dict) -> None:
        self.state["items"] = items

    def _diff_conversations(self, prev_ids: set, curr: list[dict]) -> None:
        current_ids: set[str] = set()
        for conv in curr:
            conv_id = str(conv.get("id", ""))
            if not conv_id:
                continue
            current_ids.add(conv_id)
            if conv_id not in prev_ids:
                other = self._conversation_actor(conv)
                self.events.append(M.Event(
                    kind=M.CONVERSATION_NEW, ts=utcnow(), source=M.SOURCE_POLL,
                    actor=other, raw=conv, dedup_key=f"conv:{conv_id}",
                ))
        self.state["conversation_ids"] = list(current_ids)

    @staticmethod
    def _conversation_actor(conv: dict) -> M.Actor:
        other = conv.get("other_user") or conv.get("user") or {}
        if isinstance(other, dict):
            return M.Actor(
                user_id=str(other.get("id", "")),
                micro_name=str(other.get("micro_name", "") or other.get("name", "")),
                web_slug=str(other.get("web_slug", "")),
                country=str(other.get("country", "")),
            )
        return M.Actor(user_id=str(conv.get("other_user_id", "")))

    # ── main entry ──────────────────────────────────────────────────
    def run(self) -> tuple[list[M.Event], dict]:
        self.state = self.store.load() or {}
        first_run = not bool(self.state.get("last_poll"))
        prev_counters = self.state.get("counters", {})
        prev_items = self.state.get("items", {})
        prev_convs = set(self.state.get("conversation_ids", []))

        counters = self._fetch_counters()
        items = self._fetch_items()
        conversations = self._fetch_conversations()
        unread = self._fetch_unread_messages()

        if first_run:
            # Baseline snapshot: store counters/items/messages but emit no
            # events, so pre-existing state (e.g. old reports) is not notified.
            log.info("first run: storing baseline snapshot without notifications")
        else:
            self._diff_counters(prev_counters, counters)
            self._diff_items(prev_items, items)
            self._diff_conversations(prev_convs, conversations)
            self._diff_messages(int(prev_counters.get("unread_messages", 0)), unread)

        self.state["counters"] = counters
        self.state["counters"]["unread_messages"] = unread
        self.state["last_poll"] = utcnow()

        return self.events, self.state


def settings_user_id() -> str:
    from .config import settings
    return settings.user_id
