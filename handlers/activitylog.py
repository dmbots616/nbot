"""
Activity Log & Reputation System
==================================
Tracks every user action in a group, builds a trust/reputation score,
and lets members vouch for each other to mark users as "genuine".

Features
--------
Activity Logging
  • Logs messages, media, edits, deletes, joins, leaves, commands,
    moderation actions (warn/mute/kick/ban) — all per chat
  • Optional forwarding to a dedicated log channel

Reputation Score
  • Earned automatically: +1 per message, +2 per media, +5 per vouch received
  • Penalised automatically: -10 per warn, -20 per mute, -50 per ban
  • Vouching: any member can vouch for another (once per pair per group)
  • Trust levels based on score: 🌱 New → 🥉 Known → 🥈 Trusted → 🥇 Respected → 💎 Legendary

Commands
--------
/activity [@user|reply]  — Show a user's recent activity log
/profile  [@user|reply]  — Show full reputation profile & trust badge
/top                     — Leaderboard of most reputed members
/vouch    [reply]        — Vouch for a user (reply to their message)
/unvouch  [reply]        — Remove your vouch

Admin commands
--------------
/setlogchannel           — Forward all logs to a channel (bot must be admin there)
/logchannel off          — Disable log channel forwarding
/logevents               — Choose which events to log (inline keyboard)
/logtoggle               — Turn activity logging on/off for this group
/userreport [@user|reply]— Full admin report on a user
"""

import time
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from utils.permissions import admin_only, ANONYMOUS_ADMIN_ID
from utils.database import (
    log_event,
    get_user_activity,
    get_recent_activity,
    get_reputation,
    get_top_users,
    increment_stat,
    add_vouch,
    remove_vouch,
    get_vouchers,
    get_log_settings,
    save_log_settings,
    record_name_if_changed,
    get_name_history,
    update_user_group,
    get_user_groups,
    get_user_global,
    get_join_leave_history,
    get_avg_messages_per_day,
    ALL_EVENTS,
)
from config.settings import ADMIN_IDS

# ---------------------------------------------------------------------------
# Trust level thresholds
# ---------------------------------------------------------------------------

TRUST_LEVELS = [
    (500,  "💎 Legendary"),
    (200,  "🥇 Respected"),
    (100,  "🥈 Trusted"),
    (30,   "🥉 Known"),
    (0,    "🌱 New"),
]


def _trust_badge(score: int) -> str:
    for threshold, label in TRUST_LEVELS:
        if score >= threshold:
            return label
    return "🌱 New"


def _score_bar(score: int, max_score: int = 500) -> str:
    filled = min(10, int((score / max(max_score, 1)) * 10))
    return "█" * filled + "░" * (10 - filled)


def _ts(ts: int) -> str:
    if not ts:
        return "Never"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _days_ago(ts: int) -> str:
    if not ts:
        return "?"
    diff = int(time.time()) - ts
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


# ---------------------------------------------------------------------------
# Resolve target user from reply or @username arg
# ---------------------------------------------------------------------------

async def _resolve_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[tuple[int, str, str]]:
    """Returns (user_id, full_name, username_display) or None."""
    msg = update.message
    if msg.reply_to_message:
        u = msg.reply_to_message.from_user
        return u.id, u.full_name, f"@{u.username}" if u.username else u.full_name

    if context.args:
        raw = context.args[0].lstrip("@")
        try:
            uid = int(raw)
            rep = await get_reputation(msg.chat_id, uid)
            if rep:
                return uid, rep["full_name"], rep.get("username") or rep["full_name"]
        except ValueError:
            # username lookup — check our DB
            async with __import__("aiosqlite").connect("data/bot.db") as db:
                cur = await db.execute(
                    "SELECT user_id, full_name, username FROM user_reputation "
                    "WHERE chat_id=? AND username=?",
                    (msg.chat_id, f"@{raw}"),
                )
                row = await cur.fetchone()
                if row:
                    return row[0], row[1], row[2]

    return None


# ---------------------------------------------------------------------------
# /activity
# ---------------------------------------------------------------------------

@admin_only
async def activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id

    target = await _resolve_target(update, context)
    if not target:
        # Show recent group-wide activity
        events = await get_recent_activity(chat_id, limit=20)
        if not events:
            await msg.reply_text("No activity recorded yet.")
            return
        lines = ["📋 *Recent Group Activity*\n"]
        for e in events:
            icon = _event_icon(e["event_type"])
            name = e["full_name"] or "Unknown"
            detail = f" — _{e['detail'][:40]}_" if e.get("detail") else ""
            lines.append(f"{icon} `{_days_ago(e['ts'])}` *{name}*{detail}")
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    user_id, full_name, uname = target
    events = await get_user_activity(chat_id, user_id, limit=15)
    if not events:
        await msg.reply_text(f"No activity recorded for {full_name}.")
        return

    lines = [f"📋 *Activity Log — {full_name}*\n"]
    for e in events:
        icon = _event_icon(e["event_type"])
        detail = f"\n   ↳ _{e['detail'][:60]}_" if e.get("detail") else ""
        lines.append(f"{icon} `{_days_ago(e['ts'])}` {e['event_type']}{detail}")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


def _event_icon(event_type: str) -> str:
    icons = {
        "message": "💬", "media": "🖼", "join": "👋", "leave": "🚪",
        "warn": "⚠️", "mute": "🔇", "unmute": "🔊", "kick": "👢",
        "ban": "🚫", "unban": "✅", "command": "⌨️", "edit": "✏️",
        "delete": "🗑", "pin": "📌", "vouch": "🤝",
    }
    return icons.get(event_type, "•")


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id

    target = await _resolve_target(update, context)
    if not target:
        # Show own profile
        u = msg.from_user
        target = (u.id, u.full_name, f"@{u.username}" if u.username else u.full_name)

    user_id, full_name, uname = target
    rep = await get_reputation(chat_id, user_id)
    vouchers = await get_vouchers(chat_id, user_id)

    if not rep:
        await msg.reply_text(f"No data found for {full_name} in this group.")
        return

    score   = rep["score"]
    badge   = _trust_badge(score)
    bar     = _score_bar(score)
    days_in = int((time.time() - (rep["first_seen"] or time.time())) / 86400)

    vouch_names = []
    for vid in vouchers[:5]:
        vrep = await get_reputation(chat_id, vid)
        vouch_names.append(vrep["full_name"] if vrep else str(vid))
    vouch_str = ", ".join(vouch_names) if vouch_names else "None"
    if len(vouchers) > 5:
        vouch_str += f" +{len(vouchers)-5} more"

    text = (
        f"👤 *{full_name}*\n"
        f"🆔 `{user_id}`\n\n"
        f"{badge}\n"
        f"Score: `{score}` `[{bar}]`\n\n"
        f"📊 *Activity Stats*\n"
        f"💬 Messages:  `{rep['msg_count']}`\n"
        f"🖼 Media:     `{rep['media_count']}`\n"
        f"📅 In group:  `{days_in} day(s)`\n"
        f"👁 Last seen: `{_days_ago(rep['last_seen'])}`\n\n"
        f"🛡 *Moderation History*\n"
        f"⚠️ Warns: `{rep['warn_count']}` | "
        f"🔇 Mutes: `{rep['mute_count']}` | "
        f"🚫 Bans: `{rep['ban_count']}`\n\n"
        f"🤝 *Vouched by* ({len(vouchers)}): {vouch_str}\n\n"
        f"🕐 Joined: `{_ts(rep['first_seen'])}`"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Activity Log", callback_data=f"log_activity:{chat_id}:{user_id}"),
        InlineKeyboardButton("🤝 Vouch",        callback_data=f"log_vouch:{chat_id}:{user_id}"),
    ]])
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)


# ---------------------------------------------------------------------------
# /top — leaderboard
# ---------------------------------------------------------------------------

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    users = await get_top_users(chat_id, limit=10)

    if not users:
        await msg.reply_text("No reputation data yet. Members need to chat first!")
        return

    lines = ["🏆 *Group Reputation Leaderboard*\n"]
    medals = ["🥇", "🥈", "🥉"] + ["•"] * 7

    for i, u in enumerate(users):
        badge = _trust_badge(u["score"])
        lines.append(
            f"{medals[i]} *{u['full_name']}*\n"
            f"   Score: `{u['score']}` | {badge}\n"
            f"   💬 `{u['msg_count']}` msgs · 🤝 `{u['vouch_count']}` vouches"
        )

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /vouch  and  /unvouch
# ---------------------------------------------------------------------------

async def vouch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    from_user = msg.from_user

    if not msg.reply_to_message:
        await msg.reply_text(
            "Reply to a message to vouch for that user.\n\n"
            "Vouching marks someone as genuine and gives them +5 reputation."
        )
        return

    target = msg.reply_to_message.from_user
    if target.id == from_user.id:
        await msg.reply_text("❌ You can't vouch for yourself.")
        return
    if target.is_bot:
        await msg.reply_text("❌ You can't vouch for a bot.")
        return

    # Ensure both users exist in DB
    now = int(time.time())
    rep = await get_reputation(chat_id, from_user.id)
    if not rep:
        await msg.reply_text("❌ You need some activity in this group before vouching.")
        return

    success = await add_vouch(chat_id, from_user.id, target.id)
    if not success:
        await msg.reply_text(
            f"You've already vouched for *{target.full_name}*.",
            parse_mode="Markdown",
        )
        return

    await log_event(
        chat_id, from_user.id,
        f"@{from_user.username}" if from_user.username else None,
        from_user.full_name,
        "vouch", f"Vouched for {target.full_name}",
    )

    trep = await get_reputation(chat_id, target.id)
    new_score = trep["score"] if trep else 0
    badge = _trust_badge(new_score)

    await msg.reply_text(
        f"🤝 *{from_user.full_name}* vouched for *{target.mention_html()}*!\n\n"
        f"Their trust level is now: {badge} (score: `{new_score}`)",
        parse_mode="HTML",
    )


async def unvouch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id

    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to remove your vouch for that user.")
        return

    target = msg.reply_to_message.from_user
    success = await remove_vouch(chat_id, msg.from_user.id, target.id)
    if success:
        await msg.reply_text(
            f"✅ Your vouch for *{target.full_name}* has been removed.",
            parse_mode="Markdown",
        )
    else:
        await msg.reply_text(
            f"You haven't vouched for *{target.full_name}*.",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# /userreport (admin only)
# ---------------------------------------------------------------------------

@admin_only
async def userreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id

    target = await _resolve_target(update, context)
    if not target:
        await msg.reply_text("Reply to a message or provide a username/ID.")
        return

    user_id, full_name, uname = target
    rep     = await get_reputation(chat_id, user_id)
    events  = await get_user_activity(chat_id, user_id, limit=25)
    vouchers = await get_vouchers(chat_id, user_id)

    if not rep:
        await msg.reply_text(f"No data for {full_name} in this group.")
        return

    score   = rep["score"]
    badge   = _trust_badge(score)

    # Event breakdown
    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e["event_type"]] = type_counts.get(e["event_type"], 0) + 1

    breakdown = " | ".join(
        f"{_event_icon(k)} {k}: `{v}`" for k, v in sorted(type_counts.items())
    )

    text = (
        f"🔍 *Admin Report — {full_name}*\n"
        f"🆔 `{user_id}` | {uname}\n\n"
        f"{badge} | Score: `{score}`\n\n"
        f"📊 *Totals*\n"
        f"💬 Messages: `{rep['msg_count']}` | 🖼 Media: `{rep['media_count']}`\n"
        f"⚠️ Warns: `{rep['warn_count']}` | 🔇 Mutes: `{rep['mute_count']}`\n"
        f"🚫 Bans: `{rep['ban_count']}` | 🤝 Vouches received: `{rep['vouch_count']}`\n\n"
        f"📅 First seen: `{_ts(rep['first_seen'])}`\n"
        f"👁 Last seen:  `{_ts(rep['last_seen'])}`\n\n"
        f"📋 *Recent event types*\n{breakdown or 'None'}\n\n"
        f"🤝 *Vouched by* ({len(vouchers)} users)"
    )
    await msg.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Log settings commands
# ---------------------------------------------------------------------------

@admin_only
async def set_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id

    if context.args and context.args[0].lower() == "off":
        settings = await get_log_settings(chat_id)
        settings["log_channel_id"] = None
        await save_log_settings(chat_id, settings)
        await msg.reply_text("✅ Log channel forwarding disabled.")
        return

    if not msg.forward_from_chat and not context.args:
        await msg.reply_text(
            "Usage:\n"
            "1. Forward any message from your log channel here, then run `/setlogchannel`\n"
            "2. Or: `/setlogchannel <channel_id>`\n"
            "3. Or: `/setlogchannel off` to disable\n\n"
            "The bot must be an admin in the log channel."
        )
        return

    if msg.forward_from_chat:
        channel_id = msg.forward_from_chat.id
    else:
        try:
            channel_id = int(context.args[0])
        except ValueError:
            await msg.reply_text("❌ Invalid channel ID.")
            return

    # Verify bot can post there
    try:
        await context.bot.send_message(
            channel_id,
            f"✅ Log channel set up for group `{chat_id}`.\nActivity logs will appear here.",
            parse_mode="Markdown",
        )
    except Exception:
        await msg.reply_text(
            "❌ Couldn't send a message to that channel.\n"
            "Make sure the bot is an admin there."
        )
        return

    settings = await get_log_settings(chat_id)
    settings["log_channel_id"] = channel_id
    await save_log_settings(chat_id, settings)
    await msg.reply_text(f"✅ Log channel set to `{channel_id}`.", parse_mode="Markdown")


@admin_only
async def log_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    settings = await get_log_settings(chat_id)
    settings["active"] = 0 if settings.get("active", 1) else 1
    await save_log_settings(chat_id, settings)
    state = "enabled" if settings["active"] else "disabled"
    await msg.reply_text(f"✅ Activity logging {state} for this group.")


@admin_only
async def log_events_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    settings = await get_log_settings(chat_id)
    enabled = set(settings.get("enabled_events", list(ALL_EVENTS.keys())))

    kb = _events_keyboard(chat_id, enabled)
    await msg.reply_text(
        "⚙️ *Event Logging Settings*\n\nToggle which events to log:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


def _events_keyboard(chat_id: int, enabled: set) -> InlineKeyboardMarkup:
    rows = []
    items = list(ALL_EVENTS.items())
    for i in range(0, len(items), 2):
        row = []
        for key, label in items[i:i+2]:
            tick = "✅" if key in enabled else "☑️"
            row.append(InlineKeyboardButton(
                f"{tick} {label}",
                callback_data=f"log_toggle_event:{chat_id}:{key}",
            ))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✅ Enable All",  callback_data=f"log_events_all:{chat_id}:1"),
        InlineKeyboardButton("☑️ Disable All", callback_data=f"log_events_all:{chat_id}:0"),
    ])
    rows.append([InlineKeyboardButton("✖ Close", callback_data=f"log_close:{chat_id}")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Callback queries
# ---------------------------------------------------------------------------

async def log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")

    action = parts[0]

    # log_toggle_event:<chat_id>:<event>
    if action == "log_toggle_event":
        chat_id, event = int(parts[1]), parts[2]
        settings = await get_log_settings(chat_id)
        enabled = set(settings.get("enabled_events", list(ALL_EVENTS.keys())))
        if event in enabled:
            enabled.discard(event)
        else:
            enabled.add(event)
        settings["enabled_events"] = list(enabled)
        await save_log_settings(chat_id, settings)
        await query.edit_message_reply_markup(
            reply_markup=_events_keyboard(chat_id, enabled)
        )
        return

    # log_events_all:<chat_id>:<1|0>
    if action == "log_events_all":
        chat_id, val = int(parts[1]), int(parts[2])
        settings = await get_log_settings(chat_id)
        settings["enabled_events"] = list(ALL_EVENTS.keys()) if val else []
        await save_log_settings(chat_id, settings)
        enabled = set(settings["enabled_events"])
        await query.edit_message_reply_markup(
            reply_markup=_events_keyboard(chat_id, enabled)
        )
        return

    # log_close:<chat_id>
    if action == "log_close":
        await query.delete_message()
        return

    # log_activity:<chat_id>:<user_id>
    if action == "log_activity":
        chat_id, user_id = int(parts[1]), int(parts[2])
        events = await get_user_activity(chat_id, user_id, limit=10)
        if not events:
            await query.answer("No activity recorded.", show_alert=True)
            return
        lines = ["📋 *Recent Activity*\n"]
        for e in events:
            icon = _event_icon(e["event_type"])
            detail = f" — _{e['detail'][:40]}_" if e.get("detail") else ""
            lines.append(f"{icon} `{_days_ago(e['ts'])}` {e['event_type']}{detail}")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # log_vouch:<chat_id>:<user_id>  (vouch via profile button)
    if action == "log_vouch":
        chat_id, to_id = int(parts[1]), int(parts[2])
        from_user = query.from_user
        if from_user.id == to_id:
            await query.answer("You can't vouch for yourself.", show_alert=True)
            return
        rep = await get_reputation(chat_id, from_user.id)
        if not rep:
            await query.answer("You need activity in this group first.", show_alert=True)
            return
        success = await add_vouch(chat_id, from_user.id, to_id)
        if not success:
            await query.answer("You already vouched for this user.", show_alert=True)
        else:
            await log_event(
                chat_id, from_user.id,
                f"@{from_user.username}" if from_user.username else None,
                from_user.full_name, "vouch", f"Vouched for user {to_id}",
            )
            trep = await get_reputation(chat_id, to_id)
            badge = _trust_badge(trep["score"] if trep else 0)
            await query.answer(f"✅ Vouched! Their trust: {badge}", show_alert=True)
        return


# ---------------------------------------------------------------------------
# Log channel forwarder
# ---------------------------------------------------------------------------

async def _forward_to_log_channel(
    bot, chat_id: int, text: str, settings: dict
) -> None:
    channel_id = settings.get("log_channel_id")
    if not channel_id or not settings.get("active", 1):
        return
    try:
        await bot.send_message(channel_id, text, parse_mode="Markdown")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Passive message tracker — logs activity and updates reputation
# ---------------------------------------------------------------------------

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.from_user:
        return

    user    = msg.from_user
    chat_id = msg.chat_id

    if user.is_bot:
        return

    settings = await get_log_settings(chat_id)
    if not settings.get("active", 1):
        return

    enabled = set(settings.get("enabled_events", list(ALL_EVENTS.keys())))
    uname   = f"@{user.username}" if user.username else None

    # Always track name changes and group membership
    await record_name_if_changed(user.id, uname, user.full_name)
    await update_user_group(user.id, chat_id, msg.chat.title)

    # Determine event type
    is_media = bool(msg.photo or msg.video or msg.audio or msg.voice
                    or msg.document or msg.animation or msg.sticker)

    if is_media and "media" in enabled:
        await log_event(chat_id, user.id, uname, user.full_name,
                        "media", msg.caption or "")
        await increment_stat(chat_id, user.id, "media_count")
        await increment_stat(chat_id, user.id, "score", 2)

        log_text = (
            f"🖼 *Media sent*\n"
            f"👤 [{user.full_name}](tg://user?id={user.id}) in `{chat_id}`\n"
            f"🕐 {_ts(int(time.time()))}"
        )
        await _forward_to_log_channel(context.bot, chat_id, log_text, settings)

    elif msg.text and "message" in enabled:
        preview = msg.text[:80]
        await log_event(chat_id, user.id, uname, user.full_name,
                        "message", preview)
        await increment_stat(chat_id, user.id, "msg_count")
        await increment_stat(chat_id, user.id, "score", 1)

    # Edited message
    if update.edited_message and "edit" in enabled:
        edited = update.edited_message
        if edited.from_user and not edited.from_user.is_bot:
            await log_event(chat_id, edited.from_user.id,
                            f"@{edited.from_user.username}" if edited.from_user.username else None,
                            edited.from_user.full_name,
                            "edit", (edited.text or "")[:80])


async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    settings = await get_log_settings(chat_id)
    if not settings.get("active", 1):
        return
    enabled = set(settings.get("enabled_events", list(ALL_EVENTS.keys())))
    if "join" not in enabled:
        return

    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        uname = f"@{member.username}" if member.username else None
        await log_event(chat_id, member.id, uname, member.full_name, "join")
        await record_name_if_changed(member.id, uname, member.full_name)
        await update_user_group(member.id, chat_id, msg.chat.title)
        log_text = (
            f"👋 *Member joined*\n"
            f"👤 [{member.full_name}](tg://user?id={member.id})\n"
            f"🆔 `{member.id}` | {uname or 'no username'}\n"
            f"🕐 {_ts(int(time.time()))}"
        )
        await _forward_to_log_channel(context.bot, chat_id, log_text, settings)


async def track_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    member = msg.left_chat_member
    if not member or member.is_bot:
        return
    settings = await get_log_settings(chat_id)
    if not settings.get("active", 1):
        return
    enabled = set(settings.get("enabled_events", list(ALL_EVENTS.keys())))
    if "leave" not in enabled:
        return

    uname = f"@{member.username}" if member.username else None
    await log_event(chat_id, member.id, uname, member.full_name, "leave")
    await record_name_if_changed(member.id, uname, member.full_name)
    log_text = (
        f"🚪 *Member left*\n"
        f"👤 [{member.full_name}](tg://user?id={member.id})\n"
        f"🕐 {_ts(int(time.time()))}"
    )
    await _forward_to_log_channel(context.bot, chat_id, log_text, settings)


# ---------------------------------------------------------------------------
# Public API for other handlers to call
# ---------------------------------------------------------------------------

async def log_moderation(
    bot,
    chat_id: int,
    actor: User,
    target: User,
    action: str,
    detail: str = "",
) -> None:
    """
    Called by moderation.py when warn/mute/kick/ban/unban happens.
    Updates reputation and forwards to log channel.
    """
    uname = f"@{target.username}" if target.username else None
    await log_event(chat_id, target.id, uname, target.full_name, action, detail)

    penalty_map = {"warn": -10, "mute": -20, "ban": -50, "kick": -10}
    count_map   = {"warn": "warn_count", "mute": "mute_count",
                   "ban": "ban_count", "kick": "ban_count"}

    if action in penalty_map:
        await increment_stat(chat_id, target.id, "score", penalty_map[action])
    if action in count_map:
        await increment_stat(chat_id, target.id, count_map[action])

    settings = await get_log_settings(chat_id)
    icon = _event_icon(action)
    actor_name = actor.full_name if actor else "Admin"
    log_text = (
        f"{icon} *{action.upper()}*\n"
        f"👤 Target: [{target.full_name}](tg://user?id={target.id}) (`{target.id}`)\n"
        f"🛡 By: {actor_name}\n"
        + (f"📝 Reason: _{detail}_\n" if detail else "")
        + f"🕐 {_ts(int(time.time()))}"
    )
    await _forward_to_log_channel(bot, chat_id, log_text, settings)


# ---------------------------------------------------------------------------
# /userinfo — context-aware user card
# ---------------------------------------------------------------------------

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    In a GROUP: shows join/leave history, visit count, reputation, trust level.
    In a DM   : shows name history, common groups with bot, global stats, avg messages/day.
    """
    msg      = update.message
    is_dm    = msg.chat.type == "private"
    chat_id  = msg.chat_id

    # ── resolve target ──
    target_user = None
    target_id   = None

    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user
        target_id   = target_user.id
    elif context.args:
        raw = context.args[0].lstrip("@")
        try:
            target_id = int(raw)
        except ValueError:
            # username lookup in DB
            import aiosqlite
            async with aiosqlite.connect("data/bot.db") as db:
                cur = await db.execute(
                    """SELECT user_id, full_name FROM user_reputation
                       WHERE username=? LIMIT 1""",
                    (f"@{raw}",),
                )
                row = await cur.fetchone()
                if row:
                    target_id = row[0]
                else:
                    await msg.reply_text(f"❌ No data found for @{raw}.")
                    return
    else:
        target_user = msg.from_user
        target_id   = target_user.id

    if not target_id:
        await msg.reply_text("Reply to a message or pass @username / user ID.")
        return

    # ── GROUP context ──
    if not is_dm:
        rep      = await get_reputation(chat_id, target_id)
        vouchers = await get_vouchers(chat_id, target_id)
        jl       = await get_join_leave_history(chat_id, target_id)
        avg_msg  = await get_avg_messages_per_day(chat_id, target_id)

        if not rep:
            await msg.reply_text(
                "❌ No data for this user in this group yet.\n"
                "They need to send at least one message after the bot joined."
            )
            return

        name   = rep["full_name"]
        uname  = rep.get("username") or "—"
        score  = rep["score"]
        badge  = _trust_badge(score)
        bar    = _score_bar(score)

        # Build join/leave timeline
        timeline_lines = []
        visit = 0
        for ev in jl:
            dt = _ts(ev["ts"])
            if ev["event_type"] == "join":
                visit += 1
                timeline_lines.append(f"  ✅ Joined  — `{dt}`  _(visit #{visit})_")
            else:
                timeline_lines.append(f"  🚪 Left    — `{dt}`")
        timeline = "\n".join(timeline_lines) if timeline_lines else "  _No join/leave recorded_"

        vouch_count = len(vouchers)

        text = (
            f"👤 *{name}*\n"
            f"🔖 {uname}  |  🆔 `{target_id}`\n\n"
            f"{badge}\n"
            f"Score: `{score}` `[{bar}]`\n\n"
            f"📅 *Group History*\n"
            f"{timeline}\n\n"
            f"📊 *Activity in this group*\n"
            f"💬 Messages: `{rep['msg_count']}`\n"
            f"🖼 Media:    `{rep['media_count']}`\n"
            f"📈 Avg/day:  `{avg_msg}` messages\n"
            f"👁 Last seen: `{_days_ago(rep['last_seen'])}`\n\n"
            f"🛡 *Moderation*\n"
            f"⚠️ Warns: `{rep['warn_count']}` | "
            f"🔇 Mutes: `{rep['mute_count']}` | "
            f"🚫 Bans: `{rep['ban_count']}`\n\n"
            f"🤝 Vouched by `{vouch_count}` member(s)"
        )

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Full Log",  callback_data=f"log_activity:{chat_id}:{target_id}"),
            InlineKeyboardButton("🤝 Vouch",     callback_data=f"log_vouch:{chat_id}:{target_id}"),
        ]])
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)

    # ── DM context ──
    else:
        global_rep = await get_user_global(target_id)
        name_hist  = await get_name_history(target_id, limit=8)
        groups     = await get_user_groups(target_id)

        if not global_rep:
            await msg.reply_text(
                "❌ No data found for this user.\n"
                "They haven't been seen in any group this bot manages."
            )
            return

        name  = global_rep["full_name"] or "Unknown"
        uname = global_rep.get("username") or "—"

        # Name/username history
        if name_hist:
            hist_lines = []
            for i, h in enumerate(name_hist):
                u = h.get("username") or "—"
                fn = h["full_name"]
                dt = _ts(h["changed_at"])
                marker = "👤" if i == 0 else "  ↩"
                hist_lines.append(f"{marker} `{fn}` ({u}) — _{dt}_")
            hist_text = "\n".join(hist_lines)
        else:
            hist_text = "_No history recorded_"

        # Common groups
        if groups:
            group_lines = []
            for g in groups[:10]:
                title = g.get("chat_title") or f"`{g['chat_id']}`"
                fs = _ts(g["first_seen"])
                ls = _days_ago(g["last_seen"])
                group_lines.append(f"• *{title}*\n  Joined: `{fs}` · Last seen: `{ls}`")
            groups_text = "\n".join(group_lines)
            if len(groups) > 10:
                groups_text += f"\n_...and {len(groups)-10} more_"
        else:
            groups_text = "_No common groups_"

        # Global score + trust
        score = global_rep["total_score"] or 0
        badge = _trust_badge(score)
        bar   = _score_bar(score)

        text = (
            f"👤 *{name}*\n"
            f"🔖 {uname}  |  🆔 `{target_id}`\n\n"
            f"🕐 First seen: `{_ts(global_rep['first_seen'])}`\n"
            f"👁 Last seen:  `{_days_ago(global_rep['last_seen'])}`\n\n"
            f"📝 *Name & Username History*\n"
            f"{hist_text}\n\n"
            f"🌐 *Common Groups with this bot* ({len(groups)})\n"
            f"{groups_text}\n\n"
            f"{badge}\n"
            f"Global Score: `{score}` `[{bar}]`\n\n"
            f"📊 *Global Activity*\n"
            f"💬 Total messages: `{global_rep['total_msgs'] or 0}`\n"
            f"🖼 Total media:    `{global_rep['total_media'] or 0}`\n\n"
            f"🛡 *Global Moderation*\n"
            f"⚠️ Warns: `{global_rep['total_warns'] or 0}` | "
            f"🚫 Bans: `{global_rep['total_bans'] or 0}`"
        )
        await msg.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_log_handlers(app: Application) -> None:
    # User commands
    app.add_handler(CommandHandler("activity",      activity_command))
    app.add_handler(CommandHandler("profile",       profile_command))
    app.add_handler(CommandHandler("top",           top_command))
    app.add_handler(CommandHandler("vouch",         vouch_command))
    app.add_handler(CommandHandler("unvouch",       unvouch_command))
    app.add_handler(CommandHandler("userinfo",      userinfo_command))

    # Admin commands
    app.add_handler(CommandHandler("userreport",    userreport_command))
    app.add_handler(CommandHandler("setlogchannel", set_log_channel))
    app.add_handler(CommandHandler("logchannel",    set_log_channel))
    app.add_handler(CommandHandler("logtoggle",     log_toggle))
    app.add_handler(CommandHandler("logevents",     log_events_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(log_callback, pattern=r"^log_"))

    # Passive trackers — lowest priority (group 98)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO
             | filters.DOCUMENT | filters.VOICE | filters.Sticker.ALL)
            & ~filters.COMMAND,
            track_message,
        ),
        group=98,
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_join),
        group=98,
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, track_leave),
        group=98,
    )
