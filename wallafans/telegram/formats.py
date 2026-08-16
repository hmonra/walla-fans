"""Rich HTML formatting for events and batches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import models as M

_ES_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
              "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

DEFAULT_ZONE = "Europe/Madrid"


def madrid_tz(zone: str = DEFAULT_ZONE):
    try:
        return ZoneInfo(zone)
    except Exception:
        # Fallback (no tzdata): fixed UTC+1/+2 approximation.
        return timezone(timedelta(hours=2))


def esc(text: str | None) -> str:
    if text is None:
        return ""
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def madrid_now() -> datetime:
    return datetime.now(madrid_tz())


def fmt_ts(iso_ts: str, zone: str = DEFAULT_ZONE) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(madrid_tz(zone))
        return f"{local.strftime('%d/%m/%Y %H:%M')}h"
    except (ValueError, TypeError):
        return iso_ts or ""


def fmt_date_es(dt: datetime) -> str:
    return f"{_ES_DAYS[dt.weekday()]} {dt.day} de {_ES_MONTHS[dt.month - 1]} de {dt.year}"


def fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    try:
        return f"{price:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(price)


def product_line(product: M.Product) -> list[str]:
    lines = [f"«{esc(product.title)}»" if product.title else f"Anuncio {product.id}"]
    lines.append(f"💰 {fmt_price(product.price)}")
    return lines


def actor_line(actor: M.Actor | None) -> str:
    if not actor:
        return ""
    parts = []
    name = actor.micro_name or (f"@{actor.web_slug}" if actor.web_slug else actor.user_id)
    parts.append(f"👤 {esc(name)}")
    if actor.country:
        parts.append(f"🌍 {esc(actor.country)}")
    if actor.rating_average is not None:
        parts.append(f"⭐ {actor.rating_average:.1f}")
    if actor.sells:
        parts.append(f"ventas {actor.sells}")
    if actor.buys:
        parts.append(f"compras {actor.buys}")
    return " · ".join(parts)


def format_event(event: M.Event) -> str | None:
    """Return an HTML message for one event, or None if it shouldn't be
    notified (e.g. silent kinds)."""
    if event.kind in (M.CONVERSATION_NEW, M.MESSAGE_NEW):
        return None

    when = f"📅 {fmt_ts(event.ts)}"

    if event.kind == M.PROFILE_FAVORITE_ADDED:
        return "\n".join([
            "⭐ <b>NUEVO FAVORITO EN TU PERFIL</b>",
            when,
            f"➕ +{abs(event.delta)} · total: <b>{event.totals.get('profile_favorites', '?')}</b>",
        ])

    if event.kind == M.PROFILE_FAVORITE_REMOVED:
        return "\n".join([
            "👎 <b>TE QUITARON UN FAVORITO DE PERFIL</b>",
            when,
            f"➖ -{abs(event.delta)} · total: <b>{event.totals.get('profile_favorites', '?')}</b>",
        ])

    if event.kind == M.REPORT_RECEIVED:
        return "\n".join([
            "🚩 <b>TE HAN HECHO UN REPORTE</b>",
            when,
            f"⚠️ total de reportes: <b>{event.totals.get('reports_received', '?')}</b>",
            "<i>Wallapop no revela quién ni el motivo.</i>",
        ])

    product = event.product
    if not product:
        return None
    header = {
        M.PRODUCT_FAVORITE_DELTA: "📦 <b>FAVORITOS EN TU ANUNCIO</b>",
        M.PRODUCT_VIEWS: "👀 <b>VISTAS EN TU ANUNCIO</b>",
        M.PRODUCT_PRICE_CHANGE: "💰 <b>CAMBIO DE PRECIO</b>",
        M.PRODUCT_STATUS_CHANGE: "🛒 <b>ESTADO DEL ANUNCIO</b>",
        M.PUBNUB_FAVORITE: "⭐ <b>FAVORITO DETECTADO</b>",
    }.get(event.kind)
    if not header:
        return None

    lines = [header, *product_line(product), when]

    if event.kind == M.PRODUCT_FAVORITE_DELTA:
        sign = "➕" if event.delta > 0 else "➖"
        lines.append(f"{sign} {abs(event.delta)} → total: <b>{event.totals.get('favorites', '?')}</b>")
        if event.totals.get("views"):
            lines.append(f"👀 Vistas: {event.totals['views']}")
    elif event.kind == M.PRODUCT_VIEWS:
        lines.append(f"➕ {event.delta} → total: <b>{event.totals.get('views', '?')}</b>")
    elif event.kind == M.PRODUCT_PRICE_CHANGE:
        lines.append(f"{fmt_price((product.price or 0) - (event.delta or 0))} → <b>{fmt_price(product.price)}</b>")
    elif event.kind == M.PRODUCT_STATUS_CHANGE:
        lines.append(f"Estado: <b>{esc(event.totals.get('status', '?'))}</b>")

    actor = actor_line(event.actor)
    if actor:
        lines.append(actor)
    if event.actor and event.actor.user_id:
        lines.append(f"🆔 {esc(event.actor.user_id)}")
    return "\n".join(lines)


def format_batch(events: list[M.Event]) -> str:
    """Compact summary used when too many events arrived in one run."""
    count_by_kind: dict[str, int] = {}
    for e in events:
        count_by_kind[e.kind] = count_by_kind.get(e.kind, 0) + 1
    lines = ["📊 <b>NOVEDADES WALLAPOP</b>", f"📅 {fmt_ts(utcnow_iso())}", ""]
    labels = {
        M.PROFILE_FAVORITE_ADDED: "Favoritos de perfil nuevos",
        M.PROFILE_FAVORITE_REMOVED: "Favoritos de perfil quitados",
        M.PRODUCT_FAVORITE_DELTA: "Cambios de favoritos en anuncios",
        M.PRODUCT_VIEWS: "Cambios de vistas",
        M.PRODUCT_PRICE_CHANGE: "Cambios de precio",
        M.PRODUCT_STATUS_CHANGE: "Cambios de estado",
        M.REPORT_RECEIVED: "Reportes recibidos",
        M.PUBNUB_FAVORITE: "Favoritos detectados",
    }
    for kind, count in sorted(count_by_kind.items()):
        label = labels.get(kind, kind)
        lines.append(f"· {label}: <b>{count}</b>")
    products = sorted({(e.product.title or e.product.id) for e in events if e.product})
    if products:
        lines.append("")
        lines.append("Anuncios implicados:")
        for title in products[:8]:
            lines.append(f"  - {esc(title)}")
    return "\n".join(lines)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()