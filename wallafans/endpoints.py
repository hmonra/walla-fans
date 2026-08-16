"""Discovered Wallapop API endpoints (reverse engineered).

Some of these were confirmed during the audit; others are candidates that the
capture tool validates with a real session. Kept in one place so adjustments
are trivial.
"""
from __future__ import annotations

API = "https://api.wallapop.com"
WEB = "https://es.wallapop.com"
ACCOUNTS = "https://accounts.wallapop.com"

# Public / auth endpoints confirmed working
STATS = "/api/v3/users/{user_id}/stats"
PUBLIC_PROFILE = "/api/v3/users/{user_id}"
ME = "/api/v3/users/me"
NOTIFICATIONS_CONFIG = "/api/v3/notifications/me/config"

# Candidates for listing the logged user's own items
# Verified live (2026-08): GET /api/v3/users/{uid}/items -> {"data":[...]}
MY_ITEMS = "/api/v3/users/{user_id}/items"

ITEM_DETAIL = "/api/v3/items/{item_id}"

# Conversations (identity of message senders). Candidates to validate.
CONVERSATIONS_CANDIDATES = [
    "/api/v3/conversations",
    "/api/v3/users/me/conversations",
    "/api/v3/me/conversations",
]

# Keycloak token endpoint (refresh flow discovery target)
KEYCLOAK_TOKEN = f"{ACCOUNTS}/realms/wallapop-internal/protocol/openid-connect/token"

# PubNub (public client keys extracted from the web JS bundle)
PUBNUB_SUBSCRIBE_KEY = "sub-c-89405e27-d4df-4d87-aca1-d6e9118f0a0d"
PUBNUB_PUBLISH_KEY = "pub-c-255dc549-86f5-4abd-8b9e-921d5a02fde7"

# PubNub REST (used for history fetch without the SDK)
PUBNUB_PS = "https://ps.pndsn.com"

# Counter types observed in /users/{id}/stats
COUNTERS = {
    "publish": "publish",
    "buys": "buys",
    "sells": "sells",
    "reviews": "reviews",
    "sold": "sold",
    "reports_received": "reports_received",
    "profile_favorited_received": "profileFavoritedReceived",
    "rating_average": "rating_average",
}
