#!/usr/bin/env python3
"""Local analytics helper: turns the event log into CSV + a short summary.

Usage:
    python scripts/analytics.py            # print summary
    python scripts/analytics.py --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wallafans.store import EventLog  # noqa: E402


def summarize(events: list[dict]) -> None:
    by_kind: dict[str, int] = {}
    product_favs: dict[str, int] = {}
    product_views: dict[str, int] = {}
    actors: set[str] = set()

    for e in events:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
        actor = e.get("actor") or {}
        if actor.get("user_id"):
            actors.add(actor["user_id"])
        prod = e.get("product") or {}
        title = prod.get("title") or prod.get("id") or "?"
        if e.get("kind") == "product_favorite_delta":
            product_favs[title] = product_favs.get(title, 0) + (e.get("delta", 0) or 0)
        if e.get("kind") == "product_views":
            product_views[title] = product_views.get(title, 0) + (e.get("delta", 0) or 0)

    print("=== RESUMEN DE EVENTOS ===")
    for kind, count in sorted(by_kind.items()):
        print(f"  {kind}: {count}")
    print(f"\n  Actores distintos: {len(actors)}")
    if product_favs:
        print("\n  Top anuncios por favoritos (neto):")
        for title, delta in sorted(product_favs.items(), key=lambda x: -abs(x[1]))[:10]:
            print(f"    {title}: {delta:+d}")
    if product_views:
        print("\n  Top anuncios por vistas:")
        for title, delta in sorted(product_views.items(), key=lambda x: -x[1])[:10]:
            print(f"    {title}: +{delta}")


def to_csv(events: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", "kind", "source", "actor_id", "actor_name",
                                                "product_id", "product_title", "product_price",
                                                "delta", "totals"])
        writer.writeheader()
        for e in events:
            actor = e.get("actor") or {}
            prod = e.get("product") or {}
            writer.writerow({
                "ts": e.get("ts"), "kind": e.get("kind"), "source": e.get("source"),
                "actor_id": actor.get("user_id"), "actor_name": actor.get("micro_name"),
                "product_id": prod.get("id"), "product_title": prod.get("title"),
                "product_price": prod.get("price"), "delta": e.get("delta"),
                "totals": str(e.get("totals")),
            })
    print(f"CSV escrito en {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", help="export events to this CSV file")
    args = parser.parse_args()

    events = EventLog().load_all()
    print(f"{len(events)} eventos en el log.\n")
    if not events:
        return 0
    summarize(events)
    if args.csv:
        to_csv(events, Path(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
