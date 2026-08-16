"""Authentication: session-cookie flow (the working strategy) + fallbacks.

Discovery (capture, 2026-08): Wallapop web logs in via Google SSO through
Keycloak; the access token is never handed to the browser as a refresh token.
The durable credential is the `__Secure-next-auth.session-token` cookie
(~30 days). Calling `GET /api/auth/session` with that cookie returns a fresh
access token:

  { "token": "<fresh JWT, 5 min>", "idToken": "...", "expires": "..." }

Strategies, in order:
  1. Session cookie -> /api/auth/session            [preferred, works on GH]
  2. Refresh token -> Keycloak token endpoint       [legacy fallback]
  3. Email + password -> Playwright headless login   [last resort]
"""
from __future__ import annotations

import base64
import json
import logging
import random
import time

import requests

from . import endpoints as E
from .config import settings

log = logging.getLogger("wallafans.auth")

SESSION_COOKIE_NAME = "__Secure-next-auth.session-token"
SESSION_URL = f"{E.WEB}/api/auth/session"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def decode_jwt(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


def is_token_expired(token: str) -> bool:
    exp = decode_jwt(token).get("exp", 0)
    return bool(exp) and int(time.time()) >= int(exp) - 30


def get_user_id_from_token(token: str) -> str:
    return decode_jwt(token).get("hashed_sub", "")


class TokenError(Exception):
    pass


def get_token_from_session_cookie(session_cookie: str) -> str:
    """Exchange the long-lived session cookie for a fresh access token."""
    resp = requests.get(
        SESSION_URL,
        headers={
            "Cookie": f"{SESSION_COOKIE_NAME}={session_cookie}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise TokenError(f"session endpoint returned {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TokenError("session endpoint returned non-JSON") from exc
    token = payload.get("token", "")
    if not token:
        raise TokenError("session endpoint returned no access token (session expired?)")
    return token


def refresh_via_keycloak(refresh_token: str, client_id: str = "web") -> str:
    """Legacy fallback: exchange a Keycloak refresh token for an access token."""
    resp = requests.post(
        E.KEYCLOAK_TOKEN,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"User-Agent": "WallaFans/0.1", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise TokenError(f"refresh grant failed ({resp.status_code}): {resp.text[:200]}")
    token = resp.json().get("access_token", "")
    if not token:
        raise TokenError("refresh grant returned no access_token")
    return token


def login_playwright(email: str, password: str) -> str:
    """Headless login via Playwright (slow; used only as a fallback)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise TokenError("playwright not installed")

    result: dict = {"bearer": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(user_agent=random.choice([UA]))
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        def on_response(response):
            auth = response.request.headers.get("authorization", "")
            if auth.startswith("Bearer ") and not result["bearer"]:
                result["bearer"] = auth[7:]

        page.on("response", on_response)
        page.goto(f"{E.WEB}/auth/signin", wait_until="domcontentloaded")
        page.fill('input[type="email"], input[name="email"]', email)
        page.fill('input[type="password"], input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=45000)
        browser.close()

    if not result["bearer"]:
        raise TokenError("Playwright login produced no bearer token")
    return result["bearer"]


def get_access_token() -> str:
    """Return a fresh, valid access token using the first working strategy."""
    if settings.refresh_token:
        try:
            token = refresh_via_keycloak(settings.refresh_token, settings.refresh_client_id)
            log.info("access token refreshed via Keycloak")
            return token
        except TokenError as exc:
            log.warning("refresh via Keycloak failed: %s", exc)
    if settings.session_cookie:
        try:
            token = get_token_from_session_cookie(settings.session_cookie)
            log.info("access token refreshed via session cookie")
            return token
        except TokenError as exc:
            log.warning("session cookie refresh failed: %s", exc)
    if settings.email and settings.password:
        token = login_playwright(settings.email, settings.password)
        log.info("access token obtained via Playwright login")
        return token
    raise TokenError(
        "no working auth strategy. Capture a session cookie with "
        "scripts/capture_one_time.py or set WALLAFANS_EMAIL/PASSWORD."
    )