import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("discord_bot")

intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.config = {}
        self.holiday_keys = set()
        self.last_holiday_messages = {}
        self._config_path = Path("config.json")
        self._default_config = {
            "channel_id": 1374479725392564296,
            "message_time_utc": {"hour": 10, "minute": 14},
            "delete_after_hours": 24,
            "holiday_messages": {
                "5-21": "Happy New Year! 🎉",
                "12-25": "Merry Christmas! 🎄",
                "2-14": "Happy Valentine's Day! ❤️",
                "10-31": "Happy Halloween! 🎃",
            }
        }

    def load_config(self) -> Dict[str, Any]:
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                return self._default_config

        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._default_config, f, indent=2)
            return self._default_config
        except Exception:
            logger.error("Could not write default config.json")
            return self._default_config

    async def setup_hook(self) -> None:
        self.config = self.load_config()
        self.holiday_keys = set(self.config.get("holiday_messages", {}).keys())

        self.daily_check.start()
        self.auto_delete_messages.change_interval(hours=3)
        self.auto_delete_messages.start()
        logger.info("Tasks started")

    async def on_ready(self) -> None:
        logger.info(f"Bot connected as {self.user.name} (ID: {self.user.id})")

    def cleanup_cache_files(self, keep_date_key: str) -> None:
        cache_dir = Path(".")
        prefix = "sent_"
        suffix = ".tmp"
        keep_file = f"{prefix}{keep_date_key}{suffix}"

        for file in cache_dir.glob(f"{prefix}*{suffix}"):
            if file.name != keep_file:
                try:
                    file.unlink(missing_ok=True)
                except OSError:
                    pass

    @tasks.loop(hours=24)
    async def daily_check(self) -> None:
        await self.wait_until_ready()

        now = datetime.now(timezone.utc)
        date_key = f"{now.month}-{now.day}"

        # Cleanup cache once daily
        if now.hour == 0:
            self.cleanup_cache_files(date_key)

        if date_key not in self.holiday_keys:
            return  # Skip non-holiday days

        msg_config = self.config.get("message_time_utc", {})
        target_hour = msg_config.get("hour", 0)
        target_minute = msg_config.get("minute", 0)
        target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

        if now < target_time:
            return  # Too early, wait until target time

        cache_file = Path(f"sent_{date_key}.tmp")
        if cache_file.exists():
            return  # Already sent today

        message = await self.send_message_to_configured_channel(
            self.config["holiday_messages"][date_key]
        )

        if message:
            self.last_holiday_messages[date_key] = {
                "id": message.id,
                "timestamp": now.timestamp()
            }
            try:
                cache_file.touch()
            except OSError:
                logger.error(f"Failed to create cache file: {cache_file.name}")

    @tasks.loop(hours=3)
    async def auto_delete_messages(self) -> None:
        await self.wait_until_ready()

        if not self.last_holiday_messages:
            return

        delete_after_hours = self.config.get("delete_after_hours", 24)
        threshold = datetime.now(timezone.utc) - timedelta(hours=delete_after_hours)
        threshold_ts = threshold.timestamp()

        channel_id = self.config.get("channel_id")
        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        keys_to_remove = []
        for date_key, msg_data in self.last_holiday_messages.items():
            if msg_data["timestamp"] < threshold_ts:
                try:
                    message = await channel.fetch_message(msg_data["id"])
                    await message.delete()
                    keys_to_remove.append(date_key)
                except (discord.errors.NotFound, discord.errors.Forbidden):
                    keys_to_remove.append(date_key)
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")

        for key in keys_to_remove:
            self.last_holiday_messages.pop(key, None)

    async def send_message_to_configured_channel(self, content: str) -> Optional[discord.Message]:
        channel_id = self.config.get("channel_id")
        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.error(f"Invalid channel: {channel_id}")
            return None

        try:
            return await channel.send(content)
        except discord.errors.Forbidden:
            logger.error("No permission to send messages")
        except Exception as e:
            logger.error(f"Error sending message: {e}")

        return None

def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.critical("No token found. Set DISCORD_BOT_TOKEN env variable.")
        return

    bot = HolidayBot()
    try:
        logger.info("Starting bot...")
        bot.run(token, log_handler=None)
    except discord.errors.LoginFailure:
        logger.critical("Invalid token. Check DISCORD_BOT_TOKEN env variable.")
    except Exception as e:
        logger.critical(f"Error starting bot: {e}")

if __name__ == "__main__":
    main()
