"""Daily digests: short evening summary + full morning summary."""
from __future__ import annotations

from collections import Counter

from .. import models as M
from .formats import fmt_date_es, fmt_price, fmt_ts, madrid_now


def _aggregate(events: list[M.Event]) -> dict:
    agg = {
        "profile_added": 0, "profile_removed": 0,
        "reports": 0, "messages": 0, "conversations": 0,
        "product_favs": Counter(),      # product title -> net fav delta
        "views": Counter(),             # product title -> views delta
        "price_changes": [],            # product titles
        "status_changes": [],           # (title, status)
        "actors": {},                   # actor id -> actor
    }
    for e in events:
        if e.kind == M.PROFILE_FAVORITE_ADDED:
            agg["profile_added"] += abs(e.delta)
        elif e.kind == M.PROFILE_FAVORITE_REMOVED:
            agg["profile_removed"] += abs(e.delta)
        elif e.kind == M.REPORT_RECEIVED:
            agg["reports"] += abs(e.delta)
        elif e.kind == M.CONVERSATION_NEW:
            agg["conversations"] += 1
        elif e.kind == M.MESSAGE_NEW:
            agg["messages"] += 1
        elif e.kind == M.PRODUCT_FAVORITE_DELTA and e.product:
            title = e.product.title or e.product.id
            agg["product_favs"][title] += e.delta
        elif e.kind == M.PRODUCT_VIEWS and e.product:
            title = e.product.title or e.product.id
            agg["views"][title] += e.delta
        elif e.kind == M.PRODUCT_PRICE_CHANGE and e.product:
            agg["price_changes"].append(e.product.title or e.product.id)
        elif e.kind == M.PRODUCT_STATUS_CHANGE and e.product:
            agg["status_changes"].append((e.product.title or e.product.id,
                                          str(e.totals.get("status", ""))))
        if e.actor and e.actor.user_id:
            agg["actors"][e.actor.user_id] = e.actor
    return agg


def build_evening_digest(events: list[M.Event], totals: dict) -> str:
    """Short evening summary of the current day (sent at 21:00 Madrid)."""
    agg = _aggregate(events)
    now = madrid_now()
    lines = [
        "🌙 <b>RESUMEN WALLAPOP — HOY</b>",
        f"📅 {fmt_date_es(now)}",
        "──────────────",
    ]
    lines.append(f"⭐ Perfil: <b>+{agg['profile_added']}</b> favoritos · "
                 f"<b>-{agg['profile_removed']}</b> quitados "
                 f"(total {totals.get('profile_favorites', '?')})")
    if agg["reports"]:
        lines.append(f"🚩 Reportes: <b>{agg['reports']}</b>")
    if agg["product_favs"]:
        lines.append(f"📦 Favoritos en anuncios: <b>{sum(abs(v) for v in agg['product_favs'].values())}</b>")
        top = agg["product_favs"].most_common(5)
        for title, delta in top:
            sign = "+" if delta > 0 else ""
            lines.append(f"   · {title}: {sign}{delta}")
    if agg["conversations"]:
        lines.append(f"💬 Nuevos contactos: <b>{agg['conversations']}</b>")
    if agg["views"]:
        lines.append(f"👀 Vistas acumuladas: <b>{sum(agg['views'].values())}</b>")
    lines.append("──────────────")
    lines.append("Más detalles mañana a las 08:00 😉")
    return "\n".join(lines)


def build_morning_digest(events: list[M.Event], totals: dict, day_label: str) -> str:
    """Full summary of yesterday, sent at 08:00 Madrid."""
    agg = _aggregate(events)
    lines = [
        "🌅 <b>RESUMEN INTERACCIONES WALLAPOP DE AYER</b>",
        f"📅 {day_label}",
        "══════════════",
    ]
    lines.append(f"⭐ <b>PERFIL</b>")
    lines.append(f"   +{agg['profile_added']} favoritos nuevos")
    lines.append(f"   -{agg['profile_removed']} favoritos quitados")
    lines.append(f"   total actual: {totals.get('profile_favorites', '?')}")
    lines.append("")
    lines.append("📦 <b>ANUNCIOS CON FAVORITOS</b>")
    top_favs = agg["product_favs"].most_common(8)
    if top_favs:
        for title, delta in top_favs:
            sign = "+" if delta > 0 else ""
            lines.append(f"   · {title}: {sign}{delta}")
    else:
        lines.append("   (ninguno)")
    lines.append("")
    lines.append("👀 <b>VISTAS</b>")
    top_views = agg["views"].most_common(5)
    if top_views:
        for title, delta in top_views:
            lines.append(f"   · {title}: +{delta}")
    else:
        lines.append("   (sin datos)")
    lines.append("")
    lines.append("💰 <b>PRECIOS / ESTADO</b>")
    if agg["price_changes"]:
        lines.append(f"   Precios cambiados: {len(agg['price_changes'])}")
        for t in agg["price_changes"][:5]:
            lines.append(f"   · {t}")
    else:
        lines.append("   Precios: sin cambios")
    if agg["status_changes"]:
        for title, status in agg["status_changes"][:5]:
            lines.append(f"   🛒 {title} → {status}")
    lines.append("")
    lines.append("🚩 <b>REPORTES</b>")
    lines.append(f"   {agg['reports']} reporte(s) recibido(s)")
    lines.append("")
    lines.append("💬 <b>CONTACTOS</b>")
    if agg["actors"]:
        for actor in list(agg["actors"].values())[:8]:
            name = actor.micro_name or f"@{actor.web_slug}" if actor.web_slug else (actor.micro_name or actor.user_id)
            rating = f" · ⭐{actor.rating_average:.1f}" if actor.rating_average else ""
            sells = f" · {actor.sells} ventas" if actor.sells else ""
            lines.append(f"   · {name}{rating}{sells}")
    else:
        lines.append("   (sin nuevos contactos)")
    lines.append("══════════════")
    lines.append("Datos guardados para tu estudio de negocio 📈")
    return "\n".join(lines)