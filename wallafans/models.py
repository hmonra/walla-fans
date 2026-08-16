"""Data models shared across the tracker.

These are plain dataclasses. Every value is optional because Wallapop's API
shape is only partially known; defensive parsing keeps the tracker alive even
when a field is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Event kinds
PROFILE_FAVORITE_ADDED = "profile_favorite_added"
PROFILE_FAVORITE_REMOVED = "profile_favorite_removed"
PRODUCT_FAVORITE_DELTA = "product_favorite_delta"
PRODUCT_VIEWS = "product_views"
PRODUCT_PRICE_CHANGE = "product_price_change"
PRODUCT_STATUS_CHANGE = "product_status_change"
REPORT_RECEIVED = "report_received"
CONVERSATION_NEW = "conversation_new"
MESSAGE_NEW = "message_new"
PUBNUB_FAVORITE = "pubnub_favorite"

SOURCE_POLL = "poll"
SOURCE_PUBNUB = "pubnub"


@dataclass
class Actor:
    user_id: str = ""
    micro_name: str = ""
    web_slug: str = ""
    country: str = ""
    rating_average: float | None = None
    sells: int = 0
    buys: int = 0
    publish: int = 0
    reviews: int = 0
    profile_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Actor | None":
        if not data:
            return None
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Product:
    id: str = ""
    title: str = ""
    price: float | None = None
    currency: str = "EUR"
    status: str = ""          # ACTIVE / RESERVED / SOLD / ...
    category_id: str = ""
    views: int = 0
    favorites: int = 0
    image_url: str = ""
    url: str = ""
    published_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Product | None":
        if not data:
            return None
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class ProfileSnapshot:
    ts: str = ""
    counters: dict[str, int] = field(default_factory=dict)
    profile_favorites: int = 0
    reports_received: int = 0

    def to_dict(self) -> dict:
        return {"ts": self.ts, "counters": self.counters,
                "profile_favorites": self.profile_favorites,
                "reports_received": self.reports_received}


@dataclass
class Event:
    kind: str
    ts: str                      # ISO UTC
    source: str = SOURCE_POLL
    actor: Actor | None = None
    product: Product | None = None
    delta: int = 0
    totals: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    dedup_key: str = ""          # used to avoid duplicate notifications

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ts": self.ts,
            "source": self.source,
            "actor": self.actor.to_dict() if self.actor else None,
            "product": self.product.to_dict() if self.product else None,
            "delta": self.delta,
            "totals": self.totals,
            "raw": self.raw,
            "dedup_key": self.dedup_key,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Event | None":
        if not data:
            return None
        return cls(
            kind=data.get("kind", ""),
            ts=data.get("ts", ""),
            source=data.get("source", SOURCE_POLL),
            actor=Actor.from_dict(data.get("actor")),
            product=Product.from_dict(data.get("product")),
            delta=data.get("delta", 0),
            totals=data.get("totals", {}),
            raw=data.get("raw", {}),
            dedup_key=data.get("dedup_key", ""),
        )
