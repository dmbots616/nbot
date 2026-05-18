"""
Repeat Message Handler
======================
Admins can create scheduled repeating messages per group using a
step-by-step wizard. Each repeating message supports:

  - Custom text (with Markdown formatting)
  - Optional media (photo, video, document, audio)
  - Optional inline URL buttons (label | url, one per line)
  - Custom repeat interval (e.g. 30m, 2h, 1d)
  - Multiple repeating messages per group (up to 10)
  - Edit and delete at any time via inline menu

Commands
--------
/repeat              — Open the repeat message manager
/repeat list         — List all repeating messages
/repeat stop <id>    — Stop a repeating message by ID
/repeat stopall      — Stop all repeating messages in this group

Wizard flow (triggered from menu)
----------------------------------
1. Admin taps "➕ New Repeat Message"
2. Bot asks for message text
3. Bot asks for optional media (or skip)
4. Bot asks for optional URL buttons (or skip)
5. Bot asks for repeat interval
6. Bot confirms and starts the schedule
"""

import json
import re
import uuid
from datetime import timedelta
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from utils.permissions import admin_only, ANONYMOUS_ADMIN_ID
from utils.wizard import parse_buttons, send_rich_message, skip_cancel_kb, BUTTON_HELP
from config.settings import ADMIN_IDS

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------
(
    RP_AWAIT_TEXT,
    RP_AWAIT_MEDIA,
    RP_AWAIT_BUTTONS,
    RP_AWAIT_INTERVAL,
) = range(4)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MIN_SECONDS = 60          # minimum 1 minute
_MAX_SECONDS = 30 * 86400  # maximum 30 days
_MAX_REPEATS_PER_CHAT = 10

# ---------------------------------------------------------------------------
# In-memory store
# {chat_id: {repeat_id: RepeatEntry}}
# ---------------------------------------------------------------------------
_store: dict[int, dict[str, dict]] = {}

# Wizard state per user: {user_id: {draft data}}
_drafts: dict[int, dict] = {}


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
        m = seconds // 60
        return f"{m}m"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h"
    d = seconds // 86400
    return f"{d}d"


def _format_duration_long(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second(s)"
    if seconds < 3600:
        return f"{seconds // 60} minute(s)"
    if seconds < 86400:
        return f"{seconds // 3600} hour(s)"
    return f"{seconds // 86400} day(s)"


# _parse_buttons is now imported as parse_buttons from utils.wizard


def _get_repeats(chat_id: int) -> dict[str, dict]:
    return _store.get(chat_id, {})


def _short_id(full_id: str) -> str:
    return full_id[:8]


async def _is_admin(user_id: int, chat_id: int, bot) -> bool:
    from telegram import ChatMember
    if user_id == ANONYMOUS_ADMIN_ID or user_id in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Send a repeat entry (used both for first send and scheduled repeats)
# ---------------------------------------------------------------------------

async def _send_repeat_message(bot, chat_id: int, entry: dict) -> None:
    """Delegate to shared send_rich_message utility."""
    await send_rich_message(bot, chat_id, entry)


async def _repeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """APScheduler job: send the repeating message."""
    chat_id: int = context.job.data["chat_id"]
    repeat_id: str = context.job.data["repeat_id"]

    entry = _store.get(chat_id, {}).get(repeat_id)
    if not entry:
        context.job.schedule_removal()
        return

    await _send_repeat_message(context.bot, chat_id, entry)


def _start_job(app: Application, chat_id: int, repeat_id: str, interval: int) -> None:
    app.job_queue.run_repeating(
        _repeat_job,
        interval=timedelta(seconds=interval),
        first=timedelta(seconds=interval),
        data={"chat_id": chat_id, "repeat_id": repeat_id},
        name=f"repeat_{chat_id}_{repeat_id}",
    )


def _stop_job(app: Application, chat_id: int, repeat_id: str) -> None:
    name = f"repeat_{chat_id}_{repeat_id}"
    jobs = app.job_queue.get_jobs_by_name(name)
    for job in jobs:
        job.schedule_removal()


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def _main_menu_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    repeats = _get_repeats(chat_id)
    rows = []

    if repeats:
        for rid, entry in repeats.items():
            preview = entry.get("text", "")[:28] or "[media]"
            interval = _format_duration(entry["interval"])
            rows.append([
                InlineKeyboardButton(
                    f"📌 {preview}… (every {interval})",
                    callback_data=f"rp_view:{rid}",
                ),
            ])

    rows.append([InlineKeyboardButton("➕ New Repeat Message", callback_data="rp_new")])
    if repeats:
        rows.append([InlineKeyboardButton("🗑 Stop All", callback_data="rp_stopall")])
    rows.append([InlineKeyboardButton("✖ Close", callback_data="rp_close")])
    return InlineKeyboardMarkup(rows)


def _entry_keyboard(repeat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit",   callback_data=f"rp_edit:{repeat_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"rp_delete:{repeat_id}"),
        ],
        [InlineKeyboardButton("« Back", callback_data="rp_back")],
    ])


def _entry_detail_text(entry: dict) -> str:
    lines = ["📌 *Repeat Message Details*\n"]
    preview = entry.get("text", "")
    if preview:
        lines.append(f"*Text:* {preview[:200]}")
    if entry.get("media"):
        lines.append(f"*Media:* {entry['media']['type'].capitalize()}")
    if entry.get("buttons"):
        btn_count = sum(len(r) for r in entry["buttons"])
        lines.append(f"*Buttons:* {btn_count} button(s)")
    lines.append(f"*Interval:* every {_format_duration_long(entry['interval'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /repeat command
# ---------------------------------------------------------------------------

@admin_only
async def repeat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    args = context.args

    if not args:
        repeats = _get_repeats(chat_id)
        count = len(repeats)
        header = (
            f"🔁 *Repeat Messages* — {count}/{_MAX_REPEATS_PER_CHAT} active\n"
            "Tap a message to view/edit, or add a new one:"
        )
        await msg.reply_text(
            header,
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )
        return

    sub = args[0].lower()

    if sub == "list":
        repeats = _get_repeats(chat_id)
        if not repeats:
            await msg.reply_text("No repeating messages are active in this group.")
            return
        lines = ["🔁 *Active Repeat Messages:*\n"]
        for rid, entry in repeats.items():
            preview = entry.get("text", "")[:40] or "[media only]"
            lines.append(f"• `{_short_id(rid)}` — every {_format_duration_long(entry['interval'])}\n  _{preview}_")
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if sub == "stop":
        if len(args) < 2:
            await msg.reply_text("Usage: `/repeat stop <id>`", parse_mode="Markdown")
            return
        short = args[1].lower()
        repeats = _get_repeats(chat_id)
        matched = [rid for rid in repeats if rid.startswith(short)]
        if not matched:
            await msg.reply_text(f"❌ No repeat message found with ID starting with `{short}`.", parse_mode="Markdown")
            return
        rid = matched[0]
        _stop_job(context.application, chat_id, rid)
        del _store[chat_id][rid]
        await msg.reply_text(f"✅ Repeat message `{_short_id(rid)}` stopped.", parse_mode="Markdown")
        return

    if sub == "stopall":
        repeats = _get_repeats(chat_id)
        for rid in list(repeats.keys()):
            _stop_job(context.application, chat_id, rid)
        _store.pop(chat_id, None)
        await msg.reply_text("✅ All repeat messages stopped.", parse_mode="Markdown")
        return

    await msg.reply_text(
        "Usage:\n"
        "`/repeat` — open manager\n"
        "`/repeat list` — list active messages\n"
        "`/repeat stop <id>` — stop one\n"
        "`/repeat stopall` — stop all",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback query handler (menu navigation)
# ---------------------------------------------------------------------------

async def repeat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    # Admin check for all button interactions
    if not await _is_admin(user_id, chat_id, context.bot):
        await query.answer("⛔ Only admins can manage repeat messages.", show_alert=True)
        return

    # ── Back to main menu ──
    if data == "rp_back":
        repeats = _get_repeats(chat_id)
        count = len(repeats)
        await query.edit_message_text(
            f"🔁 *Repeat Messages* — {count}/{_MAX_REPEATS_PER_CHAT} active\n"
            "Tap a message to view/edit, or add a new one:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )
        return

    # ── Close ──
    if data == "rp_close":
        await query.delete_message()
        return

    # ── Stop all ──
    if data == "rp_stopall":
        repeats = _get_repeats(chat_id)
        for rid in list(repeats.keys()):
            _stop_job(context.application, chat_id, rid)
        _store.pop(chat_id, None)
        await query.edit_message_text(
            "✅ All repeat messages stopped.\n\n"
            "🔁 *Repeat Messages* — 0 active",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )
        return

    # ── View entry ──
    if data.startswith("rp_view:"):
        rid = data.split(":", 1)[1]
        entry = _store.get(chat_id, {}).get(rid)
        if not entry:
            await query.answer("Message not found.", show_alert=True)
            return
        await query.edit_message_text(
            _entry_detail_text(entry),
            parse_mode="Markdown",
            reply_markup=_entry_keyboard(rid),
        )
        return

    # ── Delete entry ──
    if data.startswith("rp_delete:"):
        rid = data.split(":", 1)[1]
        _stop_job(context.application, chat_id, rid)
        if chat_id in _store:
            _store[chat_id].pop(rid, None)
        repeats = _get_repeats(chat_id)
        count = len(repeats)
        await query.edit_message_text(
            f"✅ Repeat message deleted.\n\n"
            f"🔁 *Repeat Messages* — {count}/{_MAX_REPEATS_PER_CHAT} active\n"
            "Tap a message to view/edit, or add a new one:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )
        return

    # ── New / Edit — start wizard via DM prompt ──
    if data in ("rp_new",) or data.startswith("rp_edit:"):
        repeats = _get_repeats(chat_id)
        if data == "rp_new" and len(repeats) >= _MAX_REPEATS_PER_CHAT:
            await query.answer(
                f"Maximum {_MAX_REPEATS_PER_CHAT} repeat messages per group.", show_alert=True
            )
            return

        editing_id = data.split(":", 1)[1] if data.startswith("rp_edit:") else None
        existing = _store.get(chat_id, {}).get(editing_id) if editing_id else None

        # Store wizard context in bot_data keyed by (chat_id, user_id)
        key = f"rp_wizard_{chat_id}_{user_id}"
        context.bot_data[key] = {
            "chat_id": chat_id,
            "editing_id": editing_id,
            "text": existing.get("text", "") if existing else "",
            "media": existing.get("media") if existing else None,
            "buttons": existing.get("buttons") if existing else None,
            "interval": existing.get("interval") if existing else None,
            "step": "text",
            "menu_message_id": query.message.message_id,
        }

        # Ask for text
        skip_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Keep existing" if existing else "⏭ Skip (media only)", callback_data=f"rp_wiz_skip_text:{chat_id}:{user_id}"),
            InlineKeyboardButton("✖ Cancel", callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}"),
        ]])

        await query.edit_message_text(
            "✏️ *Step 1/4 — Message Text*\n\n"
            "Send the text for your repeating message.\n"
            "Supports *bold*, _italic_, `code`, and links.\n\n"
            + (f"_Current: {existing['text'][:100]}_\n\n" if existing and existing.get("text") else "")
            + "Or tap Skip to keep/omit text.",
            parse_mode="Markdown",
            reply_markup=skip_kb,
        )
        return

    # ── Wizard inline skips / cancel ──
    if data.startswith("rp_wiz_skip_text:"):
        _, chat_id_s, uid_s = data.split(":")
        await _wizard_next_media(query, context, int(chat_id_s), int(uid_s))
        return

    if data.startswith("rp_wiz_skip_media:"):
        _, chat_id_s, uid_s = data.split(":")
        await _wizard_next_buttons(query, context, int(chat_id_s), int(uid_s))
        return

    if data.startswith("rp_wiz_skip_buttons:"):
        _, chat_id_s, uid_s = data.split(":")
        await _wizard_next_interval(query, context, int(chat_id_s), int(uid_s))
        return

    if data.startswith("rp_wiz_cancel:"):
        _, chat_id_s, uid_s = data.split(":")
        key = f"rp_wizard_{chat_id_s}_{uid_s}"
        context.bot_data.pop(key, None)
        await query.edit_message_text(
            "❌ Wizard cancelled.\n\n"
            "🔁 *Repeat Messages*\nTap below to manage:",
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(int(chat_id_s)),
        )
        return

    # ── Interval quick-pick ──
    if data.startswith("rp_wiz_interval:"):
        parts = data.split(":")
        chat_id_s, uid_s, secs_s = parts[1], parts[2], parts[3]
        key = f"rp_wizard_{chat_id_s}_{uid_s}"
        draft = context.bot_data.get(key)
        if not draft:
            await query.answer("Session expired. Please start again.", show_alert=True)
            return
        draft["interval"] = int(secs_s)
        await _wizard_finish(query, context, int(chat_id_s), int(uid_s))
        return


# ---------------------------------------------------------------------------
# Wizard step helpers
# ---------------------------------------------------------------------------

async def _wizard_next_media(query, context, chat_id: int, user_id: int) -> None:
    key = f"rp_wizard_{chat_id}_{user_id}"
    draft = context.bot_data.get(key, {})
    draft["step"] = "media"
    context.bot_data[key] = draft

    skip_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭ Skip / No media", callback_data=f"rp_wiz_skip_media:{chat_id}:{user_id}"),
        InlineKeyboardButton("✖ Cancel",           callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}"),
    ]])
    existing_media = draft.get("media")
    note = f"_Current: {existing_media['type']} already attached_\n\n" if existing_media else ""

    await query.edit_message_text(
        "🖼 *Step 2/4 — Media (optional)*\n\n"
        + note
        + "Send a *photo*, *video*, *document*, or *audio* file.\n"
        "Or tap Skip to send text only.",
        parse_mode="Markdown",
        reply_markup=skip_kb,
    )


async def _wizard_next_buttons(query, context, chat_id: int, user_id: int) -> None:
    key = f"rp_wizard_{chat_id}_{user_id}"
    draft = context.bot_data.get(key, {})
    draft["step"] = "buttons"
    context.bot_data[key] = draft

    skip_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭ Skip / No buttons", callback_data=f"rp_wiz_skip_buttons:{chat_id}:{user_id}"),
        InlineKeyboardButton("✖ Cancel",             callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}"),
    ]])
    existing = draft.get("buttons")
    note = ""
    if existing:
        labels = [b["text"] for row in existing for b in row]
        note = f"_Current buttons: {', '.join(labels)}_\n\n"

    await query.edit_message_text(
        "🔘 *Step 3/4 — URL Buttons (optional)*\n\n"
        + note
        + BUTTON_HELP,
        parse_mode="Markdown",
        reply_markup=skip_kb,
    )


_INTERVAL_PRESETS = [
    ("5m",  300),   ("15m", 900),   ("30m", 1800),
    ("1h",  3600),  ("2h",  7200),  ("6h",  21600),
    ("12h", 43200), ("1d",  86400), ("2d",  172800),
    ("7d",  604800),
]


async def _wizard_next_interval(query, context, chat_id: int, user_id: int) -> None:
    key = f"rp_wizard_{chat_id}_{user_id}"
    draft = context.bot_data.get(key, {})
    draft["step"] = "interval"
    context.bot_data[key] = draft

    # Build quick-pick grid (5 per row)
    preset_buttons = [
        InlineKeyboardButton(label, callback_data=f"rp_wiz_interval:{chat_id}:{user_id}:{secs}")
        for label, secs in _INTERVAL_PRESETS
    ]
    rows = [preset_buttons[i:i+5] for i in range(0, len(preset_buttons), 5)]
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}")])

    existing = draft.get("interval")
    note = f"_Current: every {_format_duration_long(existing)}_\n\n" if existing else ""

    await query.edit_message_text(
        "⏱ *Step 4/4 — Repeat Interval*\n\n"
        + note
        + "Choose how often to send this message,\n"
        "or type a custom interval (e.g. `45m`, `3h`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _wizard_finish(query, context, chat_id: int, user_id: int) -> None:
    key = f"rp_wizard_{chat_id}_{user_id}"
    draft = context.bot_data.pop(key, None)
    if not draft:
        return

    interval = draft.get("interval")
    text = draft.get("text", "").strip()
    media = draft.get("media")

    if not interval:
        await query.edit_message_text("❌ No interval set. Please start again.")
        return
    if not text and not media:
        await query.edit_message_text("❌ Message needs text or media. Please start again.")
        return

    editing_id = draft.get("editing_id")
    repeat_id = editing_id or str(uuid.uuid4())

    entry = {
        "text": text,
        "media": media,
        "buttons": draft.get("buttons"),
        "interval": interval,
    }

    # Stop old job if editing
    if editing_id:
        _stop_job(context.application, chat_id, editing_id)

    _store.setdefault(chat_id, {})[repeat_id] = entry
    _start_job(context.application, chat_id, repeat_id, interval)

    verb = "updated" if editing_id else "created"
    preview = text[:60] + "…" if len(text) > 60 else text
    media_note = f"\n📎 Media: {media['type']}" if media else ""
    btn_count = sum(len(r) for r in draft.get("buttons") or [])
    btn_note = f"\n🔘 Buttons: {btn_count}" if btn_count else ""

    await query.edit_message_text(
        f"✅ Repeat message {verb}!\n\n"
        f"📝 _{preview}_"
        f"{media_note}{btn_note}\n"
        f"⏱ Sends every *{_format_duration_long(interval)}*\n"
        f"🆔 ID: `{_short_id(repeat_id)}`\n\n"
        "🔁 *Repeat Messages*",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(chat_id),
    )

    # Send a preview immediately
    await _send_repeat_message(context.bot, chat_id, entry)


# ---------------------------------------------------------------------------
# Message handler — captures wizard input (text, media, buttons, custom interval)
# ---------------------------------------------------------------------------

async def wizard_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    user_id = msg.from_user.id
    chat_id = msg.chat_id

    # Find active wizard for this user in this chat
    key = f"rp_wizard_{chat_id}_{user_id}"
    draft = context.bot_data.get(key)
    if not draft:
        return

    step = draft.get("step")

    # ── Step: text ──
    if step == "text" and msg.text:
        draft["text"] = msg.text
        await msg.delete()

        # Reconstruct the menu message to advance to next step
        # We simulate a "skip" by editing the menu message
        skip_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Skip / No media", callback_data=f"rp_wiz_skip_media:{chat_id}:{user_id}"),
            InlineKeyboardButton("✖ Cancel",           callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}"),
        ]])
        draft["step"] = "media"
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=draft["menu_message_id"],
            text=(
                f"✅ Text saved: _{msg.text[:80]}_\n\n"
                "🖼 *Step 2/4 — Media (optional)*\n\n"
                "Send a *photo*, *video*, *document*, or *audio* file.\n"
                "Or tap Skip to send text only."
            ),
            parse_mode="Markdown",
            reply_markup=skip_kb,
        )
        return

    # ── Step: media ──
    if step == "media":
        file_id = None
        media_type = None

        if msg.photo:
            file_id = msg.photo[-1].file_id
            media_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            media_type = "video"
        elif msg.document:
            file_id = msg.document.file_id
            media_type = "document"
        elif msg.audio:
            file_id = msg.audio.file_id
            media_type = "audio"

        if file_id:
            draft["media"] = {"type": media_type, "file_id": file_id}
            await msg.delete()

            skip_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Skip / No buttons", callback_data=f"rp_wiz_skip_buttons:{chat_id}:{user_id}"),
                InlineKeyboardButton("✖ Cancel",             callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}"),
            ]])
            draft["step"] = "buttons"
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=draft["menu_message_id"],
                text=(
                    f"✅ {media_type.capitalize()} saved!\n\n"
                    "🔘 *Step 3/4 — URL Buttons (optional)*\n\n"
                    "*Vertical* — one per line:\n"
                    "`Join Channel | https://t.me/mychannel`\n\n"
                    "*Horizontal* — use `||` to put side-by-side:\n"
                    "`Left | https://a.com || Right | https://b.com`\n\n"
                    "Or tap Skip."
                ),
                parse_mode="Markdown",
                reply_markup=skip_kb,
            )
        return

    # ── Step: buttons ──
    if step == "buttons" and msg.text:
        parsed = parse_buttons(msg.text)
        await msg.delete()

        if parsed is None:
            await context.bot.send_message(
                chat_id,
                "❌ Invalid format. Examples:\n\n"
                "*Vertical (one per row):*\n"
                "`Button A | https://a.com`\n"
                "`Button B | https://b.com`\n\n"
                "*Horizontal (same row, use `||`):*\n"
                "`Left | https://a.com || Right | https://b.com`\n\n"
                "Every entry must have a label and a URL starting with `http`.",
                parse_mode="Markdown",
            )
            return

        # Serialize buttons for storage
        draft["buttons"] = [
            [{"text": btn.text, "url": btn.url} for btn in row]
            for row in parsed
        ]
        draft["step"] = "interval"

        # Build interval picker
        preset_buttons = [
            InlineKeyboardButton(label, callback_data=f"rp_wiz_interval:{chat_id}:{user_id}:{secs}")
            for label, secs in _INTERVAL_PRESETS
        ]
        rows = [preset_buttons[i:i+5] for i in range(0, len(preset_buttons), 5)]
        rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"rp_wiz_cancel:{chat_id}:{user_id}")])

        btn_labels = [b["text"] for row in draft["buttons"] for b in row]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=draft["menu_message_id"],
            text=(
                f"✅ Buttons saved: _{', '.join(btn_labels)}_\n\n"
                "⏱ *Step 4/4 — Repeat Interval*\n\n"
                "Choose how often to send this message,\n"
                "or type a custom interval (e.g. `45m`, `3h`):"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    # ── Step: interval (custom text input) ──
    if step == "interval" and msg.text:
        seconds = _parse_duration(msg.text.strip())
        await msg.delete()

        if seconds is None:
            await context.bot.send_message(
                chat_id,
                f"❌ Invalid interval. Use format like `5m`, `2h`, `1d`.\n"
                f"Range: 1 minute – 30 days.",
                parse_mode="Markdown",
            )
            return

        draft["interval"] = seconds

        # Finish — simulate a callback query finish by constructing a fake one
        # We'll just call the finish logic directly
        text = draft.get("text", "").strip()
        media = draft.get("media")

        if not text and not media:
            await context.bot.send_message(chat_id, "❌ Message needs text or media.")
            context.bot_data.pop(key, None)
            return

        editing_id = draft.get("editing_id")
        repeat_id = editing_id or str(uuid.uuid4())
        entry = {
            "text": text,
            "media": media,
            "buttons": draft.get("buttons"),
            "interval": seconds,
        }

        if editing_id:
            _stop_job(context.application, chat_id, editing_id)

        _store.setdefault(chat_id, {})[repeat_id] = entry
        _start_job(context.application, chat_id, repeat_id, seconds)
        context.bot_data.pop(key, None)

        verb = "updated" if editing_id else "created"
        preview = text[:60] + "…" if len(text) > 60 else text

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=draft["menu_message_id"],
            text=(
                f"✅ Repeat message {verb}!\n\n"
                f"📝 _{preview}_\n"
                f"⏱ Sends every *{_format_duration_long(seconds)}*\n"
                f"🆔 ID: `{_short_id(repeat_id)}`\n\n"
                "🔁 *Repeat Messages*"
            ),
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(chat_id),
        )

        await _send_repeat_message(context.bot, chat_id, entry)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_repeat_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("repeat", repeat_command))
    app.add_handler(CallbackQueryHandler(repeat_callback, pattern=r"^rp_"))

    # Wizard message capture — high priority, all content types
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT | filters.AUDIO)
            & ~filters.COMMAND,
            wizard_message_handler,
        ),
        group=1,
    )
