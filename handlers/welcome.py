"""
Welcome & Farewell Handler
===========================
Fully customisable welcome and farewell messages per group.

Features
--------
- Custom text with live variables: {name} {username} {mention} {group} {count} {id}
- Optional media: photo, video, document, audio
- Optional URL buttons: vertical, horizontal (||), or mixed
- Enable / disable independently for welcome and farewell
- Edit anytime via interactive inline-keyboard wizard
- Preview before saving

Commands
--------
/setwelcome     — Open the welcome message wizard
/setfarewell    — Open the farewell message wizard
/welcome        — Show current welcome settings & on/off toggle
/farewell       — Show current farewell settings & on/off toggle
/delwelcome     — Delete (reset) the custom welcome message
/delfarewell    — Delete (reset) the custom farewell message

Variables available inside message text
----------------------------------------
{name}      → Member's full name
{username}  → @username (or full name if no username)
{mention}   → Clickable HTML mention
{group}     → Group title
{count}     → Current member count
{id}        → Member's Telegram user ID
"""

import uuid
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from utils.permissions import admin_only, ANONYMOUS_ADMIN_ID
from utils.wizard import (
    parse_buttons,
    send_rich_message,
    skip_cancel_kb,
    substitute_vars,
    BUTTON_HELP,
    SUPPORTED_VARS,
)
from config.settings import ADMIN_IDS

# ---------------------------------------------------------------------------
# In-memory store
# {chat_id: {"welcome": entry|None, "farewell": entry|None,
#            "welcome_on": bool, "farewell_on": bool}}
# ---------------------------------------------------------------------------
_store: dict[int, dict] = {}


def _get(chat_id: int) -> dict:
    return _store.setdefault(chat_id, {
        "welcome":     None,
        "farewell":    None,
        "welcome_on":  True,
        "farewell_on": True,
    })


# ---------------------------------------------------------------------------
# Admin guard for callbacks
# ---------------------------------------------------------------------------

async def _is_admin(user_id: int, chat_id: int, bot) -> bool:
    from telegram import ChatMember
    if user_id == ANONYMOUS_ADMIN_ID or user_id in ADMIN_IDS:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Status / info helpers
# ---------------------------------------------------------------------------

def _entry_summary(entry: dict | None) -> str:
    if not entry:
        return "_Not set — default message will be used._"
    lines = []
    if entry.get("text"):
        lines.append(f"📝 Text: _{entry['text'][:80]}_")
    if entry.get("media"):
        lines.append(f"📎 Media: {entry['media']['type'].capitalize()}")
    if entry.get("buttons"):
        n = sum(len(r) for r in entry["buttons"])
        lines.append(f"🔘 Buttons: {n}")
    return "\n".join(lines) if lines else "_Empty entry_"


def _status_text(chat_id: int, kind: str) -> str:
    d = _get(chat_id)
    on = d.get(f"{kind}_on", True)
    entry = d.get(kind)
    icon = "✅" if on else "❌"
    title = "Welcome" if kind == "welcome" else "Farewell"
    return (
        f"{icon} *{title} message is {'ON' if on else 'OFF'}*\n\n"
        + _entry_summary(entry)
    )


def _status_keyboard(chat_id: int, kind: str) -> InlineKeyboardMarkup:
    d = _get(chat_id)
    on = d.get(f"{kind}_on", True)
    toggle_label = "🔴 Turn OFF" if on else "🟢 Turn ON"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ Edit message",   callback_data=f"wf_edit:{kind}:{chat_id}"),
         InlineKeyboardButton(toggle_label,         callback_data=f"wf_toggle:{kind}:{chat_id}")],
        [InlineKeyboardButton("👁 Preview",         callback_data=f"wf_preview:{kind}:{chat_id}"),
         InlineKeyboardButton("🗑 Reset to default", callback_data=f"wf_reset:{kind}:{chat_id}")],
        [InlineKeyboardButton("✖ Close",            callback_data=f"wf_close:{chat_id}")],
    ])


# ---------------------------------------------------------------------------
# Default messages (used when no custom entry is set)
# ---------------------------------------------------------------------------

_DEFAULT_WELCOME = {
    "text": "👋 Welcome to {group}, {mention}!\nPlease read the rules. Enjoy your stay 🎉",
    "media": None,
    "buttons": None,
}

_DEFAULT_FAREWELL = {
    "text": "👋 {name} has left the group. Goodbye!",
    "media": None,
    "buttons": None,
}


# ---------------------------------------------------------------------------
# /welcome  and  /farewell  — show status
# ---------------------------------------------------------------------------

@admin_only
async def welcome_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    await update.message.reply_text(
        _status_text(chat_id, "welcome"),
        parse_mode="Markdown",
        reply_markup=_status_keyboard(chat_id, "welcome"),
    )


@admin_only
async def farewell_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    await update.message.reply_text(
        _status_text(chat_id, "farewell"),
        parse_mode="Markdown",
        reply_markup=_status_keyboard(chat_id, "farewell"),
    )


# ---------------------------------------------------------------------------
# /setwelcome  and  /setfarewell  — launch wizard directly
# ---------------------------------------------------------------------------

@admin_only
async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    sent = await msg.reply_text(
        "🧙 *Welcome Message Wizard*\n\n"
        "✏️ *Step 1/3 — Message Text*\n\n"
        "Send the text for your welcome message.\n\n"
        "*Available variables:*\n"
        + "\n".join(f"`{k}` — {v}" for k, v in SUPPORTED_VARS.items())
        + "\n\nOr tap Skip to keep existing / use media only.",
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"wf_wiz_skip_text:welcome:{chat_id}:{msg.from_user.id}",
            f"wf_wiz_cancel:welcome:{chat_id}:{msg.from_user.id}",
        ),
    )
    _init_draft(context, chat_id, msg.from_user.id, "welcome", sent.message_id)


@admin_only
async def set_farewell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id
    sent = await msg.reply_text(
        "🧙 *Farewell Message Wizard*\n\n"
        "✏️ *Step 1/3 — Message Text*\n\n"
        "Send the text for your farewell message.\n\n"
        "*Available variables:*\n"
        + "\n".join(f"`{k}` — {v}" for k, v in SUPPORTED_VARS.items())
        + "\n\nOr tap Skip.",
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"wf_wiz_skip_text:farewell:{chat_id}:{msg.from_user.id}",
            f"wf_wiz_cancel:farewell:{chat_id}:{msg.from_user.id}",
        ),
    )
    _init_draft(context, chat_id, msg.from_user.id, "farewell", sent.message_id)


# ---------------------------------------------------------------------------
# /delwelcome  /delfarewell
# ---------------------------------------------------------------------------

@admin_only
async def del_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _get(update.message.chat_id)["welcome"] = None
    await update.message.reply_text("✅ Welcome message reset to default.")


@admin_only
async def del_farewell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _get(update.message.chat_id)["farewell"] = None
    await update.message.reply_text("✅ Farewell message reset to default.")


# ---------------------------------------------------------------------------
# Wizard helpers
# ---------------------------------------------------------------------------

def _draft_key(chat_id: int, user_id: int, kind: str) -> str:
    return f"wf_wizard_{kind}_{chat_id}_{user_id}"


def _init_draft(context, chat_id: int, user_id: int, kind: str, menu_msg_id: int) -> None:
    existing = _get(chat_id).get(kind) or {}
    key = _draft_key(chat_id, user_id, kind)
    context.bot_data[key] = {
        "kind":           kind,
        "chat_id":        chat_id,
        "user_id":        user_id,
        "step":           "text",
        "menu_message_id": menu_msg_id,
        "text":           existing.get("text", ""),
        "media":          existing.get("media"),
        "buttons":        existing.get("buttons"),
    }


async def _wizard_step_media(bot, draft: dict) -> None:
    existing = draft.get("media")
    note = f"_Current: {existing['type']} attached_\n\n" if existing else ""
    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_message_id"],
        text=(
            f"🖼 *Step 2/3 — Media (optional)*\n\n{note}"
            "Send a *photo*, *video*, *document*, or *audio*.\n"
            "Or tap Skip."
        ),
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"wf_wiz_skip_media:{draft['kind']}:{draft['chat_id']}:{draft['user_id']}",
            f"wf_wiz_cancel:{draft['kind']}:{draft['chat_id']}:{draft['user_id']}",
        ),
    )


async def _wizard_step_buttons(bot, draft: dict) -> None:
    existing = draft.get("buttons")
    note = ""
    if existing:
        labels = [b["text"] for row in existing for b in row]
        note = f"_Current buttons: {', '.join(labels)}_\n\n"
    await bot.edit_message_text(
        chat_id=draft["chat_id"],
        message_id=draft["menu_message_id"],
        text=(
            f"🔘 *Step 3/3 — URL Buttons (optional)*\n\n{note}"
            + BUTTON_HELP
        ),
        parse_mode="Markdown",
        reply_markup=skip_cancel_kb(
            f"wf_wiz_skip_buttons:{draft['kind']}:{draft['chat_id']}:{draft['user_id']}",
            f"wf_wiz_cancel:{draft['kind']}:{draft['chat_id']}:{draft['user_id']}",
        ),
    )


async def _wizard_finish(bot, context, draft: dict) -> None:
    kind    = draft["kind"]
    chat_id = draft["chat_id"]
    user_id = draft["user_id"]
    text    = draft.get("text", "").strip()
    media   = draft.get("media")

    if not text and not media:
        await bot.send_message(chat_id, "❌ Message needs at least text or media. Wizard cancelled.")
        return

    entry = {
        "text":    text,
        "media":   media,
        "buttons": draft.get("buttons"),
    }
    _get(chat_id)[kind] = entry

    title = "Welcome" if kind == "welcome" else "Farewell"
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=draft["menu_message_id"],
        text=(
            f"✅ *{title} message saved!*\n\n"
            + _entry_summary(entry)
            + f"\n\n_Use /{kind} to manage or preview it._"
        ),
        parse_mode="Markdown",
        reply_markup=_status_keyboard(chat_id, kind),
    )


# ---------------------------------------------------------------------------
# Callback query handler
# ---------------------------------------------------------------------------

async def wf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data    = query.data
    user_id = query.from_user.id

    # Parse prefix
    # All callbacks: wf_<action>:<kind?>:<chat_id>:<user_id?>
    parts = data.split(":")

    # Admin guard
    chat_id = int(parts[2]) if len(parts) > 2 else query.message.chat_id
    if not await _is_admin(user_id, chat_id, context.bot):
        await query.answer("⛔ Admins only.", show_alert=True)
        return

    action = parts[0]  # e.g. "wf_toggle"

    # ── wf_close ──
    if action == "wf_close":
        await query.delete_message()
        return

    # ── wf_toggle:<kind>:<chat_id> ──
    if action == "wf_toggle":
        kind = parts[1]
        d = _get(chat_id)
        d[f"{kind}_on"] = not d.get(f"{kind}_on", True)
        await query.edit_message_text(
            _status_text(chat_id, kind),
            parse_mode="Markdown",
            reply_markup=_status_keyboard(chat_id, kind),
        )
        return

    # ── wf_reset:<kind>:<chat_id> ──
    if action == "wf_reset":
        kind = parts[1]
        _get(chat_id)[kind] = None
        await query.edit_message_text(
            f"✅ {kind.capitalize()} message reset to default.\n\n"
            + _status_text(chat_id, kind),
            parse_mode="Markdown",
            reply_markup=_status_keyboard(chat_id, kind),
        )
        return

    # ── wf_preview:<kind>:<chat_id> ──
    if action == "wf_preview":
        kind  = parts[1]
        entry = _get(chat_id).get(kind) or (
            _DEFAULT_WELCOME if kind == "welcome" else _DEFAULT_FAREWELL
        )
        user  = query.from_user
        try:
            count = (await context.bot.get_chat_member_count(chat_id))
        except Exception:
            count = 0
        await send_rich_message(
            context.bot, chat_id, entry,
            member=user, chat=query.message.chat, member_count=count,
        )
        return

    # ── wf_edit:<kind>:<chat_id> ──
    if action == "wf_edit":
        kind = parts[1]
        _init_draft(context, chat_id, user_id, kind, query.message.message_id)
        key   = _draft_key(chat_id, user_id, kind)
        draft = context.bot_data[key]
        title = "Welcome" if kind == "welcome" else "Farewell"
        await query.edit_message_text(
            f"🧙 *{title} Message Wizard*\n\n"
            "✏️ *Step 1/3 — Message Text*\n\n"
            "Send new text, or tap Skip to keep current.\n\n"
            "*Variables:*\n"
            + "\n".join(f"`{k}` — {v}" for k, v in SUPPORTED_VARS.items()),
            parse_mode="Markdown",
            reply_markup=skip_cancel_kb(
                f"wf_wiz_skip_text:{kind}:{chat_id}:{user_id}",
                f"wf_wiz_cancel:{kind}:{chat_id}:{user_id}",
            ),
        )
        return

    # ── Wizard skips ──
    if action == "wf_wiz_skip_text":
        kind, chat_id_s, uid_s = parts[1], parts[2], parts[3]
        key = _draft_key(int(chat_id_s), int(uid_s), kind)
        draft = context.bot_data.get(key)
        if not draft:
            await query.answer("Session expired.", show_alert=True)
            return
        draft["step"] = "media"
        await _wizard_step_media(context.bot, draft)
        return

    if action == "wf_wiz_skip_media":
        kind, chat_id_s, uid_s = parts[1], parts[2], parts[3]
        key = _draft_key(int(chat_id_s), int(uid_s), kind)
        draft = context.bot_data.get(key)
        if not draft:
            await query.answer("Session expired.", show_alert=True)
            return
        draft["step"] = "buttons"
        await _wizard_step_buttons(context.bot, draft)
        return

    if action == "wf_wiz_skip_buttons":
        kind, chat_id_s, uid_s = parts[1], parts[2], parts[3]
        key = _draft_key(int(chat_id_s), int(uid_s), kind)
        draft = context.bot_data.pop(key, None)
        if not draft:
            await query.answer("Session expired.", show_alert=True)
            return
        await _wizard_finish(context.bot, context, draft)
        return

    if action == "wf_wiz_cancel":
        kind, chat_id_s, uid_s = parts[1], parts[2], parts[3]
        key = _draft_key(int(chat_id_s), int(uid_s), kind)
        context.bot_data.pop(key, None)
        await query.edit_message_text(
            "❌ Wizard cancelled.\n\n" + _status_text(int(chat_id_s), kind),
            parse_mode="Markdown",
            reply_markup=_status_keyboard(int(chat_id_s), kind),
        )
        return


# ---------------------------------------------------------------------------
# Message handler — captures wizard text/media/button input
# ---------------------------------------------------------------------------

async def wf_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    user_id = msg.from_user.id
    chat_id = msg.chat_id

    # Check for any active wizard for this user in either kind
    draft = None
    key   = None
    for kind in ("welcome", "farewell"):
        k = _draft_key(chat_id, user_id, kind)
        if k in context.bot_data:
            key   = k
            draft = context.bot_data[k]
            break

    if not draft:
        return

    step = draft.get("step")
    kind = draft["kind"]

    # ── Step: text ──
    if step == "text" and msg.text:
        draft["text"] = msg.text
        draft["step"] = "media"
        await msg.delete()
        await _wizard_step_media(context.bot, draft)
        return

    # ── Step: media ──
    if step == "media":
        file_id = media_type = None
        if msg.photo:
            file_id, media_type = msg.photo[-1].file_id, "photo"
        elif msg.video:
            file_id, media_type = msg.video.file_id, "video"
        elif msg.document:
            file_id, media_type = msg.document.file_id, "document"
        elif msg.audio:
            file_id, media_type = msg.audio.file_id, "audio"

        if file_id:
            draft["media"] = {"type": media_type, "file_id": file_id}
            draft["step"]  = "buttons"
            await msg.delete()
            await _wizard_step_buttons(context.bot, draft)
        return

    # ── Step: buttons ──
    if step == "buttons" and msg.text:
        parsed = parse_buttons(msg.text)
        await msg.delete()
        if parsed is None:
            await context.bot.send_message(
                chat_id,
                "❌ Invalid button format.\n\n" + BUTTON_HELP,
                parse_mode="Markdown",
            )
            return
        draft["buttons"] = parsed
        context.bot_data.pop(key)
        await _wizard_finish(context.bot, context, draft)
        return


# ---------------------------------------------------------------------------
# Group event handlers — fire on join / leave
# ---------------------------------------------------------------------------

async def on_member_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    d       = _get(chat_id)

    if not d.get("welcome_on", True):
        return

    entry = d.get("welcome") or _DEFAULT_WELCOME

    try:
        count = await context.bot.get_chat_member_count(chat_id)
    except Exception:
        count = 0

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        await send_rich_message(
            context.bot, chat_id, entry,
            member=member, chat=update.message.chat, member_count=count,
        )


async def on_member_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    d       = _get(chat_id)

    if not d.get("farewell_on", True):
        return

    member = update.message.left_chat_member
    if not member or member.is_bot:
        return

    entry = d.get("farewell") or _DEFAULT_FAREWELL

    try:
        count = await context.bot.get_chat_member_count(chat_id)
    except Exception:
        count = 0

    await send_rich_message(
        context.bot, chat_id, entry,
        member=member, chat=update.message.chat, member_count=count,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_welcome_handlers(app: Application) -> None:
    # Group events
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_member_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER,  on_member_leave))

    # Admin commands
    app.add_handler(CommandHandler("welcome",     welcome_status))
    app.add_handler(CommandHandler("farewell",    farewell_status))
    app.add_handler(CommandHandler("setwelcome",  set_welcome))
    app.add_handler(CommandHandler("setfarewell", set_farewell))
    app.add_handler(CommandHandler("delwelcome",  del_welcome))
    app.add_handler(CommandHandler("delfarewell", del_farewell))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(wf_callback, pattern=r"^wf_"))

    # Wizard message capture (group=2 so it runs after moderation)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT | filters.AUDIO)
            & ~filters.COMMAND,
            wf_wizard_message,
        ),
        group=2,
    )
