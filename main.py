import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s: %(message)s')
logger = logging.getLogger("holiday_bot")

# Minimal intents
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True


class HolidayBot(discord.Client):
    """Minimal bot to send and auto-delete holiday messages"""
    
    __slots__ = ('_config', '_config_path', '_sent_today')
    
    def __init__(self):
        super().__init__(
            intents=intents,
            chunk_guilds_at_startup=False,
            max_messages=0
        )
        
        self._config_path = Path("config.json")
        self._config = self._load_config()
        self._sent_today = set()
    
    def _load_config(self) -> dict:
        """Load config file"""
        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error("config.json not found!")
            raise
        except Exception as e:
            logger.error(f"Config error: {e}")
            raise
    
    async def setup_hook(self):
        """Start background task"""
        self.check_and_send.start()
    
    async def on_ready(self):
        """Bot is ready"""
        logger.info(f"Bot ready: {self.user}")
        
        # Validate channel exists
        channel = self.get_channel(self._config["channel_id"])
        if not channel:
            logger.error(f"Channel {self._config['channel_id']} not found!")
    
    @tasks.loop(minutes=1)
    async def check_and_send(self):
        """Check if it's time to send a holiday message"""
        await self.wait_until_ready()
        
        now = datetime.now(timezone.utc)
        date_key = f"{now.month}-{now.day}"
        
        # Reset tracking at midnight
        if now.hour == 0 and now.minute == 0:
            self._sent_today.clear()
        
        # Skip if already sent today
        if date_key in self._sent_today:
            return
        
        # Check if today is a holiday
        message_text = self._config["holiday_messages"].get(date_key)
        if not message_text:
            return
        
        # Check if it's the right time (within 1 minute window)
        target_hour = self._config["message_time_utc"]["hour"]
        target_minute = self._config["message_time_utc"]["minute"]
        
        if now.hour != target_hour or now.minute != target_minute:
            return
        
        # Send the message
        channel = self.get_channel(self._config["channel_id"])
        if not channel:
            logger.error("Channel not found")
            return
        
        try:
            message = await channel.send(message_text)
            self._sent_today.add(date_key)
            logger.info(f"Sent: {date_key}")
            
            # Delete after 24 hours
            await message.delete(delay=86400)  # 24 hours = 86400 seconds
            
        except discord.Forbidden:
            logger.error("No permission to send/delete messages")
        except discord.HTTPException as e:
            logger.error(f"Failed to send message: {e}")


def main():
    """Run the bot"""
    token = os.getenv("DISCORD_BOT_TOKEN")
    
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not found in .env")
        return
    
    bot = HolidayBot()
    
    try:
        bot.run(token, log_handler=None, log_level=logging.CRITICAL)
    except discord.LoginFailure:
        print("ERROR: Invalid token")
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
