"""Minimal Telegram Bot API client with 429 handling and inline buttons."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("wallasentry.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def _call(self, method: str, payload: dict, retries: int = 4) -> bool:
        if not self.token or not self.chat_id:
            return False
        payload.setdefault("chat_id", self.chat_id)
        for attempt in range(retries):
            try:
                resp = requests.post(API.format(token=self.token, method=method),
                                     json=payload, timeout=30)
                if resp.status_code == 429:
                    wait = int(resp.json().get("parameters", {}).get("retry_after", 5))
                    time.sleep(min(wait, 30))
                    continue
                ok = resp.ok and resp.json().get("ok", False)
                if not ok:
                    log.warning("Telegram %s failed: %s", method, resp.text[:200])
                return ok
            except requests.RequestException as exc:
                log.warning("Telegram %s network error: %s", method, exc)
                time.sleep(2 ** attempt)
        return False

    def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = True,
    ) -> bool:
        payload: dict[str, Any] = {
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def send_photo(self, photo_url: str, caption: str, reply_markup: dict | None = None) -> bool:
        payload: dict[str, Any] = {"photo": photo_url, "caption": caption}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendPhoto", payload)

    @staticmethod
    def buttons(*pairs: tuple[str, str]) -> dict:
        """pairs of (label, url) -> inline keyboard."""
        return {"inline_keyboard": [[{"text": label, "url": url} for label, url in pairs]]}

    def get_me(self) -> dict | None:
        try:
            resp = requests.get(API.format(token=self.token, method="getMe"), timeout=15)
            data = resp.json()
            return data.get("result") if data.get("ok") else None
        except Exception:
            return None