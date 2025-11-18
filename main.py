import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Set
from functools import lru_cache
from weakref import WeakSet

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Load environment once
load_dotenv()

# Improved logging setup
def setup_minimal_logging():
    """Logging that captures connection issues"""
    logger = logging.getLogger("holiday_bot")
    if logger.handlers:
        return logger
    
    # Changed to WARNING to catch connection issues
    logger.setLevel(logging.WARNING)
    
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s: %(message)s'))
    logger.addHandler(console)
    logger.propagate = False
    
    return logger

logger = setup_minimal_logging()

# Absolute minimal intents
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True

class MemoryOptimizedCache:
    """Ultra-lightweight caching with automatic cleanup"""
    __slots__ = ('_data', '_timestamps', '_max_size')
    
    def __init__(self, max_size: int = 50):
        self._data = {}
        self._timestamps = {}
        self._max_size = max_size
    
    def get(self, key: str) -> Any:
        if key in self._data:
            self._timestamps[key] = datetime.now(timezone.utc).timestamp()
            return self._data[key]
        return None
    
    def set(self, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc).timestamp()
        
        # Cleanup if at capacity
        if len(self._data) >= self._max_size:
            oldest_key = min(self._timestamps.keys(), key=lambda k: self._timestamps[k])
            self._data.pop(oldest_key, None)
            self._timestamps.pop(oldest_key, None)
        
        self._data[key] = value
        self._timestamps[key] = now
    
    def remove(self, key: str) -> None:
        self._data.pop(key, None)
        self._timestamps.pop(key, None)
    
    def cleanup_old(self, max_age_hours: int = 48) -> None:
        """Remove entries older than max_age_hours"""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        old_keys = [k for k, ts in self._timestamps.items() if ts < cutoff]
        for key in old_keys:
            self.remove(key)

class HolidayBot(commands.Bot):
    __slots__ = (
        '_config', '_config_mtime', '_config_path', '_default_config',
        '_message_cache', '_channel_cache', '_last_config_check',
        '_holiday_keys_cache', '_sent_today_cache', '_last_check_date',
        '_connection_errors'
    )
    
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            chunk_guilds_at_startup=False,
            max_messages=10,
            help_command=None,
            case_insensitive=False
        )
        
        self._config = None
        self._config_mtime = 0
        self._config_path = Path("config.json")
        self._message_cache = MemoryOptimizedCache(max_size=20)
        self._channel_cache = None
        self._last_config_check = 0
        self._holiday_keys_cache = None
        self._sent_today_cache = set()
        self._last_check_date = None
        self._connection_errors = 0
        
        self._default_config = {
            "channel_id": 1374479725392564296,
            "message_time_utc": {"hour": 22, "minute": 0},
            "delete_after_hours": 24,
            "holiday_messages": {
                "1-1": "Test Holiday! 🎉",
                "2-14": "Happy Valentine's Day! ❤️",
                "12-25": "Merry Christmas! 🎄"
            }
        }
    
    @property
    def config(self) -> Dict[str, Any]:
        """Lazy config loading with minimal file system checks"""
        now = datetime.now(timezone.utc).timestamp()
        
        # Only check file every 5 minutes to reduce I/O
        if now - self._last_config_check < 300:
            return self._config or self._default_config
        
        self._last_config_check = now
        
        try:
            if self._config_path.exists():
                mtime = self._config_path.stat().st_mtime
                if mtime != self._config_mtime:
                    with open(self._config_path, 'r', encoding='utf-8') as f:
                        self._config = json.load(f)
                    self._config_mtime = mtime
                    self._holiday_keys_cache = None
                    self._channel_cache = None  # FIXED: Clear channel cache on config change
                    logger.info("Config reloaded")
            elif self._config is None:
                try:
                    with open(self._config_path, 'w', encoding='utf-8') as f:
                        json.dump(self._default_config, f, indent=2)
                    self._config = self._default_config
                    logger.info("Created default config")
                except OSError as e:
                    logger.warning(f"Could not create config file: {e}")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Config load error: {e}")
        
        return self._config or self._default_config
    
    @property
    def holiday_keys(self) -> Set[str]:
        """Cached holiday keys"""
        if self._holiday_keys_cache is None:
            self._holiday_keys_cache = set(self.config.get("holiday_messages", {}).keys())
        return self._holiday_keys_cache
    
    async def get_target_channel(self) -> Optional[discord.TextChannel]:
        """Cached channel retrieval"""
        if self._channel_cache is None:
            channel_id = self.config.get("channel_id")
            if channel_id:
                self._channel_cache = self.get_channel(channel_id)
                if self._channel_cache is None:
                    logger.error(f"Could not find channel {channel_id}")
        
        return self._channel_cache if isinstance(self._channel_cache, discord.TextChannel) else None
    
    @staticmethod
    def get_date_key() -> str:
        """Get current date key efficiently"""
        now = datetime.now(timezone.utc)
        return f"{now.month}-{now.day}"
    
    @staticmethod
    def is_message_time(target_hour: int, target_minute: int, tolerance_minutes: int = 10) -> bool:
        """Check if current time is within message sending window"""
        now = datetime.now(timezone.utc)
        target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        return target_time <= now <= target_time + timedelta(minutes=tolerance_minutes)
    
    async def setup_hook(self) -> None:
        """Minimal setup with error handling"""
        try:
            self.check_holiday_messages.change_interval(minutes=15)
            self.check_holiday_messages.start()
            
            self.cleanup_task.change_interval(hours=6)
            self.cleanup_task.start()
            
            logger.info("Bot setup complete")
        except Exception as e:
            logger.error(f"Setup failed: {e}")
            raise  # Re-raise to prevent silent failures
    
    async def on_ready(self) -> None:
        """Ready handler with validation"""
        logger.info(f"Bot online as {self.user}")
        
        # Pre-cache and validate channel
        channel = await self.get_target_channel()
        if channel:
            logger.info(f"Target channel: {channel.name} ({channel.id})")
        else:
            logger.error("Could not find target channel!")
        
        # Clear daily cache on startup
        self._sent_today_cache.clear()
        self._connection_errors = 0
    
    async def on_disconnect(self) -> None:
        """Log disconnections"""
        self._connection_errors += 1
        logger.warning(f"Disconnected from Discord (count: {self._connection_errors})")
    
    async def on_resumed(self) -> None:
        """Log reconnections"""
        logger.info("Reconnected to Discord")
        self._connection_errors = 0
    
    @tasks.loop(minutes=15)
    async def check_holiday_messages(self):
        """Optimized holiday message checking"""
        await self.wait_until_ready()
        
        # FIXED: Clear sent cache if date has changed
        now = datetime.now(timezone.utc)
        current_date = f"{now.year}-{now.month}-{now.day}"
        if self._last_check_date != current_date:
            self._sent_today_cache.clear()
            self._last_check_date = current_date
            logger.info(f"New day: {current_date}")
        
        date_key = self.get_date_key()
        
        # Skip if not a holiday or already sent today
        if date_key not in self.holiday_keys or date_key in self._sent_today_cache:
            return
        
        # Check if it's time to send
        msg_time = self.config.get("message_time_utc", {})
        if not self.is_message_time(
            msg_time.get("hour", 0), 
            msg_time.get("minute", 0)
        ):
            return
        
        logger.info(f"Sending holiday message for {date_key}")
        
        # Send message
        content = self.config["holiday_messages"][date_key]
        message = await self.send_holiday_message(content)
        
        if message:
            # Track in lightweight cache
            self._message_cache.set(date_key, {
                "id": message.id,
                "timestamp": datetime.now(timezone.utc).timestamp()
            })
            self._sent_today_cache.add(date_key)
            logger.info(f"Holiday message sent successfully")
        else:
            logger.error(f"Failed to send holiday message for {date_key}")
    
    @tasks.loop(hours=6)
    async def cleanup_task(self):
        """Consolidated cleanup task"""
        await self.wait_until_ready()
        
        # Clean old message cache entries
        self._message_cache.cleanup_old(max_age_hours=48)
        
        # Delete old messages
        await self._delete_old_messages()
        
        logger.info("Cleanup task completed")
    
    async def _delete_old_messages(self) -> None:
        """Efficient message deletion"""
        channel = await self.get_target_channel()
        if not channel:
            return
        
        delete_after_hours = self.config.get("delete_after_hours", 24)
        cutoff_timestamp = datetime.now(timezone.utc).timestamp() - (delete_after_hours * 3600)
        
        # Find messages to delete
        to_delete = []
        for date_key in list(self._message_cache._data.keys()):
            msg_data = self._message_cache.get(date_key)
            if msg_data and msg_data["timestamp"] < cutoff_timestamp:
                to_delete.append((date_key, msg_data["id"]))
        
        # Delete messages with rate limiting
        for date_key, message_id in to_delete:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                logger.info(f"Deleted old message: {date_key}")
                await asyncio.sleep(1)
            except discord.NotFound:
                logger.info(f"Message already deleted: {date_key}")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Could not delete message {date_key}: {e}")
            finally:
                self._message_cache.remove(date_key)
    
    async def send_holiday_message(self, content: str) -> Optional[discord.Message]:
        """Optimized message sending"""
        channel = await self.get_target_channel()
        if not channel:
            logger.error("Cannot send message: channel not found")
            return None
        
        try:
            return await channel.send(content)
        except discord.Forbidden as e:
            logger.error(f"No permission to send message: {e}")
            return None
        except discord.HTTPException as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    async def close(self) -> None:
        """Clean shutdown"""
        logger.info("Shutting down bot")
        self._message_cache._data.clear()
        self._sent_today_cache.clear()
        await super().close()

def main():
    """Ultra-minimal main function"""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN missing in .env file")
        return
    
    bot = HolidayBot()
    
    try:
        print("Starting Holiday Bot...")
        bot.run(
            token,
            log_handler=None,
            log_level=logging.CRITICAL
        )
    except discord.LoginFailure:
        print("ERROR: Invalid Discord token")
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"ERROR: {e}")

if __name__ == "__main__":
    main()
