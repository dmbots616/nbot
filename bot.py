import asyncio
import logging
from telegram.ext import ApplicationBuilder

from config.settings import BOT_TOKEN
from handlers.welcome import register_welcome_handlers
from handlers.moderation import register_moderation_handlers
from handlers.admin import register_admin_handlers
from handlers.info import register_info_handlers
from handlers.autodelete import register_autodelete_handlers
from handlers.repeat import register_repeat_handlers
from handlers.activitylog import register_log_handlers
from handlers.groupsettings import register_group_settings_handlers
from utils.database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    asyncio.get_event_loop().run_until_complete(init_db())

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    register_welcome_handlers(app)
    register_moderation_handlers(app)
    register_admin_handlers(app)
    register_info_handlers(app)
    register_autodelete_handlers(app)
    register_repeat_handlers(app)
    register_log_handlers(app)
    register_group_settings_handlers(app)

    logger.info("Bot started. Polling for updates...")
    app.run_polling()


if __name__ == "__main__":
    main()
