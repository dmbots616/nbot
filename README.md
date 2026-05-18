# 🤖 Telegram Group Management Bot

A feature-rich, production-ready Telegram group management bot built with **python-telegram-bot v21** and **SQLite**. Designed for public deployment — every feature works for regular admins, anonymous admins, and bot-owner superadmins.

---

## 📦 Project Structure

```
telegram-group-bot/
├── bot.py                        # Entry point
├── requirements.txt
├── .env.example
├── .gitignore
│
├── config/
│   └── settings.py               # Token, admin IDs, banned words
│
├── handlers/
│   ├── welcome.py                # Welcome & farewell messages
│   ├── moderation.py             # Warn, mute, kick, ban, auto-mod
│   ├── admin.py                  # Pin, announce, purge
│   ├── autodelete.py             # Per-type auto-delete timers
│   ├── repeat.py                 # Scheduled repeating messages
│   ├── activitylog.py            # Activity tracking, reputation, userinfo
│   ├── groupsettings.py          # Service msg delete, hyperlink filter, broadcast
│   └── info.py                   # /start, /help, /rules, /id
│
└── utils/
    ├── permissions.py            # @admin_only, @bot_can_restrict decorators
    ├── wizard.py                 # Shared wizard utilities (buttons, rich send, vars)
    └── database.py               # SQLite layer via aiosqlite
```

---

## ✨ Features

### 👋 Welcome & Farewell
Fully customizable welcome and farewell messages per group.

**Commands:**
| Command | Description |
|---|---|
| `/setwelcome` | Launch wizard to configure welcome message |
| `/setfarewell` | Launch wizard to configure farewell message |
| `/welcome` | Show current welcome settings and on/off toggle |
| `/farewell` | Show current farewell settings and on/off toggle |
| `/delwelcome` | Reset welcome to built-in default |
| `/delfarewell` | Reset farewell to built-in default |

**3-step wizard:** Text → Media → URL Buttons

**Supported variables in message text:**
| Variable | Replaced with |
|---|---|
| `{name}` | Member's full name |
| `{username}` | @username (or full name if no username) |
| `{mention}` | Clickable HTML mention |
| `{group}` | Group title |
| `{count}` | Current member count |
| `{id}` | Member's Telegram user ID |

**Example:**
```
👋 Welcome {mention} to {group}!
You are member #{count}. Please read the rules 📜
```

---

### ⚠️ Warning System
| Command | Description |
|---|---|
| `/warn` | Warn a user (reply to their message) |
| `/clearwarns` | Clear all warnings for a user |

- Auto-bans after `MAX_WARNINGS` (default: 3, configurable in `.env`)
- Banned-word auto-moderation: configure `BANNED_WORDS` in `.env`

---

### 🔇 Mute / Unmute
| Command | Description |
|---|---|
| `/mute [minutes]` | Mute a user (default: 10 minutes) |
| `/unmute` | Restore a user's messaging permission |

---

### 👢 Kick & Ban
| Command | Description |
|---|---|
| `/kick` | Kick a user (they can rejoin) |
| `/ban` | Permanently ban a user |
| `/unban <user_id>` | Unban a user by their Telegram ID |

---

### 🕐 Auto-Delete
Set independent auto-delete timers for each message type via an interactive inline keyboard.

**Commands:**
| Command | Description |
|---|---|
| `/autodelete` | Open interactive settings menu |
| `/autodelete status` | View all current timers as text |
| `/autodelete <type> <duration>` | Set timer for a specific type |
| `/autodelete all <duration>` | Apply same timer to all types |
| `/autodelete off` | Disable all auto-delete rules |

**Supported types:** `text` `photo` `video` `audio` `voice` `document` `sticker` `gif` `poll` `forward`

**Duration format:** `10s`, `5m`, `2h`, `1d` (range: 10 seconds – 7 days)

**Example:**
```
/autodelete photo 1h      → photos deleted after 1 hour
/autodelete text 30m      → text messages deleted after 30 min
/autodelete forward off   → stop deleting forwarded messages
```

---

### 🔁 Repeat Messages
Schedule automatic repeating messages per group — each group can have up to 10 independent repeat messages.

**Commands:**
| Command | Description |
|---|---|
| `/repeat` | Open repeat message manager (inline menu) |
| `/repeat list` | List all active repeating messages |
| `/repeat stop <id>` | Stop one repeating message by short ID |
| `/repeat stopall` | Stop all repeating messages in this group |

**4-step wizard:** Text → Media (photo/video/audio/document) → URL Buttons → Interval

**Interval presets:** 5m · 15m · 30m · 1h · 2h · 6h · 12h · 1d · 2d · 7d (or type custom)

**URL Button layout:**
```
# Vertical (one per row):
Join Channel | https://t.me/mychan
Website | https://example.com

# Horizontal (side-by-side, use ||):
GitHub | https://github.com || Docs | https://docs.com

# Mixed:
Top | https://top.com
Left | https://a.com || Right | https://b.com
Bottom | https://bot.com
```

---

### 📌 Pin & Announcements
| Command | Description |
|---|---|
| `/pin` | Pin the replied-to message |
| `/unpin` | Unpin the latest pinned message |
| `/announce <text>` | Send a message and auto-pin it |
| `/purge <count>` | Bulk-delete up to 100 messages from reply point |

---

### 🗑 Service Message Deletion
Toggle auto-deletion of Telegram's own system notifications.

**Command:** `/servicemsg`

**Deletes when ON:**
- "User joined via invite link"
- "User pinned a message"
- "Group photo was changed"
- "Video chat started / ended"
- Join/leave system notifications

> ⚠️ Your custom welcome/farewell messages are **not** affected.

---

### 🔗 Hyperlink Filter
Block URLs and inline hyperlinks from non-admin members.

**Command:** `/hyperlinkfilter`

**Permanently exempt (never filtered):**
- Group admins (including anonymous admins)
- Bot owner (`ADMIN_IDS` in `.env`)
- Members of the linked/connected channel

**Catches:**
- `https://` and `http://` links
- `www.` links
- Telegram inline hyperlinks (clickable text)
- Bare domain links

When a link is caught, the message is deleted and the sender receives an auto-expiring 8-second warning.

---

### 📊 Activity Logging & Reputation

#### Automatic tracking (no setup needed)
Every event is silently recorded in SQLite:
- Messages sent, media sent, edits
- Joins and leaves (full timeline)
- Commands used
- Admin actions: warns, mutes, kicks, bans, pins, vouches
- Name and username changes
- All groups shared between a user and the bot

#### Reputation & Trust Levels

Score is earned/lost automatically:

| Action | Score change |
|---|---|
| Message sent | +1 |
| Media sent | +2 |
| Vouch received | +5 |
| Warning received | −10 |
| Mute received | −20 |
| Ban received | −50 |

**Trust levels:**
| Badge | Level | Score needed |
|---|---|---|
| 🌱 New | Newcomer | 0+ |
| 🥉 Known | Active member | 30+ |
| 🥈 Trusted | Reliable member | 100+ |
| 🥇 Respected | Veteran | 200+ |
| 💎 Legendary | Top contributor | 500+ |

#### Commands
| Command | Who | Description |
|---|---|---|
| `/userinfo` | Everyone | User card — context-aware (see below) |
| `/profile [@user]` | Everyone | Full reputation card with trust badge |
| `/activity [@user]` | Everyone | Recent activity log |
| `/top` | Everyone | Group leaderboard (top 10) |
| `/vouch` | Everyone | Vouch for a user (reply) — one per pair per group |
| `/unvouch` | Everyone | Remove your vouch |
| `/userreport [@user]` | Admins | Full admin report with event breakdown |
| `/logevents` | Admins | Toggle which event types to log (inline menu) |
| `/logtoggle` | Admins | Enable/disable all logging for this group |
| `/setlogchannel` | Admins | Forward logs to a private channel |

#### `/userinfo` — context-aware user card

**In a group** (reply to message, or `/userinfo @username`):
```
👤 John Smith
🔖 @johnsmith  |  🆔 123456789

🥈 Trusted
Score: 142 [██████████░░░]

📅 Group History
  ✅ Joined  — 2025-01-10 14:32  (visit #1)
  🚪 Left    — 2025-02-03 09:11
  ✅ Joined  — 2025-03-15 18:44  (visit #2)

📊 Activity in this group
💬 Messages: 318  🖼 Media: 42
📈 Avg/day: 8.3 messages
👁 Last seen: 2h ago

🛡 Moderation
⚠️ Warns: 1 | 🔇 Mutes: 0 | 🚫 Bans: 0

🤝 Vouched by 5 member(s)
```

**In bot DM** (`/userinfo @username` or `/userinfo <user_id>`):
```
👤 John Smith
🔖 @johnsmith  |  🆔 123456789

🕐 First seen: 2024-11-01 10:00
👁 Last seen:  2h ago

📝 Name & Username History
👤 John Smith (@johnsmith) — 2025-03-01
  ↩ Johnny S (@johnny_s) — 2024-12-15
  ↩ John (@john123) — 2024-11-01

🌐 Common Groups with this bot (3)
• Tech Talks — Joined: 2024-11-01 · Last seen: 2h ago
• Dev Community — Joined: 2025-01-10 · Last seen: 3d ago

💎 Legendary
Global Score: 387 [███████░░░░]

📊 Global Activity
💬 Total messages: 892  🖼 Total media: 104

🛡 Global Moderation
⚠️ Warns: 1 | 🚫 Bans: 0
```

---

### 📢 Broadcast *(bot owner only)*
Send a promotional or announcement message to every group the bot is active in, with automatic repeating.

**Commands:**
| Command | Description |
|---|---|
| `/broadcast` | Open broadcast wizard |
| `/broadcast status` | Show active broadcast info |
| `/broadcast cancel` | Stop the repeating broadcast |

**4-step wizard:** Text → Media → URL Buttons → Repeat Interval

- Sends to **all groups** the bot has been active in (not just admin groups)
- Message is sent **immediately** then repeated at chosen interval
- Intervals: 1h · 6h · 12h · 1d · 3d · 7d (or custom)
- Groups are discovered passively as members chat — no manual registration needed
- Only users in `ADMIN_IDS` in `.env` can use `/broadcast`

---

### 🔐 Admin System
Three levels of admin recognition, all handled transparently:

| Type | How detected |
|---|---|
| Regular admins | `get_chat_member()` status check |
| Anonymous admins | Fixed Telegram ID `1087968824` |
| Bot superadmins | `ADMIN_IDS` list in `.env` |

All admin-only commands use the `@admin_only` decorator. Adding a new admin-only command is a single line.

---

### ℹ️ General Commands
| Command | Description |
|---|---|
| `/start` | Introduction message |
| `/help` | Full command reference |
| `/rules` | Show group rules (editable in `handlers/info.py`) |
| `/id` | Show your Telegram user ID and chat ID |

---

## 🚀 Setup

### 1. Create your bot
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy your **API token**

### 2. Configure environment
```bash
cp .env.example .env
```

Edit `.env`:
```env
# Required
BOT_TOKEN=your_bot_token_here

# Your Telegram user ID (get it with /id after starting the bot)
# Comma-separated for multiple owners
ADMIN_IDS=123456789,987654321

# Auto-ban after this many warnings (default: 3)
MAX_WARNINGS=3

# Words to auto-delete and warn for (comma-separated, leave blank to disable)
BANNED_WORDS=spam,scam
```

### 3. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Add bot to your group
1. Add the bot to your Telegram group
2. Promote it to **admin** with these permissions:
   - ✅ Delete messages
   - ✅ Ban users
   - ✅ Restrict members
   - ✅ Pin messages
   - ✅ Invite users (optional)

> **Anonymous admins are supported automatically** — no extra configuration needed.

### 5. Run
```bash
python bot.py
```

---

## 🗄 Database

All data is stored in **MongoDB** via `motor` (async driver). The database and all collections are created automatically on first run.

### Collections

| Collection | Stores |
|---|---|
| `user_activity` | Every loggable event per user per chat |
| `user_reputation` | Score, trust level, msg count, warns, bans per user per chat |
| `vouches` | Vouch relationships (one per pair per chat) |
| `log_settings` | Per-chat logging config and enabled events |
| `username_history` | Name and username change history per user |
| `user_groups` | Which groups each user has been seen in |
| `known_chats` | All groups the bot has been active in (for broadcast) |
| `group_settings` | Per-chat settings (service msg delete, hyperlink filter) |

### Connection options

**Local MongoDB:**
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=telegram_bot
```

**MongoDB Atlas (cloud, free tier available):**
```env
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net
MONGO_DB_NAME=telegram_bot
```

> Get a free MongoDB Atlas cluster at [mongodb.com/atlas](https://www.mongodb.com/atlas) — no credit card required. Recommended for production deployments.

---

## ⚙️ Extending the Bot

### Add a new command
1. Write your handler function in the appropriate file under `handlers/`
2. Register it in that file's `register_*_handlers(app)` function
3. Done — no changes to `bot.py` needed

### Add a new admin-only command
```python
from utils.permissions import admin_only

@admin_only
async def my_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Only admins see this!")
```

### Add a new database table
Add a `CREATE TABLE IF NOT EXISTS` block to `init_db()` in `utils/database.py`, then write async helper functions below it.

---

## 📋 Requirements

```
python-telegram-bot[job-queue]==21.6
python-dotenv==1.0.1
motor==3.4.0
pymongo==4.7.2
```

Python 3.10+ required.

---

## 📁 Data & Privacy

- All data is stored in your MongoDB instance — fully under your control
- No data is sent to any third party
- Username history and activity logs are stored indefinitely — add a TTL index if needed for GDPR compliance
- For Atlas, enable IP allowlisting and use a dedicated database user with least-privilege access

---

## 📄 License

MIT — free to use, modify, and distribute.
