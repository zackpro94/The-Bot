# 🤖 Auction Bot

A modern, intelligent Telegram bot that monitors auction websites and automatically posts new listings to your Telegram channel. Features a beautiful web dashboard for real-time control and monitoring. Built with Python, Playwright, Flask, and the Telegram Bot API.

## ✨ Features

- **🎨 Modern Web Dashboard**: Beautiful UI with real-time monitoring and control
- **📊 Live Statistics**: View posted auctions, check intervals, and bot status
- **🔄 Real-time Logs**: Watch bot activity in real-time via WebSocket
- **🎮 Web Controls**: Start/stop bot and change configuration from the web interface
- **🤖 Telegram Commands**: Control bot via Telegram messages
- **🔁 Automated Scraping**: Continuously monitors auction websites for new listings
- **🚫 Smart Duplicate Detection**: Never posts the same auction twice
- **⚙️ Dynamic Configuration**: Change target URLs via web or Telegram
- **🔀 Retry Logic**: Exponential backoff for failed API requests
- **📝 Comprehensive Logging**: File and console logging for debugging
- **🎯 Type Safety**: Full type hints for better code maintainability
- **🛡️ Graceful Shutdown**: Clean shutdown on Ctrl+C
- **🌐 Proxy Support**: Built-in proxy configuration for restricted networks
- **🔐 Admin Commands**: Restrict sensitive commands to authorized users

## 📋 Requirements

- Python 3.8+
- Playwright
- Requests
- Flask
- Flask-SocketIO
- Eventlet

## 🚀 Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd "The Bot"
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install Playwright browsers:
```bash
playwright install chromium
```

## 🎮 Running the Bot

### Option 1: Web Dashboard (Recommended)

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
python web_app.py
```

Then open your browser to: `http://localhost:5000`

The web dashboard provides:
- 🎨 Beautiful modern UI
- 📊 Real-time statistics
- 🎮 Start/Stop controls
- ⚙️ Configuration management
- 📋 Live log viewer
- 🔄 Real-time updates via WebSocket

### Option 2: Command Line Only

```bash
python Bot.py
```

This runs the bot without the web interface. Use Telegram commands to control it.

## ⚙️ Configuration

Edit the configuration section at the top of `Bot.py`:

```python
BOT_TOKEN = "your_telegram_bot_token"      # Get from @BotFather
CHAT_ID = "your_channel_or_group_id"      # Target channel/group ID
ADMIN_CHAT_ID = "your_telegram_user_id"    # Optional: Restrict admin commands
DEFAULT_TARGET_URL = "https://auction.et/category/auctions/38"
CHECK_INTERVAL = 300                       # Seconds between checks
```

### Getting Your Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the instructions
3. Copy the token provided

### Getting Your Chat ID

1. For a channel: Add your bot to the channel as an administrator
2. For a private group: Add your bot to the group
3. Send a message to the bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
4. Find the `chat.id` in the response

### Getting Your User ID (for Admin)

1. Send a message to [@userinfobot](https://t.me/userinfobot)
2. It will reply with your user ID

## 🎮 Usage

### Starting the Bot

```bash
python Bot.py
```

The bot will:
1. Load configuration from `bot_config.json` (or use defaults)
2. Load posted auction history from `posted_auctions.json`
3. Start monitoring the target URL
4. Check for new listings at the configured interval

### Telegram Commands

Send these commands to your bot in Telegram:

- `/start` - Initialize the bot and see available commands
- `/help` - Show help message with all commands
- `/status` - View current configuration and statistics
- `/seturl <url>` - Change the target auction URL (admin only)

**Example:**
```
/seturl https://example.com/catagory/3
```

## 📁 File Structure

```
The Bot/
├── Bot.py                  # Main bot script
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── bot_config.json        # Dynamic configuration (auto-created)
├── posted_auctions.json   # History of posted auctions (auto-created)
└── bot.log                # Log file (auto-created)
```

## 🔧 Advanced Configuration

### Proxy Settings

If you need to use a proxy (e.g., for network restrictions):

```python
USE_PROXY = True
PROXIES = {
    "http": "http://127.0.0.1:10808",
    "https": "http://127.0.0.1:10808"
}
```

### Retry Configuration

Adjust retry behavior for API calls:

```python
MAX_RETRIES = 3      # Maximum retry attempts
RETRY_DELAY = 5      # Base delay in seconds (exponential backoff)
```

### Check Interval

Set how often the bot checks for new auctions:

```python
CHECK_INTERVAL = 300  # 300 seconds = 5 minutes
```

**Note:** Minimum interval is 60 seconds.

## 📊 Logging

The bot creates `bot.log` with detailed logs including:
- Timestamped events
- API request status
- Scraping results
- Error messages with stack traces

Log levels:
- `INFO`: Normal operations
- `WARNING`: Retry attempts, invalid configurations
- `ERROR`: Failed requests, scraping errors

## 🛡️ Security

- **Admin Protection**: Set `ADMIN_CHAT_ID` to restrict `/seturl` command
- **Token Security**: Never commit your `BOT_TOKEN` to version control
- **Proxy Support**: Use proxies to hide your IP if needed

## 🐛 Troubleshooting

### Bot doesn't post to Telegram

1. Check `bot.log` for error messages
2. Verify your bot token is correct
3. Ensure the bot is an administrator in the channel
4. Check if proxy settings are needed

### Scraping finds 0 listings

1. Verify the target URL is accessible in a browser
2. Check if the website structure has changed
3. Review logs for JavaScript evaluation errors
4. Try increasing the wait timeout in `scrape_auctions()`

### Connection timeout errors

1. Enable proxy if behind a firewall
2. Check your internet connection
3. Verify Telegram API is accessible
4. Increase timeout values in configuration

### Config file errors

1. Delete `bot_config.json` to reset to defaults
2. Check JSON syntax if manually editing
3. Ensure URL format is valid (starts with http:// or https://)

## 🔄 Updates

The bot automatically:
- Reloads configuration when changed via `/seturl`
- Saves posted auction history after each run
- Persists dynamic settings to `bot_config.json`

## 📝 Development

### Adding New Commands

Edit the `handle_command()` function in `Bot.py`:

```python
elif text.startswith("/mycommand"):
    # Your command logic here
    send_telegram_text_message(chat_id, "Response")
```

### Modifying Scraping Logic

Edit the JavaScript in `scrape_auctions()` to adapt to website changes.

### Adding New Auction Sites

1. Use `/seturl` to change the target URL
2. Modify the scraping logic if the site structure differs
3. Test with the new URL before deploying

## 📄 License

This project is provided as-is for educational and personal use.

## 🤝 Contributing

Feel free to submit issues and enhancement requests!

## 📞 Support

For issues or questions:
1. Check the troubleshooting section
2. Review `bot.log` for detailed error information
3. Verify your configuration settings

---

**Built with ❤️ using Python, Playwright, and Telegram Bot API**
