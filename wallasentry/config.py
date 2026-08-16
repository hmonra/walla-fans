"""Configuration: .env loader + typed access to settings.

Values are read from the environment first, falling back to the local .env
file (never committed). This keeps secrets out of the repository.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        load_dotenv()
        # Wallapop auth
        self.refresh_token = os.environ.get("WALLAFANS_REFRESH_TOKEN", "").strip()
        self.refresh_client_id = os.environ.get("WALLAFANS_REFRESH_CLIENT_ID", "web").strip()
        self.session_cookie = os.environ.get("WALLAFANS_SESSION_COOKIE", "").strip()
        self.email = os.environ.get("WALLAFANS_EMAIL", "").strip()
        self.password = os.environ.get("WALLAFANS_PASSWORD", "").strip()
        self.user_id = os.environ.get("WALLAFANS_USER_ID", "").strip()
        # Telegram
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        # PubNub
        self.pubnub_subscribe_key = os.environ.get("PUBNUB_SUBSCRIBE_KEY", "").strip()
        self.pubnub_publish_key = os.environ.get("PUBNUB_PUBLISH_KEY", "").strip()
        self.pubnub_auth_token = os.environ.get("PUBNUB_AUTH_TOKEN", "").strip()
        self.pubnub_channel = os.environ.get("PUBNUB_CHANNEL", "").strip()
        self.pubnub_last_timetoken = _int("PUBNUB_LAST_TIMETOKEN", 0)
        # Behaviour
        self.digest_evening_hour = _int("DIGEST_EVENING_HOUR", 21)
        self.digest_morning_hour = _int("DIGEST_MORNING_HOUR", 8)
        self.digest_tz = os.environ.get("DIGEST_TZ", "Europe/Madrid").strip()
        self.max_messages_per_run = _int("MAX_MESSAGES_PER_RUN", 5)

    @property
    def has_any_wallapop_auth(self) -> bool:
        return bool(
            self.refresh_token or self.session_cookie or (self.email and self.password)
        )


settings = Settings()
