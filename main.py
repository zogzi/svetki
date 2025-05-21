import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Load environment variables only once at startup
load_dotenv()

# Configure logging with a smaller buffer size
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',  # Simplified format
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8", buffering=4096)  # 4KB buffer
    ]
)
logger = logging.getLogger("discord_bot")

# Define minimal intents - only what's needed
intents = discord.Intents.none()
intents.guilds = True      # Need this for basic functionality
intents.messages = True    # For receiving messages
intents.message_content = True  # Needed for command handling

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.config = {}  # Will be populated in setup_hook
        self.last_holiday_messages = {}
        self._config_path = Path("config.json")
        self._default_config = {
            "channel_id": 1374479725392564296,
            "message_time_utc": {"hour": 10, "minute": 7},
            "delete_after_hours": 24,
            "holiday_messages": {
                "5-21": "Happy New Year! 🎉",
                "12-25": "Merry Christmas! 🎄",
                "2-14": "Happy Valentine's Day! ❤️",
                "10-31": "Happy Halloween! 🎃",
            }
        }

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json or create default."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                return self._default_config

        # No config file, create default
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._default_config, f, indent=2)  # Smaller indentation
            return self._default_config
        except Exception:
            logger.error("Could not write default config.json")
            return self._default_config

    async def setup_hook(self) -> None:
        """Asynchronous setup before bot logs in."""
        self.config = self.load_config()
        
        # Start background tasks with less frequent checks to reduce CPU usage
        self.check_holiday_messages.start()
        self.auto_delete_messages.change_interval(hours=3)  # Check less frequently
        self.auto_delete_messages.start()
        
        logger.info("Tasks started")

    async def on_ready(self) -> None:
        """Event fired when the bot is ready."""
        logger.info(f"Bot connected as {self.user.name} (ID: {self.user.id})")
        
    def cleanup_cache_files(self, keep_date_key: str) -> None:
        """Remove cache files from previous days more efficiently."""
        cache_dir = Path(".")
        prefix = "sent_"
        suffix = ".tmp"
        keep_file = f"{prefix}{keep_date_key}{suffix}"
        
        # More efficient loop with less file operations
        for file in cache_dir.glob(f"{prefix}*{suffix}"):
            if file.name != keep_file:
                try:
                    file.unlink(missing_ok=True)  # Requires Python 3.8+
                except OSError:
                    pass

    @tasks.loop(hours=1)
    async def check_holiday_messages(self) -> None:
        """Check for holiday messages once per hour."""
        await self.wait_until_ready()

        now = datetime.now(timezone.utc)
        date_key = f"{now.month}-{now.day}"
        
        # Only clean up cache files once per day
        if now.hour == 0 and 0 <= now.minute < 10:
            self.cleanup_cache_files(date_key)

        holiday_messages = self.config.get("holiday_messages", {})
        if date_key not in holiday_messages:
            return
            
        msg_config = self.config.get("message_time_utc", {})
        target_hour = msg_config.get("hour", 0)
        target_minute = msg_config.get("minute", 0)

        # Check if we're within the 5-minute window to send the message
        if now.hour == target_hour and target_minute <= now.minute < target_minute + 5:
            cache_file_name = f"sent_{date_key}.tmp"
            cache_file = Path(cache_file_name)

            if not cache_file.exists():
                holiday_message = holiday_messages[date_key]
                logger.info(f"Holiday: {date_key}. Sending: \"{holiday_message}\"")
                
                # Send message and store for later deletion
                message = await self.send_message_to_configured_channel(holiday_message)
                
                if message:
                    # Store the message for auto-deletion
                    self.last_holiday_messages[date_key] = {
                        "id": message.id,
                        "timestamp": now.timestamp()
                    }
                    
                    # Create cache file to prevent re-sending
                    try:
                        cache_file.touch()
                    except OSError:
                        logger.error(f"Failed to create cache file: {cache_file_name}")
                        
            # Skip verbose logging if already sent
            # This avoids unnecessary disk I/O

    @tasks.loop(hours=3)  # Less frequent checks to save resources
    async def auto_delete_messages(self) -> None:
        """Delete messages after the configured time period."""
        await self.wait_until_ready()
        
        if not self.last_holiday_messages:  # Skip if no messages to check
            return
            
        delete_after_hours = self.config.get("delete_after_hours", 24)
        threshold = datetime.now(timezone.utc) - timedelta(hours=delete_after_hours)
        threshold_ts = threshold.timestamp()
        
        channel_id = self.config.get("channel_id")
        if not channel_id:
            return
            
        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
            
        # Find messages to delete
        keys_to_remove = []
        for date_key, msg_data in self.last_holiday_messages.items():
            if msg_data["timestamp"] < threshold_ts:
                try:
                    message = await channel.fetch_message(msg_data["id"])
                    await message.delete()
                    keys_to_remove.append(date_key)
                except (discord.errors.NotFound, discord.errors.Forbidden):
                    # Already deleted or no permissions
                    keys_to_remove.append(date_key)
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")
        
        # Remove processed messages from tracking
        for key in keys_to_remove:
            self.last_holiday_messages.pop(key, None)

    async def send_message_to_configured_channel(self, content: str) -> Optional[discord.Message]:
        """Send a message to the configured channel."""
        channel_id = self.config.get("channel_id")
        if not channel_id:
            logger.error("Channel ID not configured.")
            return None

        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.error(f"Invalid channel: {channel_id}")
            return None

        try:
            return await channel.send(content)
        except discord.errors.Forbidden:
            logger.error(f"No permission to send messages")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
        
        return None


def main() -> None:
    """Main function to start the bot."""
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
