#!/usr/bin/env python3
"""One-time capture tool for Walla Sentry.

Opens a real Chrome window, you log in to Wallapop once, and this script
intercepts the traffic to harvest:

  * the Keycloak refresh token (if the app issues one)  -> WALLAFANS_REFRESH_TOKEN
  * the PubNub PAM auth token                           -> PUBNUB_AUTH_TOKEN
  * the PubNub channel name(s)                          -> PUBNUB_CHANNEL
  * which API endpoints actually return data            -> validates the poller
  * the X-Signature secret from the JS bundle           -> optional

Everything is written to `secrets_local.json` (git-ignored) and appended to
your local `.env`. Nothing is ever committed.

Run from the repository root:
    python scripts/capture_one_time.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wallasentry.config import load_dotenv, settings  # noqa: E402
from wallasentry import endpoints as E  # noqa: E402

SECRETS_FILE = ROOT / "secrets_local.json"
ENV_FILE = ROOT / ".env"

HOST_INTEREST = ("api.wallapop.com", "es.wallapop.com", "accounts.wallapop.com", "pndsn.com", "wallapop.com")


def looks_like_pubnub_auth(value: str) -> bool:
    """PAM v3 tokens are long base64-ish strings (several hundred chars)."""
    return len(value) > 100 and re.fullmatch(r"[A-Za-z0-9\-_=]+", value or "") is not None


class CaptureSession:
    def __init__(self) -> None:
        self.results: dict = {
            "bearer": "",
            "refresh_token": "",
            "session_cookie": "",
            "pubnub_auth": "",
            "pubnub_channels": [],
            "pubnub_messages": [],
            "api_calls": [],      # [{method, url, status, body_preview}]
            "token_responses": [],  # keycloak token endpoint responses
            "cookies": {},
            "xsig_secret": "",
        }

    # ── helpers ─────────────────────────────────────────────────────
    def on_request(self, request) -> None:
        url = request.url
        if "pndsn.com" in url and "auth=" in url:
            match = re.search(r"[?&]auth=([^&]+)", url)
            if match:
                candidate = match.group(1)
                if not self.results["pubnub_auth"] and looks_like_pubnub_auth(candidate):
                    self.results["pubnub_auth"] = candidate
        # channel names from subscribe URLs
        m = re.search(r"/v2/subscribe/[^/]+/([^?]+)/0", url)
        if m:
            for ch in m.group(1).split(","):
                if ch and ch not in self.results["pubnub_channels"]:
                    self.results["pubnub_channels"].append(ch)

    def on_response(self, response) -> None:
        url = response.url
        method = response.request.method
        status = response.status

        auth = response.request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and not self.results["bearer"]:
            self.results["bearer"] = auth[7:]

        # Keycloak token endpoint -> refresh token
        if "/protocol/openid-connect/token" in url:
            try:
                data = response.json()
            except Exception:
                data = None
            if isinstance(data, dict):
                self.results["token_responses"].append({"url": url, "status": status, "body": data})
                if data.get("refresh_token") and not self.results["refresh_token"]:
                    self.results["refresh_token"] = data["refresh_token"]
                if data.get("access_token") and not self.results["bearer"]:
                    self.results["bearer"] = data["access_token"]

        # Capture API calls for endpoint validation
        if any(h in url for h in HOST_INTEREST) and "pndsn.com" not in url:
            preview = ""
            try:
                body = response.json()
                preview = json.dumps(body, ensure_ascii=False)[:300]
            except Exception:
                try:
                    preview = response.text[:300]
                except Exception:
                    pass
            self.results["api_calls"].append({
                "method": method, "url": url, "status": status, "body_preview": preview,
            })

    def on_websocket(self, ws) -> None:
        try:
            if "pndsn" in ws.url:
                ch = None
                m = re.search(r"/channel/([^/]+)", ws.url)
                if m:
                    ch = m.group(1)
                self.results["pubnub_channels"].append(ch) if ch and ch not in self.results["pubnub_channels"] else None
        except Exception:
            pass

    def scan_storage(self, page) -> None:
        for key in ("accessToken", "token", "bearer", "session", "auth", "pubnub"):
            try:
                val = page.evaluate(f"localStorage.getItem('{key}')")
                if val and len(val) > 50:
                    if "Bearer" not in self.results["bearer"] and not self.results["bearer"]:
                        self.results["bearer"] = val
            except Exception:
                pass

    def run(self) -> dict:
        from playwright.sync_api import sync_playwright

        email = settings.email
        password = settings.password

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 850},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            )
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )

            page.on("request", self.on_request)
            page.on("response", self.on_response)
            page.on("websocket", self.on_websocket)

            print("\n=== CAPTURE DE SESIÓN WALLAPOP ===")
            print("Se abrirá Chrome. Inicia sesión en Wallapop (login manual).")
            print("La captura corre en segundo plano; no hace falta que hagas nada más.\n")
            page.goto(f"{E.WEB}/", wait_until="domcontentloaded", timeout=60000)

            if email and password:
                try:
                    page.goto(f"{E.WEB}/auth/signin", wait_until="domcontentloaded", timeout=30000)
                    page.fill('input[type="email"], input[name="email"]', email)
                    page.fill('input[type="password"], input[name="password"]', password)
                    page.click('button[type="submit"]')
                    print("Credenciales de .env usadas; esperando login automático...")
                except Exception as exc:
                    print(f"Auto-login fallido ({exc}); usa el login manual.")

            deadline = time.time() + 240
            while time.time() < deadline and not self.results["bearer"]:
                time.sleep(2)
            if not self.results["bearer"]:
                print("\nNo se capturó un token de acceso. Comprueba que el login se completó.")

            time.sleep(5)  # let the app boot realtime/pubnub

            for c in ctx.cookies():
                self.results["cookies"][c["name"]] = c["value"]
            self.results["session_cookie"] = self.results["cookies"].get("__Secure-next-auth.session-token", "")
            self.scan_storage(page)
            browser.close()

        # X-Signature secret from the JS bundle (best effort)
        self.results["xsig_secret"] = self._extract_xsig()
        return self.results

    @staticmethod
    def _extract_xsig() -> str:
        import requests as rq
        try:
            html = rq.get(E.WEB, timeout=20).text
            m = re.search(r'src="([^"]+main[^"]+\.js)"', html)
            if m:
                js = rq.get(E.WEB + m.group(1), timeout=30).text
                for cand in re.findall(r"[A-Za-z0-9+/=]{50,}", js):
                    return cand
        except Exception:
            pass
        return ""


def write_outputs(results: dict) -> None:
    SECRETS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    env_lines = []
    if ENV_FILE.exists():
        env_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    env_map = {}
    for line in env_lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env_map[k.strip()] = v.strip().strip("'\"")
    if results.get("refresh_token"):
        env_map["WALLAFANS_REFRESH_TOKEN"] = results["refresh_token"]
    if results.get("session_cookie"):
        env_map["WALLAFANS_SESSION_COOKIE"] = results["session_cookie"]
    if results.get("pubnub_auth"):
        env_map["PUBNUB_AUTH_TOKEN"] = results["pubnub_auth"]
    if results.get("pubnub_channels"):
        env_map["PUBNUB_CHANNEL"] = results["pubnub_channels"][0]
    with ENV_FILE.open("w", encoding="utf-8") as fh:
        for k, v in env_map.items():
            fh.write(f"{k}={v}\n")

    print("\n=== RESULTADO DE LA CAPTURA ===")
    print(f"  Bearer token:          {'OK (' + results['bearer'][:25] + '...)' if results['bearer'] else 'NO capturado'}")
    print(f"  Refresh token:         {'OK' if results['refresh_token'] else 'NO disponible'}")
    print(f"  Session cookie:        {'OK' if results['session_cookie'] else 'NO disponible'}")
    print(f"  PubNub PAM token:      {'OK' if results['pubnub_auth'] else 'NO capturado'}")
    print(f"  PubNub canales:        {results['pubnub_channels'] or 'NO detectados'}")
    print(f"  X-Signature secret:    {'OK' if results['xsig_secret'] else 'NO detectado'}")
    print(f"  API calls capturadas:  {len(results['api_calls'])}")
    print(f"  Respuestas token:      {len(results['token_responses'])}")
    print("\nGuardado en secrets_local.json (NO se sube a git) y .env local.")


def main() -> int:
    load_dotenv()
    capture = CaptureSession()
    results = capture.run()
    write_outputs(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
