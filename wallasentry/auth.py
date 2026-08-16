"""Authentication: refresh-token flow with Keycloak fallbacks.

Web access tokens last ~5 minutes. For 24/7 operation we need a way to obtain
fresh tokens on every GitHub Actions run:

  1. Refresh token (captured once) -> Keycloak token endpoint  [preferred]
  2. Session cookie (captured once) -> discovered refresh endpoint
  3. Email + password -> Playwright headless login                [fallback]
"""
from __future__ import annotations

import base64
import json
import logging
import time

import requests

from . import endpoints as E
from .config import settings

log = logging.getLogger("wallasentry.auth")


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


def refresh_via_keycloak(refresh_token: str, client_id: str = "web") -> str:
    """Exchange a Keycloak refresh token for a fresh access token."""
    resp = requests.post(
        E.KEYCLOAK_TOKEN,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"User-Agent": "WallaSentry/0.1", "Content-Type": "application/x-www-form-urlencoded"},
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
        ctx = browser.new_context(user_agent=__import__("random").choice(
            ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"]
        ))
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
    """Return a fresh, valid access token using the configured strategy."""
    if settings.refresh_token:
        try:
            token = refresh_via_keycloak(settings.refresh_token, settings.refresh_client_id)
            log.info("access token refreshed via Keycloak")
            return token
        except TokenError as exc:
            log.warning("refresh via Keycloak failed: %s", exc)
    if settings.email and settings.password:
        token = login_playwright(settings.email, settings.password)
        log.info("access token obtained via Playwright login")
        return token
    raise TokenError(
        "no working auth strategy. Capture a refresh token with "
        "scripts/capture_one_time.py or set WALLAFANS_EMAIL/PASSWORD."
    )
