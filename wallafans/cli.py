"""Command-line interface.

Subcommands:
  poll     — one polling cycle: refresh token, diff, PubNub history, Telegram, commit-ready state
  digest   — send the evening (21:00) and/or morning (08:00) digest (DST-aware)
  test     — verify auth + Telegram wiring
  state    — print the current saved state
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import load_dotenv, settings
from .auth import TokenError, get_access_token
from .client import WallapopAPI
from .enrich import enrich_actor
from .models import (
    CONVERSATION_NEW,
    MESSAGE_NEW,
    PRODUCT_VIEWS,
    Event,
)
from .poller import Poller
from .pubnub_history import parse_favorite_events, poll_history
from .store import EventLog, StateStore
from .telegram.bot import TelegramBot
from .telegram.digest import build_evening_digest, build_morning_digest
from .telegram.formats import format_batch, format_event, fmt_date_es, madrid_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("wallafans")

VIEW_ALERT_THRESHOLD = 20  # only alert on view deltas >= this

# Kinds that are logged but never notified (per user requirements)
SILENT_KINDS = {CONVERSATION_NEW, MESSAGE_NEW}


def notifyable(event: Event) -> bool:
    if event.kind in SILENT_KINDS:
        return False
    if event.kind == PRODUCT_VIEWS and event.delta < VIEW_ALERT_THRESHOLD:
        return False
    return True


def _send_events(bot: TelegramBot, events: list[Event], api: WallapopAPI, state: dict) -> None:
    to_send = [e for e in events if notifyable(e)]

    # enrich actors with public profile data (best effort)
    for event in to_send:
        if event.actor and event.actor.user_id:
            try:
                event.actor = enrich_actor(api, event.actor)
            except Exception as exc:  # keep going even if enrichment fails
                log.warning("enrichment failed for %s: %s", event.actor.user_id, exc)

    if not to_send:
        log.info("no notifyable events")
        return

    limit = settings.max_messages_per_run
    if len(to_send) > limit:
        log.info("%d events -> sending one batch message", len(to_send))
        bot.send_message(format_batch(to_send))
        return

    for event in to_send:
        text = format_event(event)
        if not text:
            continue
        buttons = []
        if event.product and event.product.url:
            buttons.append(("Abrir anuncio", event.product.url))
        if event.actor and (event.actor.profile_url or event.actor.user_id):
            url = event.actor.profile_url or f"https://es.wallapop.com/user/{event.actor.user_id}"
            buttons.append(("Abrir perfil", url))
        markup = bot.buttons(*buttons) if buttons else None

        if event.product and event.product.image_url:
            ok = bot.send_photo(event.product.image_url, text, markup)
        else:
            ok = bot.send_message(text, reply_markup=markup)
        log.info("telegram event '%s' -> %s", event.kind, "ok" if ok else "FAILED")


def _cmd_poll(args) -> int:
    if not settings.has_any_wallapop_auth:
        log.error("No auth configured. Capture a refresh token first (see README).")
        return 1

    try:
        token = get_access_token()
    except TokenError as exc:
        log.error("Auth failed: %s", exc)
        return 1

    api = WallapopAPI(bearer_token=token)
    store = StateStore()
    event_log = EventLog()

    # 1) polling diff
    poller = Poller(api, store)
    poll_events, state = poller.run()
    log.info("poll produced %d events", len(poll_events))

    # 2) PubNub history (identity, if available)
    pn_events, new_tt = poll_history()
    if pn_events:
        log.info("PubNub history produced %d events", len(pn_events))
        state["pubnub_last_timetoken"] = new_tt
    poll_events.extend(pn_events)

    # 3) Telegram
    if settings.telegram_bot_token and settings.telegram_chat_id:
        bot = TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id)
        _send_events(bot, poll_events, api, state)
    else:
        log.info("Telegram not configured; skipping notifications")

    # 4) persist everything
    for event in poll_events:
        event_log.append(event.to_dict())
    event_log.trim(5000)
    store.save(state)

    log.info("poll cycle done. saved state + %d events", len(poll_events))
    return 0


def _digest_state_key(kind: str) -> str:
    return f"last_{kind}_digest_date"


def _cmd_digest(args) -> int:
    now = madrid_now()
    state = StateStore().load()
    event_log = EventLog()
    totals = {
        "profile_favorites": state.get("counters", {}).get("profileFavoritedReceived", 0),
        "reports_received": state.get("counters", {}).get("reports_received", 0),
    }
    bot = TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id)

    wants_evening = args.when in ("evening", "auto")
    wants_morning = args.when in ("morning", "auto")

    sent = 0

    if wants_evening and now.hour == settings.digest_evening_hour:
        if state.get(_digest_state_key("evening")) != now.strftime("%Y-%m-%d"):
            today = now.strftime("%Y-%m-%d")
            events = [e for e in event_log.since(f"{today}T00:00:00")]
            ev_objs = [e for e in (Event.from_dict(x) for x in events) if e]
            text = build_evening_digest(ev_objs, totals)
            if bot.send_message(text):
                state[_digest_state_key("evening")] = now.strftime("%Y-%m-%d")
                StateStore().save(state)
                sent += 1
                log.info("evening digest sent")
    elif wants_evening:
        log.info("not evening digest time (now %dh, want %dh)", now.hour, settings.digest_evening_hour)

    if wants_morning and now.hour == settings.digest_morning_hour:
        if state.get(_digest_state_key("morning")) != now.strftime("%Y-%m-%d"):
            yesterday = now - timedelta(days=1)
            events = [e for e in event_log.load_all()
                      if e.get("ts", "").startswith(yesterday.strftime("%Y-%m-%d"))]
            ev_objs = [Event.from_dict(e) for e in events]
            ev_objs = [e for e in ev_objs if e]
            text = build_morning_digest(ev_objs, totals, fmt_date_es(yesterday))
            if bot.send_message(text):
                state[_digest_state_key("morning")] = now.strftime("%Y-%m-%d")
                StateStore().save(state)
                sent += 1
                log.info("morning digest sent")
    elif wants_morning:
        log.info("not morning digest time (now %dh, want %dh)", now.hour, settings.digest_morning_hour)

    return 0 if sent or (wants_evening or wants_morning) else 0


def _cmd_test(args) -> int:
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set")
        return 1
    bot = TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id)
    me = bot.get_me()
    if not me:
        log.error("Invalid bot token")
        return 1
    log.info("Bot OK: @%s (%s)", me.get("username"), me.get("first_name"))

    if settings.has_any_wallapop_auth:
        try:
            token = get_access_token()
            api = WallapopAPI(bearer_token=token)
            profile = api.get_me()
            log.info("Wallapop auth OK -> id=%s name=%s",
                     profile.get("id"), profile.get("micro_name"))
            stats = api.get_stats(settings.user_id)
            log.info("Stats OK -> counters: %s",
                     {c["type"]: c["value"] for c in stats.get("counters", [])})
        except Exception as exc:
            log.error("Wallapop auth failed: %s", exc)
            return 1
    else:
        log.warning("No Wallapop auth configured; skipped API check")

    if args.send:
        ok = bot.send_message("<b>✅ WallaFans</b> — prueba OK")
        log.info("Test message sent: %s", ok)
    return 0


def _cmd_state(args) -> int:
    state = StateStore().load()
    print(state)
    return 0


def _cmd_watchdog(args) -> int:
    """Alert by Telegram if the poll has been silent too long."""
    state = StateStore().load()
    last_poll = state.get("last_poll", "")
    age_minutes = 9999
    if last_poll:
        try:
            dt = datetime.fromisoformat(last_poll)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_minutes = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
        except ValueError:
            pass

    max_age = int(getattr(args, "max_age", 90) or 90)
    log.info("last poll: %s (%d min ago)", last_poll or "never", age_minutes)
    if age_minutes <= max_age:
        return 0

    if settings.telegram_bot_token and settings.telegram_chat_id:
        bot = TelegramBot(settings.telegram_bot_token, settings.telegram_chat_id)
        text = (
            "🚨 <b>ALERTA WALLAFANS</b>\n"
            f"El último poll fue hace <b>{age_minutes} min</b>.\n"
            "Probablemente el refresh token caducó o la API falla.\n"
            "Revisa los secrets / renueva el token."
        )
        if bot.send_message(text):
            log.info("watchdog alert sent")
    return 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="wallafans", description="WallaFans — Wallapop interaction tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("poll", help="run one polling cycle")
    p_poll.set_defaults(func=_cmd_poll)

    p_digest = sub.add_parser("digest", help="send daily digest")
    p_digest.add_argument("--when", choices=["auto", "evening", "morning"], default="auto")
    p_digest.set_defaults(func=_cmd_digest)

    p_test = sub.add_parser("test", help="verify auth + Telegram")
    p_test.add_argument("--send", action="store_true", help="also send a test Telegram message")
    p_test.set_defaults(func=_cmd_test)

    p_state = sub.add_parser("state", help="print saved state")
    p_state.set_defaults(func=_cmd_state)

    p_watchdog = sub.add_parser("watchdog", help="alert if polling is stale")
    p_watchdog.add_argument("--max-age", type=int, default=90, help="max minutes without a poll")
    p_watchdog.set_defaults(func=_cmd_watchdog)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())