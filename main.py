import os
import json
import logging
import asyncio
from datetime import datetime, time, timezone # Added timezone
from pathlib import Path
from typing import Dict, Any # Added for type hinting

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s', # Added module
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("discord_bot")

# Define intents
intents = discord.Intents.default()
intents.messages = True # Needed if you plan to have commands that are triggered by messages
# intents.message_content = True # Add this if your commands need to read message content
                                 # and enable it in your Discord Developer Portal

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        # self.config is loaded in setup_hook after async context is available for logging if needed
        self.config: Dict[str, Any] = {} # Initialize with type hint

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json or create default if not exists."""
        config_path = Path("config.json")
        default_config = {
            "channel_id": 1374479725392564296,  # Replace with your actual channel ID
            "message_time_utc": { # Clarified UTC
                "hour": 9,
                "minute": 0
            },
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
                    logger.info("Loading configuration from config.json")
                    return json.load(f)
            except json.JSONDecodeError:
                logger.error("Error decoding config.json. Using default config and overwriting.", exc_info=True)
                # Fall through to create and return default config
            except Exception as e:
                logger.error(f"Unexpected error loading config: {e}. Using default config.", exc_info=True)
                # Fall through

        logger.info("config.json not found or corrupt, creating with default values.")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)
            return default_config
        except IOError:
            logger.error("Could not write default config.json. Using in-memory default.", exc_info=True)
            return default_config


    async def setup_hook(self) -> None:
        """Asynchronous setup method called before bot logs in."""
        self.config = self.load_config() # Load config here
        # You could also perform other async setup here, like loading cogs

        # Start background tasks
        self.check_holiday_messages.start()
        logger.info("Holiday message checker task started.")

    async def on_ready(self) -> None:
        """Event fired when the bot is ready and connected."""
        logger.info(f"Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds:")
        for guild in self.guilds:
            logger.info(f"- {guild.name} (ID: {guild.id})")
        # Example: You could try to sync application commands here if you have any
        # try:
        #     synced = await self.tree.sync()
        #     logger.info(f"Synced {len(synced)} commands globally.")
        # except Exception as e:
        #     logger.exception(f"Failed to sync commands: {e}")


    def cleanup_cache_files(self, current_date_key: str) -> None:
        """Remove cache files from previous days."""
        cache_dir = Path(".") # Or a dedicated cache subfolder: Path("cache")
        cache_dir.mkdir(exist_ok=True) # Ensure directory exists if using a subfolder

        for file in cache_dir.glob("sent_*.tmp"):
            if file.name != f"sent_{current_date_key}.tmp":
                try:
                    file.unlink()
                    logger.info(f"Removed old cache file: {file.name}")
                except OSError as e:
                    logger.error(f"Error removing cache file {file.name}: {e}")

    @tasks.loop(hours=1)
    async def check_holiday_messages(self) -> None:
        """Check for holiday messages once per hour."""
        await self.wait_until_ready() # Ensure bot is ready before the first run of the task

        now = datetime.now(timezone.utc) # Use timezone-aware datetime
        date_key = f"{now.month}-{now.day}"

        # Clean up cache files from previous days first
        self.cleanup_cache_files(date_key)

        if date_key in self.config.get("holiday_messages", {}):
            target_hour = self.config.get("message_time_utc", {}).get("hour", 0)
            target_minute = self.config.get("message_time_utc", {}).get("minute", 0)

            # Check if current hour matches and if we are within a 5-minute window past the target minute
            # This allows for some flexibility if the task doesn't run at the exact start of the hour.
            if now.hour == target_hour and target_minute <= now.minute < target_minute + 5:
                cache_file_name = f"sent_{date_key}.tmp"
                cache_file = Path(cache_file_name) # Assumes cache files in current dir or specified cache_dir

                if not cache_file.exists():
                    holiday_message = self.config["holiday_messages"][date_key]
                    logger.info(f"Holiday detected: {date_key}. Current UTC time: {now.strftime('%H:%M')}. Target UTC: {target_hour:02}:{target_minute:02}. Sending message: \"{holiday_message}\"")
                    await self.send_message_to_configured_channel(holiday_message)

                    try:
                        cache_file.touch()
                        logger.info(f"Created cache file: {cache_file_name}")
                    except OSError as e:
                        logger.error(f"Error creating cache file {cache_file_name}: {e}")
                else:
                    logger.info(f"Holiday message for {date_key} already sent today (cache file exists).")
            # else: # Optional: Log why message wasn't sent (e.g., time mismatch)
            #     if now.hour == target_hour:
            #          logger.debug(f"Correct hour ({now.hour}) for {date_key}, but minute ({now.minute}) outside window ({target_minute}-{target_minute+4}).")

    async def send_message_to_configured_channel(self, content: str) -> None:
        """Send a message to the configured channel."""
        channel_id = self.config.get("channel_id")
        if not channel_id:
            logger.error("Channel ID not configured. Cannot send message.")
            return

        channel = self.get_channel(channel_id)
        if not channel:
            logger.error(f"Channel {channel_id} not found. Bot might not be in the guild or channel was deleted.")
            return
        
        if not isinstance(channel, discord.TextChannel): # Ensure it's a text channel
            logger.error(f"Channel {channel_id} ({channel.name}) is not a text channel. Cannot send message.")
            return

        try:
            logger.info(f"Attempting to send message to #{channel.name} in {channel.guild.name}")
            await channel.send(content)
            logger.info(f"Message sent successfully to #{channel.name}.")
        except discord.errors.Forbidden:
            logger.error(f"Missing permissions to send messages to #{channel.name} in {channel.guild.name}. Check bot roles/permissions.")
        except discord.errors.HTTPException as e:
            logger.error(f"HTTP error sending message to #{channel.name}: {e.status} {e.text}", exc_info=True)
        except Exception as e:
            logger.error(f"An unexpected error occurred sending message to #{channel.name}: {e}", exc_info=True)


def main() -> None:
    """Main function to start the bot."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.critical("CRITICAL: No token found. Set the DISCORD_BOT_TOKEN environment variable.")
        return

    bot = HolidayBot()

    try:
        logger.info("Starting bot...")
        bot.run(token, log_handler=None) # discord.py's default handler is fine if not using custom root logger setup
                                        # Or pass your own handler if you've configured the root logger differently
    except discord.errors.LoginFailure:
        logger.critical("CRITICAL: Invalid token. Please check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        logger.critical(f"CRITICAL: Error starting bot: {e}", exc_info=True)

if __name__ == "__main__":
    main()
