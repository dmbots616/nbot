from datetime import timedelta

from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from config.settings import BANNED_WORDS, MAX_WARNINGS
from utils.permissions import admin_only, bot_can_restrict

# In-memory warning store: {chat_id: {user_id: count}}
_warnings: dict[int, dict[int, int]] = {}


def _get_warnings(chat_id: int, user_id: int) -> int:
    return _warnings.get(chat_id, {}).get(user_id, 0)


def _add_warning(chat_id: int, user_id: int) -> int:
    _warnings.setdefault(chat_id, {})
    _warnings[chat_id][user_id] = _warnings[chat_id].get(user_id, 0) + 1
    return _warnings[chat_id][user_id]


def _clear_warnings(chat_id: int, user_id: int) -> None:
    if chat_id in _warnings:
        _warnings[chat_id].pop(user_id, None)


async def filter_banned_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-delete messages containing banned words and warn the sender."""
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.lower()
    if not any(word in text for word in BANNED_WORDS):
        return

    await msg.delete()

    user = msg.from_user
    chat_id = msg.chat_id
    count = _add_warning(chat_id, user.id)

    if count >= MAX_WARNINGS:
        await context.bot.ban_chat_member(chat_id, user.id)
        _clear_warnings(chat_id, user.id)
        await context.bot.send_message(
            chat_id,
            f"🚫 {user.mention_html()} has been banned after {MAX_WARNINGS} warnings.",
            parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id,
            f"⚠️ {user.mention_html()}, that message was removed.\n"
            f"Warning {count}/{MAX_WARNINGS}.",
            parse_mode="HTML",
        )


@admin_only
@bot_can_restrict
async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mute a replied-to user. Usage: /mute [minutes]"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to mute that user.")
        return

    target = msg.reply_to_message.from_user
    minutes = int(context.args[0]) if context.args and context.args[0].isdigit() else 10
    until = timedelta(minutes=minutes)

    await context.bot.restrict_chat_member(
        msg.chat_id,
        target.id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=until,
    )
    await msg.reply_html(f"🔇 {target.mention_html()} has been muted for {minutes} minute(s).")


@admin_only
@bot_can_restrict
async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unmute a replied-to user. Usage: /unmute"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to unmute that user.")
        return

    target = msg.reply_to_message.from_user
    await context.bot.restrict_chat_member(
        msg.chat_id,
        target.id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        ),
    )
    await msg.reply_html(f"🔊 {target.mention_html()} has been unmuted.")


@admin_only
@bot_can_restrict
async def kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick a replied-to user. Usage: /kick"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to kick that user.")
        return

    target = msg.reply_to_message.from_user
    await context.bot.ban_chat_member(msg.chat_id, target.id)
    await context.bot.unban_chat_member(msg.chat_id, target.id)  # unban so they can rejoin
    await msg.reply_html(f"👢 {target.mention_html()} has been kicked.")


@admin_only
@bot_can_restrict
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Permanently ban a replied-to user. Usage: /ban"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to ban that user.")
        return

    target = msg.reply_to_message.from_user
    await context.bot.ban_chat_member(msg.chat_id, target.id)
    _clear_warnings(msg.chat_id, target.id)
    await msg.reply_html(f"🚫 {target.mention_html()} has been permanently banned.")


@admin_only
@bot_can_restrict
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user by ID. Usage: /unban <user_id>"""
    msg = update.message
    if not context.args:
        await msg.reply_text("Usage: /unban <user_id>")
        return

    user_id = int(context.args[0])
    await context.bot.unban_chat_member(msg.chat_id, user_id)
    await msg.reply_text(f"✅ User {user_id} has been unbanned.")


@admin_only
async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually warn a replied-to user. Usage: /warn"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to warn that user.")
        return

    target = msg.reply_to_message.from_user
    count = _add_warning(msg.chat_id, target.id)

    if count >= MAX_WARNINGS:
        await context.bot.ban_chat_member(msg.chat_id, target.id)
        _clear_warnings(msg.chat_id, target.id)
        await msg.reply_html(
            f"🚫 {target.mention_html()} reached {MAX_WARNINGS} warnings and has been banned."
        )
    else:
        await msg.reply_html(
            f"⚠️ {target.mention_html()} has been warned. ({count}/{MAX_WARNINGS})"
        )


@admin_only
async def clear_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear warnings for a replied-to user. Usage: /clearwarns"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to clear that user's warnings.")
        return

    target = msg.reply_to_message.from_user
    _clear_warnings(msg.chat_id, target.id)
    await msg.reply_html(f"✅ Warnings cleared for {target.mention_html()}.")


def register_moderation_handlers(app: Application) -> None:
    if BANNED_WORDS:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_banned_words))

    app.add_handler(CommandHandler("mute", mute_user))
    app.add_handler(CommandHandler("unmute", unmute_user))
    app.add_handler(CommandHandler("kick", kick_user))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("warn", warn_user))
    app.add_handler(CommandHandler("clearwarns", clear_warnings))
