"""
Database layer — MongoDB via motor (async)
==========================================

Collections (one MongoDB database, configurable via MONGO_DB_NAME):

  user_activity     — every loggable event per user per chat
  user_reputation   — trust score, counts, first/last seen per user per chat
  vouches           — vouch relationships
  log_settings      — per-chat logging config
  username_history  — name/username change log per user
  user_groups       — which groups each user has appeared in
  known_chats       — all groups the bot has been active in
  group_settings    — per-chat feature toggles

All public function signatures are identical to the previous SQLite layer,
so no other file in the project needs to change.

Environment variables (add to .env):
    MONGO_URI      — MongoDB connection string
                     e.g. mongodb://localhost:27017
                     or   mongodb+srv://user:pass@cluster.mongodb.net
    MONGO_DB_NAME  — database name (default: telegram_bot)
"""

import os
import time
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def _get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db


async def init_db() -> None:
    """Connect to MongoDB and ensure all indexes exist."""
    global _client, _db

    uri  = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    name = os.getenv("MONGO_DB_NAME", "telegram_bot")

    _client = AsyncIOMotorClient(uri)
    _db     = _client[name]

    db = _db

    # ── user_activity indexes ──
    await db.user_activity.create_index(
        [("chat_id", ASCENDING), ("user_id", ASCENDING)], name="idx_activity_chat_user"
    )
    await db.user_activity.create_index(
        [("chat_id", ASCENDING), ("ts", DESCENDING)], name="idx_activity_ts"
    )
    await db.user_activity.create_index(
        [("chat_id", ASCENDING), ("user_id", ASCENDING), ("event_type", ASCENDING)],
        name="idx_activity_type",
    )

    # ── user_reputation indexes ──
    await db.user_reputation.create_index(
        [("chat_id", ASCENDING), ("user_id", ASCENDING)],
        unique=True,
        name="idx_rep_chat_user",
    )
    await db.user_reputation.create_index(
        [("user_id", ASCENDING)], name="idx_rep_user"
    )
    await db.user_reputation.create_index(
        [("chat_id", ASCENDING), ("score", DESCENDING)], name="idx_rep_score"
    )

    # ── vouches indexes ──
    await db.vouches.create_index(
        [("chat_id", ASCENDING), ("from_id", ASCENDING), ("to_id", ASCENDING)],
        unique=True,
        name="idx_vouches_unique",
    )
    await db.vouches.create_index(
        [("chat_id", ASCENDING), ("to_id", ASCENDING)], name="idx_vouches_to"
    )

    # ── log_settings ──
    await db.log_settings.create_index(
        [("chat_id", ASCENDING)], unique=True, name="idx_log_chat"
    )

    # ── username_history ──
    await db.username_history.create_index(
        [("user_id", ASCENDING), ("changed_at", DESCENDING)], name="idx_uname_user"
    )

    # ── user_groups ──
    await db.user_groups.create_index(
        [("user_id", ASCENDING), ("chat_id", ASCENDING)],
        unique=True,
        name="idx_ugroups_unique",
    )
    await db.user_groups.create_index(
        [("user_id", ASCENDING)], name="idx_ugroups_user"
    )

    # ── known_chats ──
    await db.known_chats.create_index(
        [("chat_id", ASCENDING)], unique=True, name="idx_known_chat"
    )

    # ── group_settings ──
    await db.group_settings.create_index(
        [("chat_id", ASCENDING)], unique=True, name="idx_gs_chat"
    )

    print(f"[DB] Connected to MongoDB — database: '{name}'")


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------

async def log_event(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    full_name: str,
    event_type: str,
    detail: str = "",
) -> None:
    db  = _get_db()
    now = int(time.time())

    await db.user_activity.insert_one({
        "chat_id":    chat_id,
        "user_id":    user_id,
        "username":   username,
        "full_name":  full_name,
        "event_type": event_type,
        "detail":     detail,
        "ts":         now,
    })

    # Upsert reputation row (touch last_seen)
    await db.user_reputation.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set":      {"username": username, "full_name": full_name, "last_seen": now},
         "$setOnInsert": {
             "score":       0,
             "msg_count":   0,
             "media_count": 0,
             "warn_count":  0,
             "ban_count":   0,
             "mute_count":  0,
             "vouch_count": 0,
             "first_seen":  now,
         }},
        upsert=True,
    )


async def get_user_activity(
    chat_id: int,
    user_id: int,
    limit: int = 20,
    event_type: Optional[str] = None,
) -> list[dict]:
    db     = _get_db()
    query  = {"chat_id": chat_id, "user_id": user_id}
    if event_type:
        query["event_type"] = event_type

    cursor = db.user_activity.find(query).sort("ts", DESCENDING).limit(limit)
    return [_clean(doc) async for doc in cursor]


async def get_recent_activity(chat_id: int, limit: int = 30) -> list[dict]:
    db     = _get_db()
    cursor = db.user_activity.find({"chat_id": chat_id}).sort("ts", DESCENDING).limit(limit)
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------

async def increment_stat(chat_id: int, user_id: int, col: str, by: int = 1) -> None:
    allowed = {"msg_count", "media_count", "warn_count", "ban_count",
               "mute_count", "vouch_count", "score"}
    if col not in allowed:
        return
    db = _get_db()
    await db.user_reputation.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$inc": {col: by}},
    )


async def get_reputation(chat_id: int, user_id: int) -> Optional[dict]:
    db  = _get_db()
    doc = await db.user_reputation.find_one({"chat_id": chat_id, "user_id": user_id})
    return _clean(doc) if doc else None


async def get_top_users(chat_id: int, limit: int = 10) -> list[dict]:
    db     = _get_db()
    cursor = (
        db.user_reputation
        .find({"chat_id": chat_id})
        .sort("score", DESCENDING)
        .limit(limit)
    )
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Vouches
# ---------------------------------------------------------------------------

async def add_vouch(chat_id: int, from_id: int, to_id: int) -> bool:
    """Returns True if the vouch was new, False if it already existed."""
    db  = _get_db()
    now = int(time.time())
    try:
        await db.vouches.insert_one({
            "chat_id": chat_id,
            "from_id": from_id,
            "to_id":   to_id,
            "ts":      now,
        })
        await increment_stat(chat_id, to_id, "vouch_count")
        await increment_stat(chat_id, to_id, "score", 5)
        return True
    except Exception:
        return False  # Duplicate key — vouch already exists


async def remove_vouch(chat_id: int, from_id: int, to_id: int) -> bool:
    db     = _get_db()
    result = await db.vouches.delete_one({
        "chat_id": chat_id, "from_id": from_id, "to_id": to_id
    })
    if result.deleted_count:
        await increment_stat(chat_id, to_id, "vouch_count", -1)
        await increment_stat(chat_id, to_id, "score", -5)
        return True
    return False


async def get_vouchers(chat_id: int, user_id: int) -> list[dict]:
    db     = _get_db()
    cursor = db.vouches.find({"chat_id": chat_id, "to_id": user_id})
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Log settings
# ---------------------------------------------------------------------------

async def get_log_settings(chat_id: int) -> dict:
    db  = _get_db()
    doc = await db.log_settings.find_one({"chat_id": chat_id})
    if doc:
        return _clean(doc)
    return {
        "chat_id":        chat_id,
        "log_channel_id": None,
        "enabled_events": list(ALL_EVENTS.keys()),
        "active":         1,
    }


async def save_log_settings(chat_id: int, settings: dict) -> None:
    db = _get_db()
    settings = {k: v for k, v in settings.items() if k != "_id"}
    await db.log_settings.update_one(
        {"chat_id": chat_id},
        {"$set": settings},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Event type catalogue
# ---------------------------------------------------------------------------

ALL_EVENTS = {
    "message":  "💬 Text messages",
    "media":    "🖼 Media sent",
    "join":     "👋 Member joined",
    "leave":    "🚪 Member left",
    "warn":     "⚠️ Warned",
    "mute":     "🔇 Muted",
    "unmute":   "🔊 Unmuted",
    "kick":     "👢 Kicked",
    "ban":      "🚫 Banned",
    "unban":    "✅ Unbanned",
    "command":  "⌨️ Commands used",
    "edit":     "✏️ Message edited",
    "delete":   "🗑 Message deleted",
    "pin":      "📌 Message pinned",
    "vouch":    "🤝 Vouched",
}


# ---------------------------------------------------------------------------
# Username / name history
# ---------------------------------------------------------------------------

async def record_name_if_changed(
    user_id: int,
    username: Optional[str],
    full_name: str,
) -> None:
    db  = _get_db()
    now = int(time.time())

    # Check most recent record for this user
    last = await db.username_history.find_one(
        {"user_id": user_id},
        sort=[("changed_at", DESCENDING)],
    )

    if last is None or last.get("username") != username or last.get("full_name") != full_name:
        await db.username_history.insert_one({
            "user_id":    user_id,
            "username":   username,
            "full_name":  full_name,
            "changed_at": now,
        })


async def get_name_history(user_id: int, limit: int = 10) -> list[dict]:
    db     = _get_db()
    cursor = (
        db.username_history
        .find({"user_id": user_id})
        .sort("changed_at", DESCENDING)
        .limit(limit)
    )
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Common groups tracking
# ---------------------------------------------------------------------------

async def update_user_group(
    user_id: int,
    chat_id: int,
    chat_title: Optional[str],
) -> None:
    db  = _get_db()
    now = int(time.time())
    await db.user_groups.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set":         {"chat_title": chat_title, "last_seen": now},
         "$setOnInsert": {"first_seen": now}},
        upsert=True,
    )


async def get_user_groups(user_id: int) -> list[dict]:
    db     = _get_db()
    cursor = db.user_groups.find({"user_id": user_id}).sort("last_seen", DESCENDING)
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Cross-chat user lookup (for DM /userinfo)
# ---------------------------------------------------------------------------

async def get_user_global(user_id: int) -> Optional[dict]:
    """Aggregate stats across ALL groups for a user."""
    db = _get_db()

    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id":         "$user_id",
            "username":    {"$last": "$username"},
            "full_name":   {"$last": "$full_name"},
            "total_msgs":  {"$sum": "$msg_count"},
            "total_media": {"$sum": "$media_count"},
            "total_warns": {"$sum": "$warn_count"},
            "total_bans":  {"$sum": "$ban_count"},
            "total_score": {"$sum": "$score"},
            "first_seen":  {"$min": "$first_seen"},
            "last_seen":   {"$max": "$last_seen"},
        }},
    ]

    cursor = db.user_reputation.aggregate(pipeline)
    async for doc in cursor:
        if doc.get("first_seen"):
            doc["user_id"] = doc.pop("_id")
            return doc
    return None


async def get_join_leave_history(chat_id: int, user_id: int) -> list[dict]:
    db     = _get_db()
    cursor = (
        db.user_activity
        .find({"chat_id": chat_id, "user_id": user_id, "event_type": {"$in": ["join", "leave"]}})
        .sort("ts", ASCENDING)
    )
    return [_clean(doc) async for doc in cursor]


async def get_avg_messages_per_day(chat_id: int, user_id: int) -> float:
    db = _get_db()

    pipeline = [
        {"$match": {"chat_id": chat_id, "user_id": user_id, "event_type": "message"}},
        {"$group": {
            "_id":   None,
            "first": {"$min": "$ts"},
            "last":  {"$max": "$ts"},
            "total": {"$sum": 1},
        }},
    ]

    cursor = db.user_activity.aggregate(pipeline)
    async for doc in cursor:
        if not doc["first"] or doc["total"] == 0:
            return 0.0
        days = max(1, (doc["last"] - doc["first"]) / 86400)
        return round(doc["total"] / days, 1)
    return 0.0


# ---------------------------------------------------------------------------
# Known chats (for broadcast)
# ---------------------------------------------------------------------------

async def upsert_known_chat(chat_id: int, chat_title: str, is_admin: bool) -> None:
    db  = _get_db()
    now = int(time.time())
    await db.known_chats.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_title": chat_title, "is_admin": is_admin, "last_seen": now}},
        upsert=True,
    )


async def get_all_known_chats(admin_only: bool = True) -> list[dict]:
    db    = _get_db()
    query = {"is_admin": True} if admin_only else {}
    cursor = db.known_chats.find(query).sort("last_seen", DESCENDING)
    return [_clean(doc) async for doc in cursor]


# ---------------------------------------------------------------------------
# Group settings (service msg delete, hyperlink filter)
# ---------------------------------------------------------------------------

async def get_group_settings(chat_id: int) -> dict:
    db  = _get_db()
    doc = await db.group_settings.find_one({"chat_id": chat_id})
    if doc:
        return _clean(doc)
    return {
        "chat_id":             chat_id,
        "delete_service_msgs": 0,
        "delete_hyperlinks":   0,
    }


async def save_group_settings(chat_id: int, settings: dict) -> None:
    db = _get_db()
    settings = {k: v for k, v in settings.items() if k != "_id"}
    await db.group_settings.update_one(
        {"chat_id": chat_id},
        {"$set": settings},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _clean(doc: dict) -> dict:
    """Remove the MongoDB _id field so callers get clean plain dicts."""
    doc.pop("_id", None)
    return doc
