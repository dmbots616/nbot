import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Comma-separated list of Telegram user IDs with admin privileges
ADMIN_IDS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
]

# Max number of warnings before a user is kicked
MAX_WARNINGS: int = int(os.getenv("MAX_WARNINGS", "3"))

# Words to flag (comma-separated)
BANNED_WORDS: list[str] = [
    w.strip().lower()
    for w in os.getenv("BANNED_WORDS", "").split(",")
    if w.strip()
]
