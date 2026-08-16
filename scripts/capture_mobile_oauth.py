#!/usr/bin/env python3
"""Capture the mobile OAuth refresh token for WallaFans.

Why mobile OAuth: the web session endpoint (es.wallapop.com/api/auth/session)
is geo-blocked from GitHub Actions runners (US datacenter IP -> 403), but
api.wallapop.com and the Keycloak token endpoint ARE reachable. The mobile
clients `android` (and `ios`) accept the OAuth `refresh_token` grant, so a
refresh token obtained through the mobile authorization_code flow lets GitHub
Actions mint fresh access tokens forever (24/7, without your PC).

Two-phase flow (single Chrome window, Google via phone-code if you want):

  PHASE 1 (web, works like a normal login):
    Open https://es.wallapop.com and log in with Google. This leaves the
    Keycloak SSO session cookie in the browser.

  PHASE 2 (android client, piggybacks on the SSO session):
    Navigate to the Keycloak auth URL for client_id=android with PKCE.
    Because the SSO session already exists, Keycloak issues the code WITHOUT
    asking to log in again (no re-broker -> no post-broker-login error).
    The code lands on https://www.wallapop.com/oauth2/callback?code=...
    and the script captures it (from URL or POST body).

  Exchange code + verifier at the Keycloak token endpoint -> access +
  refresh token (client_id=android), then verifies the refresh grant.

Writes WALLAFANS_REFRESH_TOKEN / WALLAFANS_REFRESH_CLIENT_ID=android into .env
and prints a summary. Nothing is committed.

Run from the repository root:
    python scripts/capture_mobile_oauth.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wallafans.config import load_dotenv  # noqa: E402
from wallafans import endpoints as E  # noqa: E402

ENV_FILE = ROOT / ".env"

KEYCLOAK_AUTH = f"{E.ACCOUNTS}/realms/wallapop-internal/protocol/openid-connect/auth"
KEYCLOAK_TOKEN = f"{E.ACCOUNTS}/realms/wallapop-internal/protocol/openid-connect/token"
CLIENT_ID = "android"
REDIRECT_URI = "https://www.wallapop.com/oauth2/callback"
SCOPE = "openid"

WEB_SESSION_COOKIE = "__Secure-next-auth.session-token"
KEYCLOAK_SSO_COOKIE = "KEYCLOAK_IDENTITY"


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def exchange_code(code: str, verifier: str) -> dict:
    resp = requests.post(
        KEYCLOAK_TOKEN,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"token exchange failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def verify_refresh(refresh_token: str) -> dict:
    resp = requests.post(
        KEYCLOAK_TOKEN,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"refresh grant failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def extract_code_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    return q.get("code", [""])[0]


def has_cookie(ctx, name: str) -> bool:
    try:
        for c in ctx.cookies():
            if c["name"] == name:
                return True
    except Exception:
        pass
    return False


def capture_code() -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    verifier, challenge = pkce()
    android_auth_url = (
        f"{KEYCLOAK_AUTH}?client_id={CLIENT_ID}&response_type=code"
        f"&redirect_uri={REDIRECT_URI}&scope={SCOPE}"
        f"&state={secrets.token_urlsafe(8)}"
        f"&code_challenge={challenge}&code_challenge_method=S256"
    )
    code_holder: dict = {"code": ""}

    def on_request(request) -> None:
        if code_holder["code"]:
            return
        url = request.url
        if "oauth2/callback" in url:
            if "code=" in url:
                code_holder["code"] = extract_code_from_url(url)
                return
            post = request.post_data or ""
            if "code=" in post:
                code_holder["code"] = parse_qs(post).get("code", [""])[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False, channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": 1100, "height": 850},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page.on("request", on_request)

        # ---- PHASE 1: normal web login (establishes the Keycloak SSO) ----
        print("\n=== FASE 1: LOGIN WEB NORMAL ===")
        print("Se abre es.wallapop.com. Inicia sesión con Google como siempre.")
        print("(En Google: tu cuenta o 'Prueba otra forma' -> teléfono.)\n")
        page.goto(f"{E.WEB}/", wait_until="domcontentloaded", timeout=60000)

        deadline = time.time() + 360
        while time.time() < deadline:
            if has_cookie(ctx, KEYCLOAK_SSO_COOKIE) or has_cookie(ctx, WEB_SESSION_COOKIE):
                break
            time.sleep(2)
        else:
            print("FASE 1: no se detectó la sesión web en 6 min. Revisa el login.")

        if not (has_cookie(ctx, KEYCLOAK_SSO_COOKIE) or has_cookie(ctx, WEB_SESSION_COOKIE)):
            browser.close()
            raise RuntimeError("web login not completed")

        print("Sesión web detectada (SSO de Keycloak activo).")

        # ---- PHASE 2: android client, reuse the SSO session ----
        print("\n=== FASE 2: OBTENIENDO CÓDIGO PARA client_id=android ===")
        print("Navegando al login de android (debería completarse solo con tu SSO)...")
        page.goto(android_auth_url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_url("**/oauth2/callback**", timeout=120000)
        except Exception:
            pass
        deadline = time.time() + 120
        while time.time() < deadline and not code_holder["code"]:
            time.sleep(1)
        if not code_holder["code"]:
            try:
                cur = page.url
                if "oauth2/callback" in cur and "code=" in cur:
                    code_holder["code"] = extract_code_from_url(cur)
            except Exception:
                pass
            if not code_holder["code"]:
                print("DIAGNÓSTICO - URL final fase 2:", page.url[:300])

        try:
            browser.close()
        except Exception:
            pass

    if not code_holder["code"]:
        raise RuntimeError("no authorization code captured (android flow failed)")
    return code_holder["code"], verifier


def main() -> int:
    load_dotenv()
    print("Generando PKCE...")
    code, verifier = capture_code()
    print("Código de autorización capturado. Intercambiando por tokens...")
    tokens = exchange_code(code, verifier)
    refresh_token = tokens.get("refresh_token", "")
    access_token = tokens.get("access_token", "")
    if not refresh_token:
        print("NO se obtuvo refresh_token. Respuesta:", json.dumps(tokens)[:400])
        return 1
    print("Verificando el refresh grant (client_id=android)...")
    verified = verify_refresh(refresh_token)
    print("  → access_token renovado OK:", "Sí" if verified.get("access_token") else "NO")

    env_map = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_map[k.strip()] = v.strip().strip("'\"")
    env_map["WALLAFANS_REFRESH_TOKEN"] = refresh_token
    env_map["WALLAFANS_REFRESH_CLIENT_ID"] = CLIENT_ID
    with ENV_FILE.open("w", encoding="utf-8") as fh:
        for k, v in env_map.items():
            fh.write(f"{k}={v}\n")

    print("\n=== RESULTADO ===")
    print("  Refresh token (android): guardado en .env (WALLAFANS_REFRESH_TOKEN)")
    print("  client_id:               android")
    print("  Access token inicial:   ", ("OK " + access_token[:20] + "...") if access_token else "no")
    print("\nEste refresh token permite a GitHub Actions renovar tokens para siempre.")
    print("Añádelo al secret WALLAFANS_REFRESH_TOKEN de GitHub cuando quieras.")
    return 0


if __name__ == "__main__":
    sys.exit(main())