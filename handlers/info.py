from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

HELP_TEXT = """
🤖 *Group Management Bot — Commands*

*⚠️ Moderation (admins only)*
/warn — Warn a user (reply to their message)
/clearwarns — Clear a user's warnings
/mute [minutes] — Mute a user (default: 10 min)
/unmute — Unmute a user
/kick — Kick a user (can rejoin)
/ban — Permanently ban a user
/unban <user\_id> — Unban a user by ID

*📌 Admin Tools*
/pin — Pin the replied-to message
/unpin — Unpin the latest pinned message
/announce <text> — Send a pinned announcement
/purge <count> — Bulk-delete up to 100 messages

*🕐 Auto-Delete (admins only)*
/autodelete — Open per-type timer menu
/autodelete status — View all current timers
/autodelete <type> <duration> — Set a timer (e.g. `photo 1h`)
/autodelete all <duration> — Apply to all types
/autodelete off — Disable all timers
  › Types: text, photo, video, audio, voice, document, sticker, gif, poll, forward
  › Durations: `30s` `5m` `2h` `1d` (10s – 7d)

*🔁 Repeat Messages (admins only)*
/repeat — Open repeat message manager
/repeat list — List active repeating messages
/repeat stop <id> — Stop one by short ID
/repeat stopall — Stop all

*👋 Welcome & Farewell (admins only)*
/setwelcome — Configure welcome message (wizard)
/setfarewell — Configure farewell message (wizard)
/welcome — Show settings & on/off toggle
/farewell — Show settings & on/off toggle
/delwelcome — Reset to default
/delfarewell — Reset to default
  › Variables: `{name}` `{mention}` `{username}` `{group}` `{count}` `{id}`

*⚙️ Group Settings (admins only)*
/servicemsg — Toggle Telegram service message deletion
/hyperlinkfilter — Toggle URL/link filter for non-admins

*🔍 User Info & Reputation*
/userinfo [@user|reply] — Full user card (context-aware)
/profile [@user] — Reputation card & trust badge
/activity [@user] — Recent activity log
/top — Group reputation leaderboard (top 10)
/vouch — Vouch for a user (reply to their message)
/unvouch — Remove your vouch

*📋 Logging (admins only)*
/userreport [@user] — Full admin report
/logevents — Toggle which events to log
/logtoggle — Enable/disable logging
/setlogchannel — Forward logs to a channel

*📢 Broadcast (bot owner only)*
/broadcast — Send to all groups + repeat on interval
/broadcast status — Show active broadcast
/broadcast cancel — Stop repeating broadcast

*ℹ️ General*
/start — Introduction
/help — This message
/rules — Group rules
/id — Your user ID and chat ID
"""

RULES_TEXT = """
📜 *Group Rules*

1. Be respectful to all members.
2. No spam, self-promotion, or advertisements.
3. No offensive, hateful, or NSFW content.
4. Stay on topic.
5. Listen to the admins.

Violations may result in warnings, mutes, or bans.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hi! I'm your group management bot.\nUse /help to see available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(RULES_TEXT, parse_mode="Markdown")


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    text = f"👤 Your user ID: `{user.id}`\n💬 Chat ID: `{chat.id}`"
    await update.message.reply_text(text, parse_mode="Markdown")


def register_info_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("id", get_id))
