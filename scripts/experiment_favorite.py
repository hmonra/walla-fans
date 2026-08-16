#!/usr/bin/env python3
"""Experiment: what does the web API reveal about favorites?

Before/after snapshot comparison for the "2nd account" test.

  Phase 0 (before):  partner has NOT favorited anything yet.
  Phase 1 (profile): partner favorites YOUR PROFILE.
  Phase 2 (item):    partner favorites one of YOUR ITEMS.

Run `python scripts/experiment_favorite.py before` then, after each step,
`... after`. The script dumps a snapshot (stats full body, profile fields,
item counters) and, with `after`, prints a diff vs the previous snapshot.

Expected (per validation): profile favorite -> NO visible change;
item favorite -> counters.favorites of that item +1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallafans.auth import get_access_token  # noqa: E402
from wallafans.config import settings  # noqa: E402
from wallafans.client import USER_AGENTS  # noqa: E402
from wallafans import endpoints as E  # noqa: E402

import random  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "state" / "experiment"
OUT.mkdir(parents=True, exist_ok=True)


def get(path: str) -> tuple[int, object]:
    token = get_access_token()
    resp = requests.get(
        f"{E.API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": random.choice(USER_AGENTS),
            "X-DeviceOS": "0",
        },
        timeout=20,
    )
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:500]
    return resp.status_code, body


def snapshot() -> dict:
    uid = settings.user_id
    data: dict = {}
    for label, path in [
        ("stats", f"/api/v3/users/{uid}/stats"),
        ("me", "/api/v3/users/me"),
        ("profile", f"/api/v3/users/{uid}"),
        ("items", f"/api/v3/users/{uid}/items"),
        ("type", f"/api/v3/user/{uid}/type"),
        ("unread", "/api/v3/instant-messaging/messages/unread"),
    ]:
        status, body = get(path)
        data[label] = {"status": status, "body": body}
    # Item detail counters (views/favorites) — list endpoint has no counters.
    details: dict[str, dict] = {}
    raw_items = data["items"]["body"] if isinstance(data["items"]["body"], dict) else {}
    for it in raw_items.get("data") or []:
        item_id = str(it.get("id", ""))
        if not item_id:
            continue
        status, body = get(f"/api/v3/items/{item_id}")
        counters = {}
        fav_flag = None
        if isinstance(body, dict):
            counters = body.get("counters") or {}
            if "favorited" in body:
                fav_flag = body["favorited"]
        details[item_id] = {"status": status, "counters": counters, "favorited": fav_flag}
    data["item_details"] = details
    return data


def summarize(snap: dict) -> dict:
    out: dict = {}
    for label, entry in snap.items():
        if label == "item_details":
            details = {}
            for item_id, det in entry.items():
                details[item_id] = det.get("counters", {})
            out[label] = details
            continue
        body = entry["body"]
        if not isinstance(body, dict):
            out[label] = body
            continue
        if label == "stats":
            counters = {}
            for c in body.get("counters") or []:
                if isinstance(c, dict) and "type" in c:
                    counters[c["type"]] = c.get("value")
            out["stats"] = counters
        elif label == "items":
            items = {}
            for it in body.get("data") or []:
                items[str(it.get("id"))] = it.get("title")
            out["items_count"] = len(items)
            out["items"] = items
        elif label in ("me", "profile"):
            keep = {}
            for k, v in body.items():
                if isinstance(v, (dict, list)):
                    keep[k] = v
                else:
                    keep[k] = v
            out[label] = keep
        else:
            out[label] = body
    return out


def flatten(prefix: str, obj) -> dict:
    """Flatten nested dict/list into dotted keys (only scalar leaves)."""
    flat: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(flatten(f"{prefix}.{k}" if prefix else k, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat.update(flatten(f"{prefix}[{i}]", v))
    else:
        flat[prefix] = obj
    return flat


def diff(old: dict, new: dict) -> list[str]:
    old_flat, new_flat = flatten("", old), flatten("", new)
    keys = set(old_flat) | set(new_flat)
    changes = []
    for k in sorted(keys):
        if old_flat.get(k) != new_flat.get(k):
            changes.append(f"  {k}: {old_flat.get(k)!r} -> {new_flat.get(k)!r}")
    return changes


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "before"
    if phase not in ("before", "after"):
        print("usage: python scripts/experiment_favorite.py [before|after]")
        return 1

    snap = snapshot()
    summary = summarize(snap)

    if phase == "before":
        (OUT / "before.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print("SNAPSHOT BEFORE GUARDADO (state/experiment/before.json)")
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        print("\nAHORA: pide a tu pareja que dé ⭐ a tu PERFIL y avísame.")
        return 0

    prev = json.loads((OUT / "before.json").read_text(encoding="utf-8"))
    (OUT / "after.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("SNAPSHOT AFTER GUARDADO (state/experiment/after.json)")
    print("\n=== DIFF ANTES -> DESPUÉS ===")
    changes = diff(summarize(prev), summary)
    if not changes:
        print("  (sin cambios detectables)")
    for line in changes:
        print(line)

    # Highlight the item-favorite control: item counters (needs detail fetch)
    print("\n=== CONTADORES DE ANUNCIOS (detalle) ===")
    for it in summary.get("items", {}):
        print(f"  {it}: {summary['items'][it]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())