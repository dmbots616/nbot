import functools
from telegram import Update, ChatMember
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS

# Telegram's fixed user ID for the anonymous group admin account.
# When an admin hides their identity, messages appear from this sender.
ANONYMOUS_ADMIN_ID = 1087968824


def admin_only(func):
    """Decorator: allow group admins, anonymous admins, and configured ADMIN_IDS."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat

        # Anonymous admin: Telegram routes their messages through a fixed bot account.
        # Any message from that account inside a group is guaranteed to be an admin.
        if user.id == ANONYMOUS_ADMIN_ID:
            return await func(update, context, *args, **kwargs)

        # Always allow globally configured admins
        if user.id in ADMIN_IDS:
            return await func(update, context, *args, **kwargs)

        # Check if user is a chat admin/owner
        member = await chat.get_member(user.id)
        if member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
            return await func(update, context, *args, **kwargs)

        await update.message.reply_text("⛔ This command is for admins only.")

    return wrapper


def bot_can_restrict(func):
    """Decorator: check if the bot has permission to restrict members."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        bot_member = await chat.get_member(context.bot.id)

        if not getattr(bot_member, "can_restrict_members", False):
            await update.message.reply_text(
                "⚠️ I need 'Restrict Members' permission to do that."
            )
            return

        return await func(update, context, *args, **kwargs)

    return wrapper
