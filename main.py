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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("discord_bot")

# Define intents
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True  # Needed for commands

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.config: Dict[str, Any] = {}
        self.last_holiday_messages = {}  # Simple dict to track holiday messages for auto-deletion

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json or create default."""
        config_path = Path("config.json")
        default_config = {
            "channel_id": 1374479725392564296,  # Replace with your actual channel ID
            "message_time_utc": {
                "hour": 9,
                "minute": 0
            },
            "delete_after_hours": 24,  # Time in hours before messages are deleted
            "holiday_messages": {
                "1-1": "Happy New Year! 🎉",
                "12-25": "Merry Christmas! 🎄",
                "2-14": "Happy Valentine's Day! ❤️",
                "10-31": "Happy Halloween! 🎃",
                # Add more holidays: "Month-Day": "Message"
            }
        }

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}", exc_info=True)
                return default_config

        # No config file, create default
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)
            return default_config
        except Exception:
            logger.error("Could not write default config.json", exc_info=True)
            return default_config

    async def setup_hook(self) -> None:
        """Asynchronous setup before bot logs in."""
        self.config = self.load_config()
        
        # Start background tasks
        self.check_holiday_messages.start()
        self.auto_delete_messages.start()
        
        logger.info("Holiday message checker and auto-delete tasks started.")

    async def on_ready(self) -> None:
        """Event fired when the bot is ready."""
        logger.info(f"Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")

    def cleanup_cache_files(self, current_date_key: str) -> None:
        """Remove cache files from previous days."""
        cache_dir = Path(".")
        for file in cache_dir.glob("sent_*.tmp"):
            if file.name != f"sent_{current_date_key}.tmp":
                try:
                    file.unlink()
                    logger.info(f"Removed old cache file: {file.name}")
                except OSError:
                    pass

    @tasks.loop(hours=1)
    async def check_holiday_messages(self) -> None:
        """Check for holiday messages once per hour."""
        await self.wait_until_ready()

        now = datetime.now(timezone.utc)
        date_key = f"{now.month}-{now.day}"

        # Clean up cache files from previous days
        self.cleanup_cache_files(date_key)

        if date_key in self.config.get("holiday_messages", {}):
            target_hour = self.config.get("message_time_utc", {}).get("hour", 0)
            target_minute = self.config.get("message_time_utc", {}).get("minute", 0)

            # Check if we're within the 5-minute window to send the message
            if now.hour == target_hour and target_minute <= now.minute < target_minute + 5:
                cache_file_name = f"sent_{date_key}.tmp"
                cache_file = Path(cache_file_name)

                if not cache_file.exists():
                    holiday_message = self.config["holiday_messages"][date_key]
                    logger.info(f"Holiday detected: {date_key}. Sending message: \"{holiday_message}\"")
                    
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
                            logger.error(f"Failed to create cache file: {cache_file_name}", exc_info=True)
                else:
                    logger.info(f"Holiday message for {date_key} already sent today.")

    @tasks.loop(hours=1)
    async def auto_delete_messages(self) -> None:
        """Delete messages after the configured time period."""
        await self.wait_until_ready()
        
        delete_after_hours = self.config.get("delete_after_hours", 24)
        threshold = datetime.now(timezone.utc) - timedelta(hours=delete_after_hours)
        threshold_ts = threshold.timestamp()
        
        channel_id = self.config.get("channel_id")
        channel = self.get_channel(channel_id) if channel_id else None
        
        if not channel or not isinstance(channel, discord.TextChannel):
            return
            
        # Find messages to delete
        keys_to_remove = []
        for date_key, msg_data in self.last_holiday_messages.items():
            if msg_data["timestamp"] < threshold_ts:
                try:
                    message = await channel.fetch_message(msg_data["id"])
                    await message.delete()
                    logger.info(f"Auto-deleted holiday message for {date_key}")
                    keys_to_remove.append(date_key)
                except discord.errors.NotFound:
                    # Already deleted
                    keys_to_remove.append(date_key)
                except discord.errors.Forbidden:
                    logger.error("Missing permissions to delete messages")
                    keys_to_remove.append(date_key)  # No point trying again
                except Exception as e:
                    logger.error(f"Error deleting message: {e}", exc_info=True)
        
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
            logger.error(f"Invalid channel configuration: {channel_id}")
            return None

        try:
            message = await channel.send(content)
            logger.info(f"Message sent to #{channel.name} (ID: {message.id})")
            return message
        except discord.errors.Forbidden:
            logger.error(f"Missing permissions to send messages to #{channel.name}")
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
        
        return None


def main() -> None:
    """Main function to start the bot."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.critical("CRITICAL: No token found. Set the DISCORD_BOT_TOKEN environment variable.")
        return

    bot = HolidayBot()
    
    # Add the test command
    @bot.command(name="test")
    async def test_cmd(ctx):
        """Test command to send a holiday message."""
        await bot.test_holiday_message(ctx)

    try:
        logger.info("Starting bot...")
        bot.run(token, log_handler=None)
    except discord.errors.LoginFailure:
        logger.critical("CRITICAL: Invalid token. Please check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        logger.critical(f"CRITICAL: Error starting bot: {e}", exc_info=True)

if __name__ == "__main__":
    main()
