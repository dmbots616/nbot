"""
Shared wizard utilities
=======================
Shared helpers for building inline-keyboard wizards, parsing buttons,
sending rich messages (text + media + URL buttons), and variable
substitution used by both the Welcome and Repeat handlers.
"""

import re
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode


# ---------------------------------------------------------------------------
# Button parsing
# ---------------------------------------------------------------------------

def parse_buttons(raw: str) -> Optional[list[list[dict]]]:
    """
    Parse button definitions into a serialisable list-of-rows.

    Vertical  — one button per line:
        Label A | https://a.com
        Label B | https://b.com

    Horizontal — buttons on the same row, separated by  ||:
        Left | https://a.com || Right | https://b.com

    Mixed:
        Top | https://top.com
        Left | https://a.com || Middle | https://m.com || Right | https://b.com
        Bottom | https://bottom.com

    Returns list[list[{"text": str, "url": str}]] or None on bad input.
    """
    rows: list[list[dict]] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split("||")]
        row: list[dict] = []
        for cell in cells:
            if "|" not in cell:
                return None
            label, url = cell.split("|", 1)
            label, url = label.strip(), url.strip()
            if not label or not url.startswith("http"):
                return None
            row.append({"text": label, "url": url})
        if row:
            rows.append(row)
    return rows if rows else None


def build_reply_markup(buttons: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    kb = [
        [InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
        for row in buttons
    ]
    return InlineKeyboardMarkup(kb)


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

SUPPORTED_VARS = {
    "{name}":       "Member's full name",
    "{username}":   "@username (or full name if none)",
    "{mention}":    "Clickable mention (HTML)",
    "{group}":      "Group name",
    "{count}":      "Current member count",
    "{id}":         "Member's Telegram user ID",
}


def substitute_vars(text: str, member=None, chat=None, count: int = 0) -> str:
    """Replace {var} placeholders with real values."""
    if not text:
        return text
    if member:
        username = f"@{member.username}" if member.username else member.full_name
        text = text.replace("{name}", member.full_name)
        text = text.replace("{username}", username)
        text = text.replace("{mention}", f'<a href="tg://user?id={member.id}">{member.full_name}</a>')
        text = text.replace("{id}", str(member.id))
    if chat:
        text = text.replace("{group}", chat.title or "this group")
    text = text.replace("{count}", str(count))
    return text


# ---------------------------------------------------------------------------
# Sending a rich message (text + optional media + optional buttons)
# ---------------------------------------------------------------------------

async def send_rich_message(
    bot,
    chat_id: int,
    entry: dict,
    member=None,
    chat=None,
    member_count: int = 0,
) -> None:
    """
    Send a rich message from a stored entry dict.
    Applies variable substitution if member/chat are provided.

    entry keys:
        text      : str  (may contain {vars})
        media     : {"type": "photo"|"video"|"document"|"audio", "file_id": str} | None
        buttons   : list[list[{"text", "url"}]] | None
    """
    raw_text = entry.get("text", "")
    text = substitute_vars(raw_text, member=member, chat=chat, count=member_count)
    media = entry.get("media")
    reply_markup = build_reply_markup(entry.get("buttons"))

    # Use HTML parse mode so {mention} links render properly
    parse_mode = ParseMode.HTML

    try:
        if not media:
            await bot.send_message(
                chat_id,
                text or "​",           # zero-width space fallback
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        else:
            mtype  = media["type"]
            fid    = media["file_id"]
            cap    = text or None
            kwargs = dict(caption=cap, parse_mode=parse_mode, reply_markup=reply_markup)
            if mtype == "photo":
                await bot.send_photo(chat_id, fid, **kwargs)
            elif mtype == "video":
                await bot.send_video(chat_id, fid, **kwargs)
            elif mtype == "document":
                await bot.send_document(chat_id, fid, **kwargs)
            elif mtype == "audio":
                await bot.send_audio(chat_id, fid, **kwargs)
            else:
                await bot.send_message(chat_id, text or "📌",
                                       parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Wizard step keyboard builders (reusable)
# ---------------------------------------------------------------------------

def skip_cancel_kb(skip_cb: str, cancel_cb: str, skip_label: str = "⏭ Skip") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(skip_label, callback_data=skip_cb),
        InlineKeyboardButton("✖ Cancel",  callback_data=cancel_cb),
    ]])


BUTTON_HELP = (
    "Send URL buttons, one entry per line.\n\n"
    "*Vertical* (each on its own row):\n"
    "`Join Channel | https://t.me/chan`\n"
    "`Website | https://example.com`\n\n"
    "*Horizontal* (side-by-side, use `||`):\n"
    "`Left | https://a.com || Right | https://b.com`\n\n"
    "*Mixed:*\n"
    "`Top | https://top.com`\n"
    "`A | https://a.com || B | https://b.com`\n"
    "`Bottom | https://bot.com`\n\n"
    "Or tap Skip."
)
