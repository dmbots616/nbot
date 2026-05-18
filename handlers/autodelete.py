"""
Auto-delete handler (per message-type)
=======================================
Admins configure auto-delete via an interactive inline keyboard menu,
or directly via commands.

Commands
--------
/autodelete          — Open the settings menu (inline keyboard)
/autodelete status   — Show current settings as text
/autodelete off      — Disable ALL auto-delete rules at once

Per-type command syntax
------------------------
/autodelete <type> <duration|off>

  Types   : text, photo, video, audio, voice, document,
            sticker, gif, poll, forward, all
  Duration: 10s, 5m, 2h, 1d  (10s min – 7d max)

Examples
--------
/autodelete text 10m       → delete text messages after 10 minutes
/autodelete photo 1h       → delete photos after 1 hour
/autodelete video off      → stop auto-deleting videos
/autodelete all 30m        → apply 30-minute timer to every type
/autodelete off            → disable everything

The setting is stored in-memory per chat.
Swap _store for a real DB if you need persistence across restarts.
"""

import re
from datetime import timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from utils.permissions import admin_only

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MIN_SECONDS = 10
_MAX_SECONDS = 7 * 86400

# All supported message types and their display names / emoji
MESSAGE_TYPES: dict[str, tuple[str, str]] = {
    "text":     ("Text",      "💬"),
    "photo":    ("Photo",     "🖼️"),
    "video":    ("Video",     "🎬"),
    "audio":    ("Audio",     "🎵"),
    "voice":    ("Voice",     "🎤"),
    "document": ("Document",  "📄"),
    "sticker":  ("Sticker",   "🎭"),
    "gif":      ("GIF",       "🎞️"),
    "poll":     ("Poll",      "📊"),
    "forward":  ("Forward",   "↪️"),
}

# Quick-pick durations shown in the inline keyboard
_QUICK_DURATIONS = [
    ("30s", 30),
    ("1m",  60),
    ("5m",  300),
    ("10m", 600),
    ("30m", 1800),
    ("1h",  3600),
    ("6h",  21600),
    ("12h", 43200),
    ("1d",  86400),
    ("7d",  604800),
]

# ---------------------------------------------------------------------------
# In-memory store
# {chat_id: {msg_type: seconds}}   — only types with an active timer appear
# ---------------------------------------------------------------------------
_store: dict[int, dict[str, int]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_duration(raw: str) -> Optional[int]:
    match = re.fullmatch(r"(\d+)([smhd])", raw.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    seconds = value * _UNITS[unit]
    return seconds if _MIN_SECONDS <= seconds <= _MAX_SECONDS else None


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _format_duration_long(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second(s)"
    if seconds < 3600:
        return f"{seconds // 60} minute(s)"
    if seconds < 86400:
        return f"{seconds // 3600} hour(s)"
    return f"{seconds // 86400} day(s)"


def _get_settings(chat_id: int) -> dict[str, int]:
    return _store.get(chat_id, {})


def _set_type(chat_id: int, msg_type: str, seconds: int) -> None:
    _store.setdefault(chat_id, {})[msg_type] = seconds


def _clear_type(chat_id: int, msg_type: str) -> None:
    if chat_id in _store:
        _store[chat_id].pop(msg_type, None)


def _clear_all(chat_id: int) -> None:
    _store.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Inline keyboard builders
# ---------------------------------------------------------------------------

def _main_menu_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Main menu: one button per message type showing current timer."""
    settings = _get_settings(chat_id)
    rows = []
    items = list(MESSAGE_TYPES.items())
    # 2 columns
    for i in range(0, len(items), 2):
        row = []
        for key, (label, emoji) in items[i:i + 2]:
            timer = settings.get(key)
            status = f" · {_format_duration(timer)}" if timer else " · off"
            row.append(InlineKeyboardButton(
                f"{emoji} {label}{status}",
                callback_data=f"ad_type:{key}",
            ))
        rows.append(row)

    rows.append([
        InlineKeyboardButton("⚡ Set All Types", callback_data="ad_type:all"),
        InlineKeyboardButton("🗑 Disable All",   callback_data="ad_disable_all"),
    ])
    rows.append([InlineKeyboardButton("✖ Close", callback_data="ad_close")])
    return InlineKeyboardMarkup(rows)


def _duration_keyboard(msg_type: str) -> InlineKeyboardMarkup:
    """Duration picker for a specific message type."""
    rows = []
    dur_buttons = [
        InlineKeyboardButton(label, callback_data=f"ad_set:{msg_type}:{secs}")
        for label, secs in _QUICK_DURATIONS
    ]
    for i in range(0, len(dur_buttons), 5):
        rows.append(dur_buttons[i:i + 5])

    rows.append([
        InlineKeyboardButton("🔕 Turn Off", callback_data=f"ad_off:{msg_type}"),
        InlineKeyboardButton("« Back",      callback_data="ad_back"),
    ])
    return InlineKeyboardMarkup(rows)


def _status_text(chat_id: int) -> str:
    settings = _get_settings(chat_id)
    if not settings:
        return "🕐 Auto-delete is *OFF* for all message types."

    lines = ["🕐 *Auto-delete settings:*\n"]
    for key, (label, emoji) in MESSAGE_TYPES.items():
        timer = settings.get(key)
        val = f"`{_format_duration_long(timer)}`" if timer else "off"
        lines.append(f"{emoji} {label}: {val}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

@admin_only
async def autodelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    args = context.args

    # /autodelete  →  open interactive menu
    if not args:
        await msg.reply_text(
            "⚙️ *Auto-Delete Settings*\nChoose a message type to configure:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )
        return

    # /autodelete status
    if args[0].lower() == "status":
        await msg.reply_text(_status_text(chat_id), parse_mode="Markdown")
        return

    # /autodelete off
    if args[0].lower() == "off" and len(args) == 1:
        _clear_all(chat_id)
        await msg.reply_text("✅ Auto-delete *disabled* for all message types.", parse_mode="Markdown")
        return

    # /autodelete <type> <duration|off>
    if len(args) < 2:
        await msg.reply_text(
            "Usage:\n"
            "`/autodelete` — open settings menu\n"
            "`/autodelete status` — view current settings\n"
            "`/autodelete <type> <duration>` — set timer for a type\n"
            "`/autodelete <type> off` — disable for a type\n"
            "`/autodelete all <duration>` — apply to all types\n"
            "`/autodelete off` — disable everything\n\n"
            f"Types: `{'`, `'.join(MESSAGE_TYPES.keys())}`, `all`",
            parse_mode="Markdown",
        )
        return

    msg_type = args[0].lower()
    duration_arg = args[1].lower()

    valid_types = list(MESSAGE_TYPES.keys()) + ["all"]
    if msg_type not in valid_types:
        await msg.reply_text(
            f"❌ Unknown type `{msg_type}`.\nValid types: `{'`, `'.join(valid_types)}`",
            parse_mode="Markdown",
        )
        return

    if duration_arg == "off":
        if msg_type == "all":
            _clear_all(chat_id)
            await msg.reply_text("✅ Auto-delete disabled for all types.", parse_mode="Markdown")
        else:
            _clear_type(chat_id, msg_type)
            emoji = MESSAGE_TYPES[msg_type][1]
            await msg.reply_text(
                f"✅ {emoji} *{msg_type.capitalize()}* auto-delete turned off.",
                parse_mode="Markdown",
            )
        return

    seconds = _parse_duration(duration_arg)
    if seconds is None:
        await msg.reply_text(
            f"❌ Invalid duration `{duration_arg}`.\n"
            f"Format: `10s`, `5m`, `2h`, `1d` (range: 10s – 7d).",
            parse_mode="Markdown",
        )
        return

    if msg_type == "all":
        for t in MESSAGE_TYPES:
            _set_type(chat_id, t, seconds)
        await msg.reply_text(
            f"✅ Auto-delete set to *{_format_duration_long(seconds)}* for *all* message types.",
            parse_mode="Markdown",
        )
    else:
        _set_type(chat_id, msg_type, seconds)
        emoji = MESSAGE_TYPES[msg_type][1]
        await msg.reply_text(
            f"✅ {emoji} *{msg_type.capitalize()}* messages will be deleted after "
            f"*{_format_duration_long(seconds)}*.",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Inline keyboard callback handler
# ---------------------------------------------------------------------------

async def autodelete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    # Only admins (including anonymous) should be able to interact
    from utils.permissions import ANONYMOUS_ADMIN_ID
    from telegram import ChatMember
    from config.settings import ADMIN_IDS

    user = query.from_user
    is_allowed = False
    if user.id == ANONYMOUS_ADMIN_ID or user.id in ADMIN_IDS:
        is_allowed = True
    else:
        member = await query.message.chat.get_member(user.id)
        if member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
            is_allowed = True

    if not is_allowed:
        await query.answer("⛔ Only admins can change auto-delete settings.", show_alert=True)
        return

    # ad_type:<type>  →  show duration picker
    if data.startswith("ad_type:"):
        msg_type = data.split(":", 1)[1]
        label = "All Types" if msg_type == "all" else MESSAGE_TYPES[msg_type][0]
        emoji = "⚡" if msg_type == "all" else MESSAGE_TYPES[msg_type][1]
        await query.edit_message_text(
            f"⏱ Choose how long before *{emoji} {label}* messages are deleted:",
            parse_mode="Markdown",
            reply_markup=_duration_keyboard(msg_type),
        )

    # ad_set:<type>:<seconds>  →  apply setting
    elif data.startswith("ad_set:"):
        _, msg_type, secs_str = data.split(":")
        seconds = int(secs_str)

        if msg_type == "all":
            for t in MESSAGE_TYPES:
                _set_type(chat_id, t, seconds)
            text = f"✅ All types set to *{_format_duration_long(seconds)}*."
        else:
            _set_type(chat_id, msg_type, seconds)
            emoji = MESSAGE_TYPES[msg_type][1]
            text = (
                f"✅ {emoji} *{msg_type.capitalize()}* → "
                f"deleted after *{_format_duration_long(seconds)}*."
            )

        await query.edit_message_text(
            f"{text}\n\n⚙️ *Auto-Delete Settings*\nChoose a message type to configure:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )

    # ad_off:<type>  →  disable for type
    elif data.startswith("ad_off:"):
        msg_type = data.split(":", 1)[1]
        if msg_type == "all":
            _clear_all(chat_id)
            text = "✅ Auto-delete disabled for all types."
        else:
            _clear_type(chat_id, msg_type)
            emoji = MESSAGE_TYPES[msg_type][1]
            text = f"✅ {emoji} *{msg_type.capitalize()}* auto-delete turned off."

        await query.edit_message_text(
            f"{text}\n\n⚙️ *Auto-Delete Settings*\nChoose a message type to configure:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )

    # ad_disable_all  →  clear everything
    elif data == "ad_disable_all":
        _clear_all(chat_id)
        await query.edit_message_text(
            "✅ Auto-delete *disabled* for all message types.\n\n"
            "⚙️ *Auto-Delete Settings*\nChoose a message type to configure:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )

    # ad_back  →  back to main menu
    elif data == "ad_back":
        await query.edit_message_text(
            "⚙️ *Auto-Delete Settings*\nChoose a message type to configure:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )

    # ad_close  →  remove the menu
    elif data == "ad_close":
        await query.delete_message()


# ---------------------------------------------------------------------------
# Message interceptor — schedule deletions
# ---------------------------------------------------------------------------

def _detect_type(msg) -> Optional[str]:
    """Return the autodelete type key for a message, or None if unrecognised."""
    if msg.forward_origin or msg.forward_from or msg.forward_from_chat:
        return "forward"
    if msg.text:
        return "text"
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.audio:
        return "audio"
    if msg.voice:
        return "voice"
    if msg.animation:
        return "gif"
    if msg.document:
        return "document"
    if msg.sticker:
        return "sticker"
    if msg.poll:
        return "poll"
    return None


async def _schedule_delete(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def handle_message_for_autodelete(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.message or update.channel_post
    if not msg:
        return

    settings = _get_settings(msg.chat_id)
    if not settings:
        return

    msg_type = _detect_type(msg)
    if not msg_type:
        return

    delay = settings.get(msg_type)
    if not delay:
        return

    context.job_queue.run_once(
        _schedule_delete,
        when=timedelta(seconds=delay),
        data=(msg.chat_id, msg.message_id),
        name=f"autodel_{msg.chat_id}_{msg.message_id}",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_autodelete_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("autodelete", autodelete_command))
    app.add_handler(CallbackQueryHandler(autodelete_callback, pattern=r"^ad_"))
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message_for_autodelete),
        group=99,
    )
