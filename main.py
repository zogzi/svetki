import os
import json
import logging
import asyncio
from datetime import datetime, time
from pathlib import Path

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


intents = discord.Intents.default()
intents.messages = True

class HolidayBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        
        e
        self.config = self.load_config()
            
    async def on_ready(self):
        """Event fired when the bot is ready and connected."""
        logger.info(f"Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        
        self.check_holiday_messages.start()
        
    
    def load_config(self):
        """Load configuration from config.json or create default if not exists."""
        config_path = Path("config.json")
        
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
           
            default_config = {
                "channel_id": 1247269454652641313,  
                "message_time": {
                    "hour": 0,  
                    "minute": 0
                },
                "holiday_messages": {
                    "1-1": "Happy New Year! 🎉",
                    "12-25": "Merry Christmas! 🎄",
                    "2-14": "Happy Valentine's Day! ❤️",
                    "10-31": "Happy Halloween! 🎃",
                   
                }
            }
            
            
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)
                
            return default_config
    
    @tasks.loop(hours=1)  
    async def check_holiday_messages(self):
        """Check for holiday messages once per hour."""
        now = datetime.utcnow()
        date_key = f"{now.month}-{now.day}"
        
       
        if date_key in self.config["holiday_messages"]:
            
            target_hour = self.config["message_time"]["hour"]
            target_minute = self.config["message_time"]["minute"]
            
            
            if now.hour == target_hour and 0 <= now.minute < target_minute + 5:
               
                cache_file = Path(f"sent_{date_key}.tmp")
                if not cache_file.exists():
                   
                    holiday_message = self.config["holiday_messages"][date_key]
                    logger.info(f"Holiday detected: {date_key}, sending message: {holiday_message}")
                    await self.send_message(holiday_message)
                    
                    
                    cache_file.touch()
        
      
        self.cleanup_cache_files(now)
                
    def cleanup_cache_files(self, now):
        """Remove cache files from previous days."""
        current_date_key = f"{now.month}-{now.day}"
        for file in Path(".").glob("sent_*.tmp"):
            if file.name != f"sent_{current_date_key}.tmp":
                file.unlink()
                
    async def send_message(self, content):
        """Send a message to the configured channel."""
        channel = self.get_channel(self.config["channel_id"])
        
        if not channel:
            logger.error(f"Channel {self.config['channel_id']} not found")
            return
            
        try:
            logger.info(f"Sending message to {channel.name}")
            await channel.send(content)
            logger.info("Message sent successfully")
                
        except discord.errors.Forbidden:
            logger.error("Missing permissions to send messages to the channel")
        except Exception as e:
            logger.error(f"Error sending message: {e}")

def main():
    """Main function to start the bot."""
   
    token = os.getenv("DISCORD_BOT_TOKEN")
    
    if not token:
        logger.error("No token found. Set the DISCORD_BOT_TOKEN environment variable.")
        return
    
    
    bot = HolidayBot()
    
    try:
        logger.info("Starting bot...")
        bot.run(token)
    except discord.errors.LoginFailure:
        logger.error("Invalid token. Please check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

if __name__ == "__main__":
    main()
