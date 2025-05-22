import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Load .env file once
load_dotenv()

# Logging setup (rotating to prevent log bloat)
from logging.handlers import RotatingFileHandler
handler = RotatingFileHandler("bot.log", maxBytes=5_000_000, backupCount=3, encoding='utf-8')
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), handler]
)
logger = logging.getLogger("holiday_bot")

# Minimal Discord intents
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.config = {}
        self.last_holiday_messages = {}
        self._config_path = Path("config.json")
        self._default_config = {
            "channel_id": 1374479725392564296,
            "message_time_utc": {"hour": 22, "minute": 0},  # 01:00 Latvia Time (summer)
            "delete_after_hours": 24,
            "holiday_messages": {
                "5-22": "Test Holiday! 🎉",
                "2-14": "Happy Valentine's Day! ❤️",
                "12-25": "Merry Christmas! 🎄"
            }
        }

    def load_config(self) -> Dict[str, Any]:
        if self._config_path.exists():
            try:
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed loading config: {e}")
                return self._default_config

        try:
            with open(self._config_path, 'w', encoding='utf-8') as f:
                json.dump(self._default_config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write config: {e}")

        return self._default_config

    async def setup_hook(self) -> None:
        self.config = self.load_config()
        self.check_holiday_messages.start()
        self.auto_delete_messages.change_interval(hours=5)  # Reduced frequency
        self.auto_delete_messages.start()

    async def on_ready(self) -> None:
        logger.info(f"Bot connected as {self.user} (ID: {self.user.id})")

    @tasks.loop(minutes=10)
    async def check_holiday_messages(self):
        await self.wait_until_ready()

        now = datetime.now(timezone.utc)
        date_key = f"{now.month}-{now.day}"

        msg_time = self.config.get("message_time_utc", {})
        if now.hour == msg_time.get("hour", 0) and now.minute < msg_time.get("minute", 0) + 5:
            if date_key not in self.config.get("holiday_messages", {}):
                return

            if date_key not in self.last_holiday_messages:
                content = self.config["holiday_messages"][date_key]
                message = await self.send_message_to_configured_channel(content)

                if message:
                    self.last_holiday_messages[date_key] = {
                        "id": message.id,
                        "timestamp": now.timestamp()
                    }
                    logger.info(f"Sent holiday message for {date_key}")

    @tasks.loop(hours=5)
    async def auto_delete_messages(self):
        await self.wait_until_ready()

        if not self.last_holiday_messages:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.get("delete_after_hours", 24))
        channel_id = self.config.get("channel_id")
        channel = self.get_channel(channel_id)

        if not channel or not isinstance(channel, discord.TextChannel):
            return

        to_delete = []
        for date_key, msg in self.last_holiday_messages.items():
            if msg["timestamp"] < cutoff.timestamp():
                try:
                    message = await channel.fetch_message(msg["id"])
                    await message.delete()
                    to_delete.append(date_key)
                except (discord.NotFound, discord.Forbidden):
                    to_delete.append(date_key)

        for key in to_delete:
            self.last_holiday_messages.pop(key, None)

    async def send_message_to_configured_channel(self, content: str) -> Optional[discord.Message]:
        channel_id = self.config.get("channel_id")
        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.error("Invalid channel")
            return None
        try:
            return await channel.send(content)
        except discord.Forbidden:
            logger.error("Missing permissions to send message")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
        return None

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.critical("DISCORD_BOT_TOKEN is missing")
        return

    bot = HolidayBot()
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.critical("Invalid token")
    except Exception as e:
        logger.critical(f"Bot error: {e}")

if __name__ == "__main__":
    main()
