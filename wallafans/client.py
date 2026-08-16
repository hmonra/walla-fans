"""Wallapop API client.

Uses plain `requests`. The optional X-Signature header is only enabled when a
signing secret is provided (extracted at capture time); most authenticated
endpoints work with the Bearer token alone.
"""
from __future__ import annotations

import base64
import codecs
import hashlib
import hmac
import random
import re
import time
from typing import Any

import requests

from . import endpoints as E


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Public signing secret, extracted from the web JS bundle during capture.
# Optional: endpoints used by the tracker do not require it.
XSIG_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")


def extract_xsig_secret(js_text: str) -> str:
    """Best-effort extraction of the X-Signature secret from a JS bundle."""
    for match in XSIG_RE.findall(js_text or ""):
        if len(match) >= 60:
            return match
    return ""


class WallapopError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class WallapopAPI:
    def __init__(
        self,
        bearer_token: str = "",
        xsig_secret: str = "",
        timeout: int = 20,
        max_retries: int = 3,
    ) -> None:
        self.bearer_token = bearer_token
        self.xsig_secret = xsig_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    # ── headers / signing ───────────────────────────────────────────
    def _base_headers(self, auth: bool, signed: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Origin": E.WEB,
            "Referer": f"{E.WEB}/",
            "User-Agent": random.choice(USER_AGENTS),
            "X-DeviceOS": "0",
        }
        if auth and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if signed and self.xsig_secret:
            ts = str(int(time.time()))
            headers["X-Timestamp"] = ts
            headers["X-Signature"] = self._signature(auth, ts)
        return headers

    def _signature(self, auth: bool, ts: str) -> str:
        method = "POST" if not auth else "GET"
        # The signature covers a canonical endpoint path; keep it permissive.
        payload = f"{method}|/api/v3/|{ts}|".encode()
        sig = hmac.new(base64.b64decode(self.xsig_secret), payload, hashlib.sha256).digest()
        return str(codecs.encode(sig, "base64").decode()).strip()

    # ── request core ────────────────────────────────────────────────
    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        auth: bool = True,
        signed: bool = False,
    ) -> tuple[int, Any]:
        url = path if path.startswith("http") else f"{E.API}{path}"
        headers = self._base_headers(auth=auth, signed=signed)
        json_body = data if isinstance(data, (dict, list)) else None

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=headers, timeout=self.timeout,
                )
                if resp.status_code == 429 and attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code >= 500 and attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
                return resp.status_code, body
            except requests.RequestException as exc:  # network error
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
        raise WallapopError(f"request failed: {last_exc}")

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, data: dict | None = None, **kw):
        return self.request("POST", path, data=data, **kw)

    # ── endpoints used by the tracker ───────────────────────────────
    def get_stats(self, user_id: str, auth: bool = True) -> dict:
        status, body = self.get(E.STATS.format(user_id=user_id), auth=auth)
        return body if isinstance(body, dict) else {}

    def get_public_profile(self, user_id: str) -> dict:
        status, body = self.get(E.PUBLIC_PROFILE.format(user_id=user_id), auth=False)
        return body if isinstance(body, dict) else {}

    def get_me(self) -> dict:
        status, body = self.get(E.ME, auth=True)
        return body if isinstance(body, dict) else {}

    def list_my_items(self, user_id: str) -> list[dict]:
        """Return the logged user's items as a list of dicts.

        Tries several shapes (search GET, search POST, plain user items) and
        unwraps whatever the API currently returns. The capture tool validates
        which candidate works with the real session.
        """
        attempts: list[list[dict]] = []
        # 1) search GET with user filter
        for params in (
            {"user_ids": user_id},
            {"user_ids": user_id, "order_by": "creation_date", "order_by_desc": "true"},
            {"user_id": user_id},
        ):
            status, body = self.get(E.MY_ITEMS_CANDIDATES[0], params=params, auth=True)
            attempts.append(self._unwrap_items(body))
        # 2) search POST (API-style)
        for payload in (
            {"filters": {"user_ids": [user_id]}, "keywords": "", "order_by": "creation_date", "order_by_desc": "true"},
            {"filters": {"user_ids": [user_id]}},
            {"user_ids": [user_id]},
        ):
            status, body = self.post(E.MY_ITEMS_CANDIDATES[0], data=payload, auth=True)
            attempts.append(self._unwrap_items(body))
        # 3) plain user item paths
        for path in E.MY_ITEMS_CANDIDATES[1:]:
            status, body = self.get(path.format(user_id=user_id), auth=True)
            attempts.append(self._unwrap_items(body))
        return self._first_valid_items(attempts)

    @staticmethod
    def _unwrap_items(body: Any) -> list[dict]:
        """Extract a flat list of item dicts from any plausible wrapper."""
        if not isinstance(body, dict):
            return []
        candidates: list[Any] = []
        for key in ("data", "items", "results", "search_objects", "item", "objects"):
            if isinstance(body.get(key), list):
                candidates.extend(body[key])
        if isinstance(body.get("data"), dict):
            for key in ("items", "results", "search_objects", "list"):
                if isinstance(body["data"].get(key), list):
                    candidates.extend(body["data"][key])
        out: list[dict] = []
        for c in candidates:
            if isinstance(c, dict) and ("id" in c or "title" in c):
                out.append(c)
        return out

    @staticmethod
    def _first_valid_items(payloads: list[list[dict]]) -> list[dict]:
        for items in payloads:
            if items:
                return items
        return []

    # ── item detail / conversations ─────────────────────────────────
    def get_item(self, item_id: str, auth: bool = True) -> dict:
        status, body = self.get(E.ITEM_DETAIL.format(item_id=item_id), auth=auth)
        return body if isinstance(body, dict) else {}

    def list_conversations(self) -> list[dict]:
        """Return conversations with message metadata (identity of senders)."""
        for path in E.CONVERSATIONS_CANDIDATES:
            status, body = self.get(path, auth=True)
            items = self._unwrap_conversations(body)
            if items:
                return items
        return []

    @staticmethod
    def _unwrap_conversations(body: Any) -> list[dict]:
        if not isinstance(body, dict):
            return []
        for key in ("data", "conversations", "items", "results"):
            if isinstance(body.get(key), list):
                return body[key]
        if isinstance(body.get("data"), dict):
            for key in ("conversations", "items", "results"):
                if isinstance(body["data"].get(key), list):
                    return body["data"][key]
        return []
