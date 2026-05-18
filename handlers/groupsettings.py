"""
Group Settings & Broadcast Handler
====================================

Feature 1 — Service Message Deletion
--------------------------------------
Admins can toggle auto-deletion of Telegram's own service messages:
  - "User joined via invite link"
  - "User pinned a message"
  - "User changed the group photo"
  - Video chat started/ended, etc.

Commands: /servicemsg  (inline toggle menu)

Feature 2 — Hyperlink Filter
-------------------------------
Admins can block messages containing URLs/hyperlinks from non-admin members.
Connected chat members (linked channel subscribers) are exempt.

Commands: /hyperlinkfilter  (inline toggle menu)

Feature 3 — Broadcast
------------------------
Bot owner (ADMIN_IDS) can send a single message to ALL groups the bot
is an admin in. Supports text + media + URL buttons via a wizard,
exactly like the repeat message wizard. Optionally schedule repeating
broadcasts too.

Commands: /broadcast  (owner only — wizard)
          /broadcast status  — show last broadcast info
          /broadcast cancel  — cancel a pending wizard
"""

import re
import time
from datetime import timedelta
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from utils.permissions import admin_only, ANONYMOUS_ADMIN_ID
from utils.wizard import parse_buttons, send_rich_message, skip_cancel_kb, BUTTON_HELP
from utils.database import (
    get_group_settings,
    save_group_settings,
    upsert_known_chat,
    get_all_known_chats,
)
from config.settings import ADMIN_IDS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _is_admin(user_id: int, chat_id: int, bot) -> bool:
    if user_id == ANONYMOUS_ADMIN_ID or user_id in ADMIN_IDS:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


def _bool_icon(val: int | bool) -> str:
    return "✅" if val else "❌"


# ---------------------------------------------------------------------------
# Feature 1 — Service Message Deletion
# ---------------------------------------------------------------------------

SERVICE_MSG_FILTERS = (
    filters.StatusUpdate.NEW_CHAT_MEMBERS
    | filters.StatusUpdate.LEFT_CHAT_MEMBER
    | filters.StatusUpdate.NEW_CHAT_TITLE
    | filters.StatusUpdate.NEW_CHAT_PHOTO
    | filters.StatusUpdate.DELETE_CHAT_PHOTO
    | filters.StatusUpdate.PINNED_MESSAGE
    | filters.StatusUpdate.VIDEO_CHAT_STARTED
    | filters.StatusUpdate.VIDEO_CHAT_ENDED
    | filters.StatusUpdate.VIDEO_CHAT_PARTICIPANTS_INVITED
)


def _servicemsg_keyboard(chat_id: int, on: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔴 Turn OFF" if on else "🟢 Turn ON"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"gs_svc_toggle:{chat_id}")],
        [InlineKeyboardButton("✖ Close",    callback_data=f"gs_close:{chat_id}")],
    ])


@admin_only
async def servicemsg_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    s = await get_group_settings(chat_id)
    on = bool(s["delete_service_msgs"])
    icon = _bool_icon(on)
    await msg.reply_text(
        f"🗑 *Service Message Deletion*\n\n"
        f"Status: {icon} {'ON — Telegram service messages are auto-deleted' if on else 'OFF — service messages are kept'}\n\n"
        f"*What gets deleted when ON:*\n"
        f"• 'User joined via invite link'\n"
        f"• 'User pinned a message'\n"
        f"• 'Group photo changed'\n"
        f"• 'Video chat started/ended'\n"
        f"• 'User left the group'\n"
        f"• Other Telegram system notifications\n\n"
        f"_Note: The bot's own welcome/farewell messages are NOT affected._",
        parse_mode="Markdown",
        reply_markup=_servicemsg_keyboard(chat_id, on),
    )


async def delete_service_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently delete Telegram service messages if enabled for this chat."""
    msg = update.message
    if not msg:
        return
    s = await get_group_settings(msg.chat_id)
    if not s["delete_service_msgs"]:
        return
    try:
        await msg.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Feature 2 — Hyperlink Filter
# ---------------------------------------------------------------------------

# Regex: detects http(s) URLs and bare domain-like links
_URL_RE = re.compile(
    r"(https?://\S+|www\.\S+\.\S+|\b\w+\.\w{2,}(?:/\S*)?)",
    re.IGNORECASE,
)
# Also catches Markdown/HTML hyperlink entities
_ENTITY_LINK_TYPES = {"url", "text_link"}


def _hyperlinkfilter_keyboard(chat_id: int, on: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔴 Turn OFF" if on else "🟢 Turn ON"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"gs_hl_toggle:{chat_id}")],
        [InlineKeyboardButton("✖ Close",    callback_data=f"gs_close:{chat_id}")],
    ])


@admin_only
async def hyperlinkfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    s = await get_group_settings(chat_id)
    on = bool(s["delete_hyperlinks"])
    icon = _bool_icon(on)
    await msg.reply_text(
        f"🔗 *Hyperlink Filter*\n\n"
        f"Status: {icon} {'ON — links from non-admins are deleted' if on else 'OFF — links are allowed'}\n\n"
        f"*Exempt from filtering:*\n"
        f"• Group admins (including anonymous)\n"
        f"• Bot owner (ADMIN\\_IDS)\n"
        f"• Members of the linked/connected channel\n\n"
        f"*Catches:*\n"
        f"• `https://` and `http://` URLs\n"
        f"• Inline hyperlinks (clickable text)\n"
        f"• `www.` links and bare domains",
        parse_mode="Markdown",
        reply_markup=_hyperlinkfilter_keyboard(chat_id, on),
    )


async def hyperlink_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete messages with links from non-admin, non-connected-channel members."""
    msg = update.message
    if not msg or not msg.from_user:
        return

    user = msg.from_user
    if user.is_bot:
        return

    chat_id = msg.chat_id
    s = await get_group_settings(chat_id)
    if not s["delete_hyperlinks"]:
        return

    # Check if message actually has a link
    has_link = False
    if msg.text or msg.caption:
        text = msg.text or msg.caption or ""
        if _URL_RE.search(text):
            has_link = True
    if not has_link and msg.entities:
        for ent in msg.entities:
            if ent.type in _ENTITY_LINK_TYPES:
                has_link = True
                break
    if not has_link and msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type in _ENTITY_LINK_TYPES:
                has_link = True
                break

    if not has_link:
        return

    # Exempt admins
    if await _is_admin(user.id, chat_id, context.bot):
        return

    # Exempt linked/connected channel members
    try:
        chat = await context.bot.get_chat(chat_id)
        if chat.linked_chat_id:
            member = await context.bot.get_chat_member(chat.linked_chat_id, user.id)
            if member.status not in (ChatMember.LEFT, ChatMember.BANNED):
                return
    except Exception:
        pass

    # Delete and warn
    try:
        await msg.delete()
    except Exception:
        return

    warning = await context.bot.send_message(
        chat_id,
        f"🔗 {user.mention_html()}, links are not allowed here.",
        parse_mode=ParseMode.HTML,
    )
    # Auto-delete the warning after 8 seconds
    context.job_queue.run_once(
        lambda ctx: ctx.bot.delete_message(chat_id, warning.message_id),
        when=timedelta(seconds=8),
        name=f"hl_warn_{chat_id}_{warning.message_id}",
    )


# ---------------------------------------------------------------------------
# Feature 3 — Broadcast (owner only)
# ---------------------------------------------------------------------------

_BROADCAST_INTERVAL_PRESETS = [
    ("1h", 3600), ("6h", 21600), ("12h", 43200),
    ("1d", 86400), ("3d", 259200), ("7d", 604800),
]

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration(raw: str) -> Optional[int]:
    match = re.fullmatch(r"(\d+)([smhd])", raw.strip().lower())
    if not match:
        return None
    v, u = int(match.group(1)), match.group(2)
    return v * _UNITS[u]


def _owner_only(func):
    """Only ADMIN_IDS can run this command."""
    import functools
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Only the bot owner can use this command.")
            return
        return await func(update, context, *a, **kw)
    return wrapper


def _bc_draft_key(user_id: int) -> str:
    return f"bc_wizard_{user_id}"


async def _do_broadcast(bot, entry: dict, chats: list[dict]) -> tuple[int, int]:
    """Send entry to all chats. Returns (success, fail) counts."""
    ok = fail = 0
    for chat in chats:
        try:
            await send_rich_message(bot, chat["chat_id"], entry)
            ok += 1
        except Exception:
            fail += 1
    return ok, fail


async def _broadcast_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    entry = context.job.data["entry"]
    chats = await get_all_known_chats(admin_only=True)
    await _do_broadcast(context.bot, entry, chats)


@_owner_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_id = msg.from_user.id
    args = context.args

    if args and args[0].lower() == "cancel":
        context.bot_data.pop(_bc_draft_key(user_id), None)
        # Cancel any repeating broadcast
        for job in context.job_queue.get_jobs_by_name(f"broadcast_{user_id}"):
            job.schedule_removal()
        await msg.reply_text("✅ Broadcast wizard and any scheduled broadcast cancelled.")
        return

    if args and args[0].lower() == "status":
        draft = context.bot_data.get(_bc_draft_key(user_id))
        jobs  = context.job_queue.get_jobs_by_name(f"broadcast_{user_id}")
        if not draft and not jobs:
            await msg.reply_text("No active broadcast wizard or scheduled broadcast.")
        else:
            lines = []
            if draft:
                lines.append(f"📝 *Wizard in progress* — step: `{draft.get('step','?')}`")
            if jobs:
                lines.append(f"🔁 *Repeating broadcast active* — {len(jobs)} job(s) running")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Start wizard
    chats = await get_all_known_chats(admin_only=True)
    if not chats:
        await msg.reply_text(
            "⚠️ No groups found yet.\n"
            "The bot needs to be active in groups first — it learns about groups as members send messages."
        )
        return

    draft = {
        "step":    "text",
        "text":    "",
        "media":   None,
        "buttons": None,
        "repeat":  False,
    }
    context.bot_data[_bc_draft_key(user_id)] = draft

    sent = await msg.reply_text(
        f"📢 *Broadcast Wizard*\n\n"
        f"Will send to *all {len(chats)} group(s)* the bot is in.\n"
        f"The message is sent immediately, then repeats at your chosen interval.\n\n"
        f"✏️ *Step 1/4 — Message Text*\n\n"
        f"Send the text for your broadcast message.\n"
        f"Supports *bold*, _italic_, `code`, and links.\n\n"
        f"Or tap Skip for media-only.",
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"bc_skip_text:{user_id}",
            f"bc_cancel:{user_id}",
        ),
    )
    draft["menu_msg_id"] = sent.message_id
    draft["chat_id"]     = msg.chat_id


async def _bc_step_media(bot, draft: dict) -> None:
    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_msg_id"],
        text=(
            "🖼 *Step 2/4 — Media (optional)*\n\n"
            "Send a *photo*, *video*, *document*, or *audio*.\n"
            "Or tap Skip."
        ),
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"bc_skip_media:{draft['user_id']}",
            f"bc_cancel:{draft['user_id']}",
        ),
    )


async def _bc_step_buttons(bot, draft: dict) -> None:
    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_msg_id"],
        text=(
            "🔘 *Step 3/4 — URL Buttons (optional)*\n\n"
            + BUTTON_HELP
        ),
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"bc_skip_buttons:{draft['user_id']}",
            f"bc_cancel:{draft['user_id']}",
        ),
    )


async def _bc_step_repeat(bot, draft: dict) -> None:
    preset_btns = [
        InlineKeyboardButton(label, callback_data=f"bc_interval:{draft['user_id']}:{secs}")
        for label, secs in _BROADCAST_INTERVAL_PRESETS
    ]
    rows = [preset_btns[i:i+3] for i in range(0, len(preset_btns), 3)]
    rows.append([
        InlineKeyboardButton("✖ Cancel", callback_data=f"bc_cancel:{draft['user_id']}"),
    ])
    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_msg_id"],
        text=(
            "⏱ *Step 4/4 — Repeat Interval*\n\n"
            "The message will be sent to *all groups immediately*, "
            "then repeated automatically at the chosen interval.\n\n"
            "Choose a preset or type a custom interval (e.g. `2h`, `3d`).\n\n"
            "_Use /broadcast cancel anytime to stop the repeat._"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _bc_finish(bot, context, draft: dict, interval: Optional[int] = None) -> None:
    user_id = draft["user_id"]
    entry = {
        "text":    draft.get("text", "").strip(),
        "media":   draft.get("media"),
        "buttons": draft.get("buttons"),
    }

    if not entry["text"] and not entry["media"]:
        await bot.send_message(draft["chat_id"], "❌ Broadcast needs text or media. Cancelled.")
        context.bot_data.pop(_bc_draft_key(user_id), None)
        return

    chats = await get_all_known_chats(admin_only=False)
    ok, fail = await _do_broadcast(bot, entry, chats)

    repeat_note = ""
    if interval:
        # Cancel any existing broadcast job first
        for job in context.job_queue.get_jobs_by_name(f"broadcast_{user_id}"):
            job.schedule_removal()
        context.job_queue.run_repeating(
            _broadcast_job,
            interval=timedelta(seconds=interval),
            first=timedelta(seconds=interval),
            data={"entry": entry},
            name=f"broadcast_{user_id}",
        )
        from handlers.activitylog import _format_duration_long  # avoid circular if any
        h = interval // 3600
        m = (interval % 3600) // 60
        d = interval // 86400
        if d:
            dur = f"{d} day(s)"
        elif h:
            dur = f"{h} hour(s)"
        else:
            dur = f"{m} minute(s)"
        repeat_note = f"\n🔁 Will repeat every *{dur}*.\nUse `/broadcast cancel` to stop."

    context.bot_data.pop(_bc_draft_key(user_id), None)

    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_msg_id"],
        text=(
            f"✅ *Broadcast sent!*\n\n"
            f"📤 Delivered to: `{ok}` group(s)\n"
            f"❌ Failed: `{fail}` group(s)"
            + repeat_note
        ),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback handler (settings toggles + broadcast wizard)
# ---------------------------------------------------------------------------

async def gs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    parts = data.split(":")

    action = parts[0]

    # ── gs_close ──
    if action == "gs_close":
        await query.delete_message()
        return

    # ── gs_svc_toggle:<chat_id> ──
    if action == "gs_svc_toggle":
        chat_id = int(parts[1])
        if not await _is_admin(query.from_user.id, chat_id, context.bot):
            await query.answer("⛔ Admins only.", show_alert=True)
            return
        s = await get_group_settings(chat_id)
        s["delete_service_msgs"] = 0 if s["delete_service_msgs"] else 1
        await save_group_settings(chat_id, s)
        on = bool(s["delete_service_msgs"])
        icon = _bool_icon(on)
        await query.edit_message_text(
            f"🗑 *Service Message Deletion*\n\n"
            f"Status: {icon} {'ON' if on else 'OFF'}\n\n"
            f"*What gets deleted when ON:*\n"
            f"• Join/leave system messages\n"
            f"• Pinned message notifications\n"
            f"• Group photo/title changes\n"
            f"• Video chat started/ended\n\n"
            f"_Welcome/farewell messages are NOT affected._",
            parse_mode="Markdown",
            reply_markup=_servicemsg_keyboard(chat_id, on),
        )
        return

    # ── gs_hl_toggle:<chat_id> ──
    if action == "gs_hl_toggle":
        chat_id = int(parts[1])
        if not await _is_admin(query.from_user.id, chat_id, context.bot):
            await query.answer("⛔ Admins only.", show_alert=True)
            return
        s = await get_group_settings(chat_id)
        s["delete_hyperlinks"] = 0 if s["delete_hyperlinks"] else 1
        await save_group_settings(chat_id, s)
        on = bool(s["delete_hyperlinks"])
        icon = _bool_icon(on)
        await query.edit_message_text(
            f"🔗 *Hyperlink Filter*\n\n"
            f"Status: {icon} {'ON — links from non-admins are deleted' if on else 'OFF — links are allowed'}\n\n"
            f"*Exempt:* admins, bot owner, linked channel members",
            parse_mode="Markdown",
            reply_markup=_hyperlinkfilter_keyboard(chat_id, on),
        )
        return

    # ── Broadcast wizard callbacks ──
    user_id = int(parts[1]) if len(parts) > 1 else None

    if action == "bc_cancel":
        context.bot_data.pop(_bc_draft_key(user_id), None)
        await query.edit_message_text("❌ Broadcast cancelled.")
        return

    draft = context.bot_data.get(_bc_draft_key(user_id))
    if not draft:
        await query.answer("Session expired. Run /broadcast again.", show_alert=True)
        return

    if action == "bc_skip_text":
        draft["step"] = "media"
        await _bc_step_media(context.bot, draft)
        return

    if action == "bc_skip_media":
        draft["step"] = "buttons"
        await _bc_step_buttons(context.bot, draft)
        return

    if action == "bc_skip_buttons":
        draft["step"] = "repeat"
        await _bc_step_repeat(context.bot, draft)
        return

    if action == "bc_interval":
        interval = int(parts[2])
        await _bc_finish(context.bot, context, draft, interval=interval)
        return


# ---------------------------------------------------------------------------
# Message handler — captures broadcast wizard input
# ---------------------------------------------------------------------------

async def bc_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.from_user:
        return

    user_id = msg.from_user.id
    if user_id not in ADMIN_IDS:
        return

    key   = _bc_draft_key(user_id)
    draft = context.bot_data.get(key)
    if not draft:
        return

    draft["user_id"] = user_id
    step = draft.get("step")

    # ── text ──
    if step == "text" and msg.text:
        draft["text"] = msg.text
        draft["step"] = "media"
        await msg.delete()
        await _bc_step_media(context.bot, draft)
        return

    # ── media ──
    if step == "media":
        fid = mtype = None
        if msg.photo:
            fid, mtype = msg.photo[-1].file_id, "photo"
        elif msg.video:
            fid, mtype = msg.video.file_id, "video"
        elif msg.document:
            fid, mtype = msg.document.file_id, "document"
        elif msg.audio:
            fid, mtype = msg.audio.file_id, "audio"
        if fid:
            draft["media"] = {"type": mtype, "file_id": fid}
            draft["step"]  = "buttons"
            await msg.delete()
            await _bc_step_buttons(context.bot, draft)
        return

    # ── buttons ──
    if step == "buttons" and msg.text:
        parsed = parse_buttons(msg.text)
        await msg.delete()
        if parsed is None:
            await context.bot.send_message(
                draft["chat_id"],
                "❌ Invalid button format.\n\n" + BUTTON_HELP,
                parse_mode="Markdown",
            )
            return
        draft["buttons"] = parsed
        draft["step"]    = "repeat"
        await _bc_step_repeat(context.bot, draft)
        return

    # ── custom interval text ──
    if step == "repeat" and msg.text:
        secs = _parse_duration(msg.text.strip())
        await msg.delete()
        if secs is None or secs < 60:
            await context.bot.send_message(
                draft["chat_id"],
                "❌ Invalid interval. Use format like `1h`, `2d`. Minimum 1 minute.",
                parse_mode="Markdown",
            )
            return
        await _bc_finish(context.bot, context, draft, interval=secs)


# ---------------------------------------------------------------------------
# Chat tracker — register every group the bot is active in
# ---------------------------------------------------------------------------

async def track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Passively records every group the bot sees messages in."""
    msg = update.message or update.edited_message
    if not msg:
        return
    chat = msg.chat
    if chat.type not in ("group", "supergroup"):
        return
    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        is_admin = bot_member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        is_admin = False
    await upsert_known_chat(chat.id, chat.title or "", is_admin)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_group_settings_handlers(app: Application) -> None:
    # Service message deletion
    app.add_handler(CommandHandler("servicemsg",       servicemsg_command))
    app.add_handler(CommandHandler("servicemsgs",      servicemsg_command))

    # Hyperlink filter
    app.add_handler(CommandHandler("hyperlinkfilter",  hyperlinkfilter_command))
    app.add_handler(CommandHandler("linkfilter",       hyperlinkfilter_command))

    # Broadcast (owner only)
    app.add_handler(CommandHandler("broadcast",        broadcast_command))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(gs_callback, pattern=r"^(gs_|bc_)"))

    # Passive: hyperlink filter on all non-command messages (group=10)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            hyperlink_filter,
        ),
        group=10,
    )

    # Passive: delete service messages (group=10)
    app.add_handler(
        MessageHandler(SERVICE_MSG_FILTERS, delete_service_msg),
        group=10,
    )

    # Passive: broadcast wizard input capture (private chat, owner only)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT | filters.AUDIO)
            & ~filters.COMMAND,
            bc_wizard_message,
        ),
        group=3,
    )

    # Passive: track all groups (lowest priority)
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, track_chat),
        group=100,
    )
