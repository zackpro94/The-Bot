import asyncio
import re
import os
import json
import logging
import time
import random
from typing import Dict, Set, Optional, Any, List
from dataclasses import dataclass, asdict
from datetime import datetime
from urllib.parse import urlparse
import requests
from playwright.async_api import async_playwright, Browser, Page
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import database

# ==================== CONFIGURATION ====================
# Environment variables override defaults for security
# WARNING: Set these via environment variables in production!
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATA_DIR = os.getenv("DATA_DIR", "")
CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json") if DATA_DIR else "bot_config.json"
LOG_FILE = os.path.join(DATA_DIR, "bot.log") if DATA_DIR else "bot.log"
HISTORY_FILE = "posted_auctions.json"
DAILY_POSTS_FILE = "daily_posts.json"
SCRAPE_CACHE_FILE = "scrape_cache.json"
ANALYTICS_FILE = "analytics.json"
DEFAULT_TARGET_URL = "https://auction.et/category/auctions/38"
CHECK_INTERVAL = 300
MAX_RETRIES = 3
RETRY_DELAY = 5
# ========================================================

# --- Proxy Settings ---
USE_PROXY = False
PROXIES = {
    "http": "http://127.0.0.1:10808",
    "https": "http://127.0.0.1:10808"
}
# ========================================================

# ==================== LOGGING SETUP ====================
def setup_logging() -> logging.Logger:
    """Configure logging with file rotation and console handlers."""
    from logging.handlers import RotatingFileHandler
    
    logger = logging.getLogger("AuctionBot")
    logger.setLevel(logging.INFO)
    
    # File handler with rotation (10MB max, keep 5 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# Check that credentials are configured
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN is not set in environment variables or .env file!")
if not CHAT_ID:
    logger.error("❌ CHAT_ID is not set in environment variables or .env file!")
if not ADMIN_CHAT_ID:
    logger.warning("⚠️  ADMIN_CHAT_ID is not set. Admin features will be disabled.")

# Global bot control flag for web interface
bot_running_flag = False
last_heartbeat = None  # Updated each scrape cycle
trigger_scrape_now = False  # Set to True when manual scrape is requested
# ========================================================

@dataclass
class BotConfig:
    """Bot configuration dataclass."""
    target_urls: List[str]
    check_interval: int
    hourly_post_limit: int = 10  # Default 10 posts per hour per category
    
    # Filter options
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    auction_types: List[str] = None  # e.g., ["Open Bidding", "Sealed Bid"], None = all
    include_keywords: List[str] = None  # Must contain these keywords
    exclude_keywords: List[str] = None  # Must not contain these keywords
    
    # Schedule options
    business_hours_start: Optional[str] = None  # e.g., "09:00"
    business_hours_end: Optional[str] = None  # e.g., "17:00"
    business_days: List[int] = None  # 0=Monday, 6=Sunday, None = all days
    
    # Channel support
    chat_ids: List[str] = None  # Multiple Telegram channels
    
    # Custom post template
    post_template: str = """📢 <b>New Auction Listed!</b>

📦 <b>Asset:</b> {name}
🆔 <b>Lot No:</b> {lot_no}
💰 <b>Initial Price:</b> {initial_price}
⏳ <b>End Date:</b> {end_date_time}
⚡ <b>Type:</b> {auction_type}

🔗 <a href='{target_url}'>View details</a>"""
    
    def __post_init__(self):
        # Initialize list fields with defaults
        if self.auction_types is None:
            self.auction_types = []
        if self.include_keywords is None:
            self.include_keywords = []
        if self.exclude_keywords is None:
            self.exclude_keywords = []
        if self.business_days is None:
            self.business_days = []
        if self.chat_ids is None:
            self.chat_ids = [CHAT_ID]  # Default to single channel
        if self.post_template is None or not self.post_template.strip():
            self.post_template = """📢 <b>New Auction Listed!</b>

📦 <b>Asset:</b> {name}
🆔 <b>Lot No:</b> {lot_no}
💰 <b>Initial Price:</b> {initial_price}
⏳ <b>End Date:</b> {end_date_time}
⚡ <b>Type:</b> {auction_type}

🔗 <a href='{target_url}'>View details</a>"""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BotConfig':
        # Handle backward compatibility with old single URL format
        if 'target_url' in data and 'target_urls' not in data:
            data['target_urls'] = [data['target_url']]
            del data['target_url']
        
        # Handle backward compatibility with old single CHAT_ID
        if 'chat_ids' not in data:
            data['chat_ids'] = [CHAT_ID]
        
        # Add defaults for new fields
        if 'hourly_post_limit' not in data:
            data['hourly_post_limit'] = data.get('daily_post_limit', 10)
        if 'daily_post_limit' in data:
            del data['daily_post_limit']
        if 'min_price' not in data:
            data['min_price'] = None
        if 'max_price' not in data:
            data['max_price'] = None
        if 'auction_types' not in data:
            data['auction_types'] = []
        if 'include_keywords' not in data:
            data['include_keywords'] = []
        if 'exclude_keywords' not in data:
            data['exclude_keywords'] = []
        if 'business_hours_start' not in data:
            data['business_hours_start'] = None
        if 'business_hours_end' not in data:
            data['business_hours_end'] = None
        if 'business_days' not in data:
            data['business_days'] = []
        if 'post_template' not in data or not data['post_template']:
            data['post_template'] = """📢 <b>New Auction Listed!</b>

📦 <b>Asset:</b> {name}
🆔 <b>Lot No:</b> {lot_no}
💰 <b>Initial Price:</b> {initial_price}
⏳ <b>End Date:</b> {end_date_time}
⚡ <b>Type:</b> {auction_type}

🔗 <a href='{target_url}'>View details</a>"""
        
        return cls(**data)

def validate_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def load_config() -> BotConfig:
    """Load bot configuration with environment variable overrides."""
    config_data = {}
    
    # 1. Load from file if it exists
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            config_data = {}
            
    # 2. Override with environment variables
    # Target URLs
    env_urls = os.getenv("BOT_TARGET_URLS") or os.getenv("TARGET_URLS") or os.getenv("TARGET_URL")
    if env_urls:
        if env_urls.strip().startswith("[") and env_urls.strip().endswith("]"):
            try:
                config_data["target_urls"] = json.loads(env_urls)
            except Exception:
                config_data["target_urls"] = [url.strip() for url in env_urls.split(",") if url.strip()]
        else:
            config_data["target_urls"] = [url.strip() for url in env_urls.split(",") if url.strip()]
            
    # Check Interval
    env_interval = os.getenv("BOT_CHECK_INTERVAL") or os.getenv("CHECK_INTERVAL")
    if env_interval:
        try:
            config_data["check_interval"] = int(env_interval)
        except ValueError:
            pass
            
    # Hourly Post Limit
    env_limit = os.getenv("BOT_HOURLY_POST_LIMIT")
    if env_limit:
        try:
            config_data["hourly_post_limit"] = int(env_limit)
        except ValueError:
            pass
            
    # Min/Max Price
    env_min_price = os.getenv("BOT_MIN_PRICE")
    if env_min_price:
        try:
            config_data["min_price"] = float(env_min_price)
        except ValueError:
            pass
    env_max_price = os.getenv("BOT_MAX_PRICE")
    if env_max_price:
        try:
            config_data["max_price"] = float(env_max_price)
        except ValueError:
            pass
            
    # Auction Types
    env_types = os.getenv("BOT_AUCTION_TYPES")
    if env_types:
        config_data["auction_types"] = [t.strip() for t in env_types.split(",") if t.strip()]
        
    # Keywords
    env_include = os.getenv("BOT_INCLUDE_KEYWORDS")
    if env_include:
        config_data["include_keywords"] = [k.strip() for k in env_include.split(",") if k.strip()]
    env_exclude = os.getenv("BOT_EXCLUDE_KEYWORDS")
    if env_exclude:
        config_data["exclude_keywords"] = [k.strip() for k in env_exclude.split(",") if k.strip()]
        
    # Business Hours
    env_hours_start = os.getenv("BOT_BUSINESS_HOURS_START")
    if env_hours_start:
        config_data["business_hours_start"] = env_hours_start.strip()
    env_hours_end = os.getenv("BOT_BUSINESS_HOURS_END")
    if env_hours_end:
        config_data["business_hours_end"] = env_hours_end.strip()
        
    # Business Days
    env_days = os.getenv("BOT_BUSINESS_DAYS")
    if env_days:
        try:
            config_data["business_days"] = [int(d.strip()) for d in env_days.split(",") if d.strip()]
        except ValueError:
            pass
            
    # Chat IDs
    env_chat_ids = os.getenv("BOT_CHAT_IDS")
    if env_chat_ids:
        config_data["chat_ids"] = [c.strip() for c in env_chat_ids.split(",") if c.strip()]
        
    # Post Template
    env_template = os.getenv("BOT_POST_TEMPLATE")
    if env_template:
        config_data["post_template"] = env_template
        
    # 3. Handle backward compatibility and defaults
    if "target_url" in config_data and "target_urls" not in config_data:
        config_data["target_urls"] = [config_data["target_url"]]
        del config_data["target_url"]
        
    if "target_urls" not in config_data or not config_data["target_urls"]:
        config_data["target_urls"] = [DEFAULT_TARGET_URL]
        
    # Validate URLs
    config_data["target_urls"] = [url for url in config_data["target_urls"] if validate_url(url)]
    if not config_data["target_urls"]:
        config_data["target_urls"] = [DEFAULT_TARGET_URL]
        
    # Validate interval
    interval = config_data.get("check_interval", CHECK_INTERVAL)
    try:
        interval = int(interval)
        if interval < 60:
            interval = 60
    except (ValueError, TypeError):
        interval = CHECK_INTERVAL
    config_data["check_interval"] = interval
    
    return BotConfig.from_dict(config_data)

def save_config(config: BotConfig) -> bool:
    """Save bot configuration to JSON file."""
    try:
        with open(CONFIG_FILE, "w", encoding='utf-8') as f:
            json.dump(config.to_dict(), f, indent=2)
        logger.info("Configuration saved successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False

def load_posted_auctions() -> List[str]:
    """Load posted auction lot numbers from database."""
    return database.load_posted_auctions()

def save_posted_auctions(lot_numbers: List[str]):
    """Save posted auction lot numbers to database."""
    database.save_posted_auctions(lot_numbers)

def add_posted_auction(lot_no: str, category: str = "", name: str = "", price: str = ""):
    """Add a single posted auction to database."""
    database.add_posted_auction(lot_no, category, name, price)

def load_daily_posts() -> Dict[str, Dict[str, int]]:
    """Load daily post counts from database."""
    return database.load_daily_posts()

def save_daily_posts(daily_posts: Dict[str, Dict[str, int]]):
    """Save daily post counts to database."""
    database.save_daily_posts(daily_posts)

def get_daily_post_count(date: str, category: str) -> int:
    """Get post count for a specific date and category."""
    return database.get_daily_post_count(date, category)

def increment_daily_post(date: str, category: str):
    """Increment daily post count for a category."""
    database.increment_daily_post(date, category)

def load_scrape_cache() -> Dict[str, Dict[str, Any]]:
    """Load scrape cache from database."""
    return database.load_scrape_cache()

def save_scrape_cache(cache: Dict[str, Dict[str, Any]]):
    """Save scrape cache to database."""
    database.save_scrape_cache(cache)

def clear_scrape_cache():
    """Clear scrape cache."""
    database.clear_scrape_cache()

def load_analytics() -> Dict[str, Any]:
    """Load analytics from database."""
    return database.load_analytics()

def save_analytics(analytics: Dict[str, Any]):
    """Save analytics to database."""
    database.save_analytics(analytics)

def record_post_attempt(success: bool, category: str = "default"):
    """Record a post attempt in analytics."""
    database.record_post_attempt(success, category)

def parse_price(price_str: str) -> Optional[float]:
    """Parse price string to float."""
    if not price_str or price_str == "N/A":
        return None
    try:
        # Remove currency symbols and commas
        cleaned = price_str.replace(',', '').replace('ETB', '').replace('Br', '').strip()
        return float(cleaned)
    except (ValueError, AttributeError):
        return None

def passes_filters(
    name: str,
    price_str: str,
    auction_type: str,
    config: BotConfig
) -> bool:
    """Check if auction passes all configured filters."""
    # Price range filter
    if config.min_price is not None or config.max_price is not None:
        price = parse_price(price_str)
        if price is not None:
            if config.min_price is not None and price < config.min_price:
                return False
            if config.max_price is not None and price > config.max_price:
                return False
    
    # Auction type filter
    if config.auction_types:
        if auction_type not in config.auction_types:
            return False
    
    # Include keywords filter
    if config.include_keywords:
        name_lower = name.lower()
        if not any(keyword.lower() in name_lower for keyword in config.include_keywords):
            return False
    
    # Exclude keywords filter
    if config.exclude_keywords:
        name_lower = name.lower()
        if any(keyword.lower() in name_lower for keyword in config.exclude_keywords):
            return False
    
    return True

def is_within_schedule(config: BotConfig) -> bool:
    """Check if current time is within configured business hours."""
    if not config.business_hours_start and not config.business_hours_end and not config.business_days:
        return True  # No schedule configured, always allow
    
    now = datetime.now()
    
    # Check day of week
    if config.business_days:
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        if current_day not in config.business_days:
            return False
    
    # Check time range
    if config.business_hours_start and config.business_hours_end:
        current_time = now.strftime("%H:%M")
        if not (config.business_hours_start <= current_time <= config.business_hours_end):
            return False
    
    return True

def retry_request(func, *args, max_retries: int = MAX_RETRIES, **kwargs) -> Optional[requests.Response]:
    """Retry HTTP request with improved exponential backoff."""
    for attempt in range(max_retries):
        try:
            response = func(*args, **kwargs)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                # Rate limited - use exponential backoff with jitter
                wait_time = min(RETRY_DELAY * (2 ** attempt) + random.uniform(0, 2), 60)
                logger.warning(f"Rate limited, waiting {wait_time:.1f}s before retry {attempt + 1}/{max_retries}")
                time.sleep(wait_time)
            elif 400 <= response.status_code < 500:
                # Client errors (400, 401, 403, 404) are permanent — retrying won't help
                logger.warning(f"Client error {response.status_code} (not retrying): {response.text[:200]}")
                return response
            else:
                logger.warning(f"Request failed with status {response.status_code}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    wait_time = min(RETRY_DELAY * (2 ** attempt), 30)
                    time.sleep(wait_time)
        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                wait_time = min(RETRY_DELAY * (2 ** attempt), 30)
                time.sleep(wait_time)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error: {e}, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                wait_time = min(RETRY_DELAY * (2 ** attempt), 30)
                time.sleep(wait_time)
        except Exception as e:
            logger.error(f"Unexpected error in request: {e}")
            break
    return None

def send_telegram_text_message(chat_id: str, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """Send a text message to a Telegram chat with retry logic."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    proxies = PROXIES if USE_PROXY else None
    logger.info(f"Sending message to chat {chat_id} (proxy: {USE_PROXY})")
    
    # Add delay to avoid rate limiting
    time.sleep(1)
    
    try:
        response = retry_request(requests.post, url, json=payload, proxies=proxies, timeout=15)
        
        if response and response.status_code == 200:
            logger.info(f"Message sent to chat {chat_id}")
            return True
        else:
            # Don't log errors for personal chat failures (user may not have started the bot)
            if str(chat_id) != str(ADMIN_CHAT_ID):
                logger.error(f"Failed to send message to chat {chat_id} - Status: {response.status_code if response else 'No response'}")
                if response and response.text:
                    logger.error(f"Response: {response.text}")
            else:
                logger.warning(f"Cannot send to admin chat {chat_id} - user may need to send /start to bot first (Status: {response.status_code if response else 'No response'})")
            return False
    except Exception as e:
        logger.error(f"Exception sending message to chat {chat_id}: {e}")
        return False

def answer_telegram_callback_query(callback_query_id: str, text: Optional[str] = None):
    """Answer a Telegram callback query to clear the loading state on the button click."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id
    }
    if text:
        payload["text"] = text
    proxies = PROXIES if USE_PROXY else None
    try:
        retry_request(requests.post, url, json=payload, proxies=proxies, timeout=10)
    except Exception as e:
        logger.error(f"Error answering callback query: {e}")

def get_telegram_updates(offset: int = 0) -> List[Dict[str, Any]]:
    """Get updates from Telegram bot with retry logic."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 0, "limit": 100}
    
    proxies = PROXIES if USE_PROXY else None
    response = retry_request(requests.get, url, params=params, proxies=proxies, timeout=15)
    
    if response and response.status_code == 200:
        return response.json().get("result", [])
    return []

def is_authorized(user_id: Optional[int]) -> bool:
    """Check if user is authorized to use admin commands."""
    if ADMIN_CHAT_ID is None:
        return True
    return str(user_id) == str(ADMIN_CHAT_ID)

def get_menu_keyboard() -> Dict[str, Any]:
    """Return the inline keyboard markup for the bot menu."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "menu_status"},
                {"text": "📈 Stats", "callback_data": "menu_stats"}
            ],
            [
                {"text": "🔄 Scrape Now", "callback_data": "menu_scrape"},
                {"text": "⏱️ Uptime", "callback_data": "menu_uptime"}
            ]
        ]
    }

def handle_command(message: Dict[str, Any], config: BotConfig) -> BotConfig:
    """Handle incoming Telegram commands."""
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    user_id = message.get("from", {}).get("id")
    
    # Ignore messages from bots (including our own bot's messages)
    if message.get("from", {}).get("is_bot", False):
        return config
    
    # Only process commands from private chats with authorized user
    # Ignore messages from channels/groups to prevent processing our own messages
    if message["chat"].get("type") != "private":
        return config
    
    if text.startswith("/start") or text.startswith("/menu"):
        send_telegram_text_message(chat_id, 
            "🤖 <b>Welcome to Auction Bot Menu!</b>\n\n"
            "Use the buttons below to control and monitor the bot, or type `/help` for a list of all commands.",
            reply_markup=get_menu_keyboard()
        )
    
    elif text.startswith("/help"):
        send_telegram_text_message(chat_id,
            "📋 <b>Available Commands:</b>\n\n"
            "/addurl <url> - Add target auction URL\n"
            "  Example: /addurl https://auction.et/category/auctions/39\n\n"
            "/listurls - List all target URLs\n\n"
            "/removeurl <index> - Remove URL by index\n"
            "  Example: /removeurl 1 (removes first URL)\n\n"
            "/setlimit <number> - Set daily post limit per category\n"
            "  Example: /setlimit 10 (max 10 posts per category per day)\n\n"
            "/filter - Manage filters (price, type, keywords)\n"
            "  /filter price 1000-5000\n"
            "  /filter type Open Bidding\n"
            "  /filter include car,house\n"
            "  /filter exclude test,demo\n"
            "  /filter clear\n\n"
            "/schedule - Set business hours/days\n"
            "  /schedule hours 09:00-17:00\n"
            "  /schedule days 0,1,2,3,4 (Mon-Fri)\n"
            "  /schedule clear\n\n"
            "/channels - Manage Telegram channels\n"
            "  /channels add <chat_id>\n"
            "  /channels list\n"
            "  /channels remove <index>\n\n"
            "/status - Show current configuration\n\n"
            "/help - Show this help message"
        )
    
    elif text.startswith("/status"):
        posted_count = len(load_posted_auctions())
        urls_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(config.target_urls)])
        channels_text = "\n".join([f"{i+1}. {cid}" for i, cid in enumerate(config.chat_ids)])
        
        filter_info = "None"
        if config.min_price or config.max_price or config.auction_types or config.include_keywords or config.exclude_keywords:
            filter_parts = []
            if config.min_price or config.max_price:
                filter_parts.append(f"Price: {config.min_price or '0'}-{config.max_price or '∞'}")
            if config.auction_types:
                filter_parts.append(f"Types: {', '.join(config.auction_types)}")
            if config.include_keywords:
                filter_parts.append(f"Include: {', '.join(config.include_keywords)}")
            if config.exclude_keywords:
                filter_parts.append(f"Exclude: {', '.join(config.exclude_keywords)}")
            filter_info = "\n".join(filter_parts)
        
        schedule_info = "Always"
        if config.business_hours_start and config.business_hours_end:
            schedule_info = f"{config.business_hours_start}-{config.business_hours_end}"
        if config.business_days:
            days_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            days = ", ".join([days_map[d] for d in config.business_days])
            schedule_info += f" ({days})"
        
        send_telegram_text_message(chat_id,
            f"📊 <b>Current Status:</b>\n\n"
            f"🔗 Target URLs ({len(config.target_urls)}):\n{urls_text}\n\n"
            f"📢 Channels ({len(config.chat_ids)}):\n{channels_text}\n\n"
            f"⏱️ Check Interval: {config.check_interval} seconds\n"
            f"📊 Hourly Post Limit: {config.hourly_post_limit} per category\n"
            f"� Filters:\n{filter_info}\n\n"
            f"⏰ Schedule: {schedule_info}\n\n"
            f"📝 Posted Auctions: {posted_count}"
        )
    
    elif text.startswith("/listurls"):
        if not config.target_urls:
            send_telegram_text_message(chat_id, "📋 No target URLs configured.")
        else:
            urls_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(config.target_urls)])
            send_telegram_text_message(chat_id, f"📋 <b>Target URLs:</b>\n\n{urls_text}")
    
    elif text.startswith("/addurl"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to add URL")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /addurl <url>")
            return config
        
        new_url = parts[1].strip()
        if not validate_url(new_url):
            send_telegram_text_message(chat_id, "❌ Invalid URL format")
            return config
        
        if new_url in config.target_urls:
            send_telegram_text_message(chat_id, "❌ URL already exists in the list")
            return config
        
        config.target_urls.append(new_url)
        if save_config(config):
            send_telegram_text_message(chat_id, f"✅ URL added. Total URLs: {len(config.target_urls)}")
            logger.info(f"URL added by user {user_id}: {new_url}")
        else:
            send_telegram_text_message(chat_id, "❌ Failed to save configuration")
    
    elif text.startswith("/removeurl"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to remove URL")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /removeurl <index>")
            return config
        
        try:
            index = int(parts[1].strip()) - 1  # Convert to 0-based index
            if index < 0 or index >= len(config.target_urls):
                send_telegram_text_message(chat_id, f"❌ Invalid index. Use 1-{len(config.target_urls)}")
                return config
            
            removed_url = config.target_urls.pop(index)
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Removed: {removed_url}\nRemaining URLs: {len(config.target_urls)}")
                logger.info(f"URL removed by user {user_id}: {removed_url}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        except ValueError:
            send_telegram_text_message(chat_id, "❌ Invalid index format. Use a number.")
    
    elif text.startswith("/setlimit"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to set limit")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /setlimit <number>")
            return config
        
        try:
            limit = int(parts[1].strip())
            if limit < 1:
                send_telegram_text_message(chat_id, "❌ Limit must be at least 1")
                return config
            
            config.hourly_post_limit = limit
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Hourly post limit set to: {limit} per category")
                logger.info(f"Hourly post limit set by user {user_id}: {limit}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        except ValueError:
            send_telegram_text_message(chat_id, "❌ Invalid number format")
    
    # Legacy /seturl command for backward compatibility
    elif text.startswith("/seturl"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to change URL")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /seturl <url>")
            return config
        
        new_url = parts[1].strip()
        if not validate_url(new_url):
            send_telegram_text_message(chat_id, "❌ Invalid URL format")
            return config
        
        # Replace all URLs with this single URL
        config.target_urls = [new_url]
        if save_config(config):
            send_telegram_text_message(chat_id, f"✅ Target URL set to: {new_url}\n(Use /addurl to add more URLs)")
            logger.info(f"Target URL set by user {user_id}: {new_url}")
        else:
            send_telegram_text_message(chat_id, "❌ Failed to save configuration")
    
    # Filter management commands
    elif text.startswith("/filter"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to set filters")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /filter <type> <value>\nTypes: price, type, include, exclude, clear")
            return config
        
        filter_type = parts[1].lower()
        
        if filter_type == "clear":
            config.min_price = None
            config.max_price = None
            config.auction_types = []
            config.include_keywords = []
            config.exclude_keywords = []
            if save_config(config):
                send_telegram_text_message(chat_id, "✅ All filters cleared")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        elif filter_type == "price":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /filter price <min>-<max>\nExample: /filter price 1000-5000")
                return config
            try:
                price_range = parts[2].split('-')
                config.min_price = float(price_range[0]) if price_range[0] else None
                config.max_price = float(price_range[1]) if len(price_range) > 1 and price_range[1] else None
                if save_config(config):
                    send_telegram_text_message(chat_id, f"✅ Price filter set: {config.min_price or '0'}-{config.max_price or '∞'}")
                else:
                    send_telegram_text_message(chat_id, "❌ Failed to save configuration")
            except ValueError:
                send_telegram_text_message(chat_id, "❌ Invalid price format")
        
        elif filter_type == "type":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /filter type <auction_type>\nExample: /filter type Open Bidding")
                return config
            auction_type = parts[2].strip()
            config.auction_types = [auction_type]
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Auction type filter set: {auction_type}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        elif filter_type == "include":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /filter include <keyword1,keyword2>")
                return config
            keywords = [k.strip() for k in parts[2].split(',')]
            config.include_keywords = keywords
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Include keywords set: {', '.join(keywords)}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        elif filter_type == "exclude":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /filter exclude <keyword1,keyword2>")
                return config
            keywords = [k.strip() for k in parts[2].split(',')]
            config.exclude_keywords = keywords
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Exclude keywords set: {', '.join(keywords)}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        else:
            send_telegram_text_message(chat_id, "❌ Invalid filter type. Use: price, type, include, exclude, clear")
    
    # Schedule management commands
    elif text.startswith("/schedule"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to set schedule")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /schedule <type> <value>\nTypes: hours, days, clear")
            return config
        
        schedule_type = parts[1].lower()
        
        if schedule_type == "clear":
            config.business_hours_start = None
            config.business_hours_end = None
            config.business_days = []
            if save_config(config):
                send_telegram_text_message(chat_id, "✅ Schedule cleared (posting always allowed)")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        elif schedule_type == "hours":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /schedule hours <start>-<end>\nExample: /schedule hours 09:00-17:00")
                return config
            try:
                hours = parts[2].split('-')
                config.business_hours_start = hours[0].strip()
                config.business_hours_end = hours[1].strip() if len(hours) > 1 else hours[0].strip()
                if save_config(config):
                    send_telegram_text_message(chat_id, f"✅ Business hours set: {config.business_hours_start}-{config.business_hours_end}")
                else:
                    send_telegram_text_message(chat_id, "❌ Failed to save configuration")
            except Exception:
                send_telegram_text_message(chat_id, "❌ Invalid time format")
        
        elif schedule_type == "days":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /schedule days <0,1,2,3,4,5,6>\nExample: /schedule days 0,1,2,3,4 (Mon-Fri)")
                return config
            try:
                days = [int(d.strip()) for d in parts[2].split(',')]
                if all(0 <= d <= 6 for d in days):
                    config.business_days = days
                    if save_config(config):
                        days_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
                        days_str = ", ".join([days_map[d] for d in days])
                        send_telegram_text_message(chat_id, f"✅ Business days set: {days_str}")
                    else:
                        send_telegram_text_message(chat_id, "❌ Failed to save configuration")
                else:
                    send_telegram_text_message(chat_id, "❌ Invalid day numbers (use 0-6)")
            except ValueError:
                send_telegram_text_message(chat_id, "❌ Invalid day format")
        
        else:
            send_telegram_text_message(chat_id, "❌ Invalid schedule type. Use: hours, days, clear")
    
    # Channel management commands
    elif text.startswith("/channels"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to manage channels")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            send_telegram_text_message(chat_id, "❌ Usage: /channels <action> <value>\nActions: add, list, remove")
            return config
        
        action = parts[1].lower()
        
        if action == "list":
            if not config.chat_ids:
                send_telegram_text_message(chat_id, "📋 No channels configured.")
            else:
                channels_text = "\n".join([f"{i+1}. {cid}" for i, cid in enumerate(config.chat_ids)])
                send_telegram_text_message(chat_id, f"📋 <b>Channels:</b>\n\n{channels_text}")
        
        elif action == "add":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /channels add <chat_id>")
                return config
            new_channel = parts[2].strip()
            if new_channel in config.chat_ids:
                send_telegram_text_message(chat_id, "❌ Channel already exists")
                return config
            config.chat_ids.append(new_channel)
            if save_config(config):
                send_telegram_text_message(chat_id, f"✅ Channel added. Total channels: {len(config.chat_ids)}")
            else:
                send_telegram_text_message(chat_id, "❌ Failed to save configuration")
        
        elif action == "remove":
            if len(parts) < 3:
                send_telegram_text_message(chat_id, "❌ Usage: /channels remove <index>")
                return config
            try:
                index = int(parts[2].strip()) - 1
                if index < 0 or index >= len(config.chat_ids):
                    send_telegram_text_message(chat_id, f"❌ Invalid index. Use 1-{len(config.chat_ids)}")
                    return config
                removed = config.chat_ids.pop(index)
                if save_config(config):
                    send_telegram_text_message(chat_id, f"✅ Removed: {removed}\nRemaining: {len(config.chat_ids)}")
                else:
                    send_telegram_text_message(chat_id, "❌ Failed to save configuration")
            except ValueError:
                send_telegram_text_message(chat_id, "❌ Invalid index format")
        
        else:
            send_telegram_text_message(chat_id, "❌ Invalid action. Use: add, list, remove")
    
    # Reset posted auctions history
    elif text.startswith("/reset"):
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized user {user_id} attempted to reset history")
            send_telegram_text_message(chat_id, "❌ You are not authorized to use this command.")
            return config
        
        database.clear_posted_auctions()
        send_telegram_text_message(chat_id, "✅ Posted auctions history has been cleared. The bot will re-post all auctions it finds.")
        logger.info(f"Posted auctions history reset by user {user_id}")
    
    # Quick analytics stats
    elif text.startswith("/stats"):
        analytics = load_analytics()
        total = analytics.get('total_posts', 0)
        success = analytics.get('successful_posts', 0)
        failed = analytics.get('failed_posts', 0)
        rate = analytics.get('success_rate', 0)
        
        current_hour = datetime.now().strftime("%Y-%m-%d %H:00")
        hour_count = get_daily_post_count(current_hour, 'all')
        
        # Get category breakdown
        cat_info = ""
        posts_by_cat = analytics.get('posts_by_category', {})
        if posts_by_cat:
            cat_lines = [f"  • {cat.split('/')[-1]}: {count}" for cat, count in posts_by_cat.items()]
            cat_info = "\n".join(cat_lines[:10])  # Max 10 categories
        
        send_telegram_text_message(chat_id,
            f"📈 <b>Analytics Summary</b>\n\n"
            f"📊 Total Posts: {total}\n"
            f"✅ Successful: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Success Rate: {rate:.1f}%\n"
            f"📅 Current Hour: {hour_count} posts\n\n"
            f"📂 <b>By Category:</b>\n{cat_info if cat_info else '  No data yet'}"
        )
    
    return config

def handle_callback_query(callback_query: Dict[str, Any], config: BotConfig) -> None:
    """Handle incoming Telegram inline button callback queries."""
    global trigger_scrape_now
    
    query_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data", "")
    user_id = callback_query.get("from", {}).get("id")
    
    # Check authorization (same as command checks)
    if not is_authorized(user_id):
        answer_telegram_callback_query(query_id, "❌ Unauthorized")
        send_telegram_text_message(chat_id, "❌ You are not authorized to use these buttons.")
        return
        
    logger.info(f"Received callback query '{data}' from user {user_id}")
    
    if data == "menu_status":
        answer_telegram_callback_query(query_id, "Fetching Status...")
        posted_count = len(load_posted_auctions())
        urls_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(config.target_urls)])
        channels_text = "\n".join([f"{i+1}. {cid}" for i, cid in enumerate(config.chat_ids)])
        
        filter_info = "None"
        if config.min_price or config.max_price or config.auction_types or config.include_keywords or config.exclude_keywords:
            filter_parts = []
            if config.min_price or config.max_price:
                filter_parts.append(f"Price: {config.min_price or '0'}-{config.max_price or '∞'}")
            if config.auction_types:
                filter_parts.append(f"Types: {', '.join(config.auction_types)}")
            if config.include_keywords:
                filter_parts.append(f"Include: {', '.join(config.include_keywords)}")
            if config.exclude_keywords:
                filter_parts.append(f"Exclude: {', '.join(config.exclude_keywords)}")
            filter_info = "\n".join(filter_parts)
            
        send_telegram_text_message(chat_id,
            f"📊 <b>Bot Configuration:</b>\n\n"
            f"🔗 Target URLs ({len(config.target_urls)}):\n{urls_text}\n\n"
            f"📢 Channels ({len(config.chat_ids)}):\n{channels_text}\n\n"
            f"⏱️ Check Interval: {config.check_interval} seconds\n"
            f"📊 Hourly Limit: {config.hourly_post_limit} posts/cat\n"
            f"📋 Filters:\n{filter_info}"
        )
        
    elif data == "menu_stats":
        answer_telegram_callback_query(query_id, "Fetching Stats...")
        analytics = load_analytics()
        total = analytics.get('total_posts', 0)
        success = analytics.get('successful_posts', 0)
        failed = analytics.get('failed_posts', 0)
        rate = analytics.get('success_rate', 0)
        
        current_hour = datetime.now().strftime("%Y-%m-%d %H:00")
        hour_count = get_daily_post_count(current_hour, 'all')
        
        send_telegram_text_message(chat_id,
            f"📈 <b>Analytics Summary:</b>\n\n"
            f"📊 Total Posts: {total}\n"
            f"✅ Successful: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Success Rate: {rate:.1f}%\n"
            f"📅 Current Hour: {hour_count} posts"
        )
        
    elif data == "menu_scrape":
        answer_telegram_callback_query(query_id, "⚡ Triggering Scrape!")
        trigger_scrape_now = True
        send_telegram_text_message(chat_id, "⚡ <b>Scraper Triggered!</b> Checking target URLs now...")
        
    elif data == "menu_uptime":
        answer_telegram_callback_query(query_id, "Checking Uptime...")
        try:
            import web_app
            if web_app.bot_start_time:
                uptime_sec = int(time.time() - web_app.bot_start_time)
                h = uptime_sec // 3600
                m = (uptime_sec % 3600) // 60
                s = uptime_sec % 60
                uptime_str = f"{h}h {m}m {s}s"
            else:
                uptime_str = "Not started / stopped"
        except:
            uptime_str = "Unknown"
            
        send_telegram_text_message(chat_id, f"⏱️ <b>Bot Uptime:</b> {uptime_str}")
        
    else:
        answer_telegram_callback_query(query_id, "Unknown Action")

def format_template(template: str, **kwargs) -> str:
    """Safely format template by replacing {key} placeholders."""
    result = template
    for key, val in kwargs.items():
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, str(val))
    return result

def check_closing_soon_alerts(config: BotConfig):
    """Check for auctions closing soon and send alerts to Telegram."""
    from datetime import timedelta
    # Ethiopia is EAT (UTC+3)
    eat_now = datetime.utcnow() + timedelta(hours=3)
    now_str = eat_now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Check for auctions ending in the next 60 minutes
    closing_auctions = database.get_auctions_needing_closing_alert(now_str, alert_threshold_minutes=60)
    
    for item in closing_auctions:
        lot_no = item['lot_no']
        name = item['name']
        end_date_str = item['end_date']
        minutes_left = item['minutes_left']
        category_url = item['category']
        
        alert_text = (
            f"⏰ <b>Closing Soon Alert!</b>\n\n"
            f"📦 <b>Asset:</b> {name}\n"
            f"🆔 <b>Lot No:</b> {lot_no}\n"
            f"⏳ <b>Closes in:</b> {minutes_left} minutes (at {end_date_str})\n\n"
            f"🔗 <a href='{category_url or DEFAULT_TARGET_URL}'>View Category</a>"
        )
        
        # Post to all configured channels
        success = False
        for chat_id in config.chat_ids:
            if send_telegram_text_message(chat_id, alert_text):
                success = True
                
        if success:
            database.mark_closing_alert_sent(lot_no)
            logger.info(f"Sent closing soon alert for Lot {lot_no} (ends in {minutes_left}m)")

def send_telegram_message(
    name: str,
    lot_no: str,
    initial_price: str,
    end_date_time: str,
    auction_type: str,
    image_url: Optional[str],
    target_url: str,
    chat_ids: List[str],
    config: Optional[BotConfig] = None
) -> bool:
    """Formulate the HTML message and push it to multiple Telegram channels."""
    if not config:
        config = load_config()
        
    caption = format_template(
        config.post_template,
        name=name,
        lot_no=lot_no,
        initial_price=initial_price,
        end_date_time=end_date_time,
        auction_type=auction_type,
        target_url=target_url
    )

    # Add delay to avoid rate limiting (reduced from 1.5s to 0.5s)
    time.sleep(0.5)

    # Use the sendPhoto endpoint if we have an image URL
    if image_url and not image_url.endswith("placeholder.png"):
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        base_payload = {
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML"
        }
    else:
        # Fallback to pure text message if no image is available
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        base_payload = {
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

    proxies = PROXIES if USE_PROXY else None
    success_count = 0
    failed_channels = []
    
    # Post to all configured channels
    for chat_id in chat_ids:
        payload = base_payload.copy()
        payload["chat_id"] = chat_id
        response = retry_request(requests.post, url, json=payload, proxies=proxies, timeout=15)
        
        if response and response.status_code == 200:
            logger.info(f"Successfully posted Lot: {lot_no} to {chat_id}")
            success_count += 1
        else:
            error_msg = response.text if response else 'No response'
            status_code = response.status_code if response else 'N/A'
            logger.error(f"Telegram API Error for {chat_id}: Status {status_code}, Response: {error_msg}")
            logger.error(f"Payload: {payload}")
            failed_channels.append((chat_id, status_code, error_msg))
    
    # Log summary for debugging
    if failed_channels:
        logger.warning(f"Failed to post to {len(failed_channels)}/{len(chat_ids)} channels for Lot {lot_no}")
        for chat_id, status, error in failed_channels:
            logger.warning(f"  - {chat_id}: {status} - {error[:100]}")
    
    return success_count > 0  # Return True if at least one channel succeeded

def normalize_image_url(image_url: Optional[str], base_url: str) -> Optional[str]:
    """Normalize image URL to absolute path. Rejects data URLs."""
    if not image_url:
        return None
    # Reject data URLs (Telegram doesn't accept them)
    if image_url.startswith("data:"):
        return None
    if image_url.startswith("http"):
        return image_url
    if image_url.startswith("/"):
        parsed_base = urlparse(base_url)
        return f"{parsed_base.scheme}://{parsed_base.netloc}{image_url}"
    return image_url

async def scrape_page_cards(page: Page) -> list:
    """Extract auction card data from the current page."""
    try:
        cards_data = await page.evaluate('''() => {
            const results = [];
            const detailsElements = Array.from(document.querySelectorAll('a, button, span')).filter(el => {
                return el.textContent.trim().toLowerCase() === 'details';
            });
            
            for (const detailsEl of detailsElements) {
                let parent = detailsEl.parentElement;
                let foundCard = false;
                
                for (let i = 0; i < 10 && parent; i++) {
                    if (parent.textContent.includes('Lot No.')) {
                        foundCard = true;
                        break;
                    }
                    parent = parent.parentElement;
                }
                
                if (foundCard && parent) {
                    const cardText = parent.innerText || parent.textContent;
                    const titleEl = parent.querySelector('h1, h2, h3, h4, h5, strong, .title, [class*="title"], [class*="name"]');
                    const title = titleEl ? titleEl.textContent.trim() : 'Unknown Asset';
                    const imgEl = parent.querySelector('img');
                    let imgUrl = null;
                    
                    if (imgEl) {
                        // Try multiple attributes for image URL
                        imgUrl = imgEl.getAttribute('src') || 
                                  imgEl.getAttribute('data-src') || 
                                  imgEl.getAttribute('data-original') ||
                                  imgEl.getAttribute('srcset');
                        
                        // If srcset, get the first URL
                        if (imgUrl && imgUrl.includes(',')) {
                            imgUrl = imgUrl.split(',')[0].trim().split(' ')[0];
                        }
                        
                        // Skip data URLs
                        if (imgUrl && imgUrl.startsWith('data:')) {
                            imgUrl = null;
                        }
                    }
                    
                    results.push({
                        text: cardText,
                        title: title,
                        imgUrl: imgUrl
                    });
                }
            }
            return results;
        }''')
        return cards_data
    except Exception as e:
        logger.error(f"Failed to evaluate page content: {e}")
        return []

async def get_next_page_url(page: Page) -> Optional[str]:
    """Find and return the next page URL if pagination exists."""
    try:
        next_url = await page.evaluate('''() => {
            // Look for common pagination patterns
            const nextLinks = Array.from(document.querySelectorAll(
                'a[rel="next"], a.next, a.pagination-next, ' +
                'li.next a, .pagination a, nav a'
            ));
            
            // Also look for links/buttons with "Next" or "›" or "»" text
            const allLinks = Array.from(document.querySelectorAll('a'));
            const textNextLinks = allLinks.filter(a => {
                const text = a.textContent.trim().toLowerCase();
                return text === 'next' || text === '›' || text === '»' || 
                       text === 'next page' || text === '>' || text === '>>';
            });
            
            const candidates = [...nextLinks, ...textNextLinks];
            for (const link of candidates) {
                const href = link.getAttribute('href');
                if (href && href !== '#' && !href.startsWith('javascript:')) {
                    // Return absolute URL
                    return link.href;
                }
            }
            return null;
        }''')
        return next_url
    except Exception:
        return None

async def scrape_auctions(
    browser: Browser,
    page: Page,
    target_url: str,
    posted_auctions: Set[str],
    daily_posts: Dict[str, Dict[str, int]],
    config: BotConfig,
    scrape_cache: Dict[str, Dict[str, Any]],
    analytics: Dict[str, Any]
) -> int:
    """Scrape auctions from the target URL and post new ones to Telegram."""
    # Scrape current page + follow pagination (up to MAX_PAGES)
    MAX_PAGES = 5
    all_cards_data = []
    current_url = target_url
    
    for page_num in range(1, MAX_PAGES + 1):
        try:
            response = await page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
            status = response.status if response else "unknown"
            title = await page.title()
            logger.info(f"Loaded page {page_num}: {current_url} | Status: {status} | Title: {title}")
        except Exception as e:
            logger.error(f"Failed to load page {page_num}: {e}")
            break
        
        await page.wait_for_timeout(2000)
        
        cards_data = await scrape_page_cards(page)
        if not cards_data:
            break
        
        all_cards_data.extend(cards_data)
        logger.info(f"Page {page_num}: found {len(cards_data)} listings")
        
        # Try to find next page
        if page_num < MAX_PAGES:
            next_url = await get_next_page_url(page)
            if next_url and next_url != current_url:
                current_url = next_url
            else:
                break  # No more pages
    
    logger.info(f"Found {len(all_cards_data)} total listings across {page_num} page(s). Processing updates...")
    
    new_posts_count = 0
    skip_keywords = {"new", "featured", "active", "open", "sealed bid", "live", "hot", "watchlist", "details", "bid now"}
    
    for card in all_cards_data:
        card_text = card['text']
        name = card['title']
        image_url = card['imgUrl']
        
        lot_match = re.search(r"Lot No[.:\s-]*([A-Za-z0-9-]+)", card_text, re.IGNORECASE)
        lot_no = lot_match.group(1).strip() if lot_match else None
        
        if name == 'Unknown Asset':
            lines = [line.strip() for line in card_text.split('\n') if line.strip()]
            for line in lines:
                if line.lower() not in skip_keywords and len(line) > 3:
                    name = line
                    break
        
        if not lot_no:
            lot_no = name
        
        if not lot_no or lot_no == 'Unknown Asset':
            continue
        
        if lot_no in posted_auctions:
            continue
        
        # Check scrape cache to avoid re-processing
        if target_url in scrape_cache and lot_no in scrape_cache[target_url]:
            continue
        
        # Check hourly post limit for this category
        current_hour = datetime.now().strftime("%Y-%m-%d %H:00")
        current_count = get_daily_post_count(current_hour, target_url)
        if current_count >= config.hourly_post_limit:
            logger.warning(f"Hourly post limit reached for {target_url}. Skipping remaining posts from this category.")
            break
        
        image_url = normalize_image_url(image_url, target_url)
                
        initial_price_match = re.search(r"Initial Price[.:\s]*([\d,]+\.?\d*\s*(?:ETB|Br)?)", card_text, re.IGNORECASE)
        initial_price = initial_price_match.group(1).strip() if initial_price_match else "N/A"
        
        end_date_match = re.search(r"End Date[.:\s]*(.*?)(?=\n|$|GC|watchlist)", card_text, re.IGNORECASE)
        end_date_time = end_date_match.group(1).strip() if end_date_match else "N/A"
        
        auction_type = "Sealed Bid" if "SEALED BID" in card_text.upper() else "Open Bidding"
        
        # Check filters
        if not passes_filters(name, initial_price, auction_type, config):
            logger.info(f"Auction {lot_no} filtered out by filters")
            continue
        
        # Check schedule
        if not is_within_schedule(config):
            logger.info(f"Outside business hours, skipping posting")
            break
        
        success = send_telegram_message(
            name=name,
            lot_no=lot_no,
            initial_price=initial_price,
            end_date_time=end_date_time,
            auction_type=auction_type,
            image_url=image_url,
            target_url=target_url,
            chat_ids=config.chat_ids,
            config=config
        )
        
        if success:
            posted_auctions.add(lot_no)
            increment_daily_post(current_hour, target_url)
            # Save to database with detailed metadata including end_date
            database.add_posted_auction(
                lot_no=lot_no,
                category=target_url,
                name=name,
                price=initial_price,
                end_date=end_date_time
            )
            # Add to scrape cache
            if target_url not in scrape_cache:
                scrape_cache[target_url] = {}
            scrape_cache[target_url][lot_no] = {"name": name, "price": initial_price}
            # Record analytics
            record_post_attempt(True, target_url)
            new_posts_count += 1
            await asyncio.sleep(1.0)
        else:
            # Record failed attempt
            record_post_attempt(False, target_url)
    
    # Batch save I/O operations only if there were new posts
    if new_posts_count > 0:
        save_posted_auctions(list(posted_auctions))
        save_scrape_cache(scrape_cache)
        save_daily_posts(daily_posts)
        save_analytics(analytics)
    
    logger.info(f"Finished scraping. Total new items posted: {new_posts_count}")
    return new_posts_count

async def main() -> None:
    """Main bot loop with graceful shutdown handling."""
    global bot_running_flag, last_heartbeat, trigger_scrape_now
    bot_running_flag = True
    
    logger.info("="*50)
    logger.info("Auction Bot Starting...")
    logger.info("="*50)
    
    config = load_config()
    posted_auctions = set(load_posted_auctions())  # Convert list to set for efficient lookup
    daily_posts = load_daily_posts()
    scrape_cache = load_scrape_cache()
    analytics = load_analytics()
    update_offset = 0
    
    logger.info(f"Target URLs ({len(config.target_urls)}):")
    for i, url in enumerate(config.target_urls, 1):
        logger.info(f"  {i}. {url}")
    logger.info(f"Check interval: {config.check_interval} seconds")
    logger.info(f"Hourly post limit: {config.hourly_post_limit} per category")
    logger.info(f"Posted auctions in history: {len(posted_auctions)}")
    logger.info("Send /help to your bot for available commands.")
    
    browser: Optional[Browser] = None
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            logger.info("Browser launched successfully")
            
            while bot_running_flag:
                # Check for Telegram commands & callback queries
                updates = get_telegram_updates(update_offset)
                for update in updates:
                    update_offset = update["update_id"] + 1
                    if "message" in update and "text" in update["message"]:
                        message = update["message"]
                        chat_type = message.get("chat", {}).get("type", "unknown")
                        msg_text = message.get("text", "")
                        logger.info(f"📨 Update: chat_type={chat_type}, text={msg_text!r}")
                        if message["text"].startswith("/"):
                            config = handle_command(message, config)
                    elif "callback_query" in update:
                        logger.info(f"🔘 Callback query: data={update['callback_query'].get('data')!r}")
                        handle_callback_query(update["callback_query"], config)
                
                # Check for auctions closing soon
                try:
                    check_closing_soon_alerts(config)
                except Exception as e:
                    logger.error(f"Error checking closing soon alerts: {e}")
                
                # Scrape auctions from all URLs concurrently
                if config.target_urls:
                    # Create pages for each URL
                    pages = []
                    for _ in config.target_urls:
                        try:
                            # Use a realistic User-Agent to prevent basic headless detection
                            page = await browser.new_page(
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                            )
                            pages.append(page)
                        except Exception as e:
                            logger.error(f"Failed to create page: {e}")
                    
                    if pages:
                        # Scrape all URLs concurrently
                        scrape_tasks = []
                        for target_url, page in zip(config.target_urls, pages):
                            scrape_tasks.append(scrape_auctions(browser, page, target_url, posted_auctions, daily_posts, config, scrape_cache, analytics))
                        
                        logger.info(f"Scraping {len(config.target_urls)} URLs concurrently...")
                        await asyncio.gather(*scrape_tasks, return_exceptions=True)
                        
                        # Close all pages
                        for page in pages:
                            try:
                                await page.close()
                            except Exception as e:
                                logger.error(f"Error closing page: {e}")
                
                # Emit stats update to WebSocket clients immediately
                try:
                    import web_app
                    posted_count = database.get_total_posted_count()
                    now = time.time()
                    bot_uptime = int(now - web_app.bot_start_time) if web_app.bot_start_time else 0
                    web_app.socketio.emit('stats_update', {
                        'posted_count': posted_count,
                        'status': 'running',
                        'bot_uptime_seconds': bot_uptime
                    })
                except Exception as e:
                    logger.error(f"Failed to emit stats update: {e}")
                
                # Update heartbeat after each scrape cycle
                last_heartbeat = datetime.now().isoformat()
                
                # Wait for next check (with interruption and manual trigger support)
                logger.info(f"Waiting {config.check_interval} seconds before next check...")
                for _ in range(config.check_interval):
                    if not bot_running_flag:
                        break
                    if trigger_scrape_now:
                        logger.info("Manual scrape requested, waking up...")
                        trigger_scrape_now = False
                        break
                    await asyncio.sleep(1)
    
    except asyncio.CancelledError:
        logger.info("Bot shutdown requested...")
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}", exc_info=True)
    finally:
        bot_running_flag = False
        last_heartbeat = None
        if browser:
            await browser.close()
            logger.info("Browser closed")
        logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    finally:
        # Clean up any remaining asyncio tasks
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.close()
        except:
            pass