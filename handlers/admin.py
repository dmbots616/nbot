from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from utils.permissions import admin_only


@admin_only
async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pin the replied-to message. Usage: /pin"""
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a message to pin it.")
        return

    await context.bot.pin_chat_message(msg.chat_id, msg.reply_to_message.message_id)
    await msg.reply_text("📌 Message pinned.")


@admin_only
async def unpin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unpin the most recent pinned message. Usage: /unpin"""
    await context.bot.unpin_chat_message(update.message.chat_id)
    await update.message.reply_text("📌 Message unpinned.")


@admin_only
async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a pinned announcement. Usage: /announce <text>"""
    msg = update.message
    if not context.args:
        await msg.reply_text("Usage: /announce <your announcement text>")
        return

    text = "📢 *Announcement*\n\n" + " ".join(context.args)
    sent = await context.bot.send_message(msg.chat_id, text, parse_mode="Markdown")
    await context.bot.pin_chat_message(msg.chat_id, sent.message_id)
    await msg.delete()


@admin_only
async def purge_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete N messages above the replied-to message. Usage: /purge <count>"""
    msg = update.message
    if not msg.reply_to_message or not context.args:
        await msg.reply_text("Reply to a message and provide count: /purge <count>")
        return

    count = int(context.args[0]) if context.args[0].isdigit() else 0
    if count < 1 or count > 100:
        await msg.reply_text("Count must be between 1 and 100.")
        return

    start_id = msg.reply_to_message.message_id
    ids_to_delete = list(range(start_id, start_id + count + 1)) + [msg.message_id]

    for mid in ids_to_delete:
        try:
            await context.bot.delete_message(msg.chat_id, mid)
        except Exception:
            pass  # Message may not exist or already deleted


def register_admin_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("pin", pin_message))
    app.add_handler(CommandHandler("unpin", unpin_message))
    app.add_handler(CommandHandler("announce", announce))
    app.add_handler(CommandHandler("purge", purge_messages))
