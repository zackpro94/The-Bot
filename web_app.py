from flask import Flask, render_template, jsonify, request, session, Response
from flask_socketio import SocketIO, emit
from functools import wraps
import threading
from collections import deque
import os
import json
import logging
import time
import psutil
from typing import Optional
from datetime import datetime
import Bot
from Bot import (
    load_config, save_config, BotConfig, validate_url,
    load_posted_auctions, load_daily_posts, load_analytics, logger as bot_logger
)
import database

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'auction-bot-secret-key-change-in-production')
socketio = SocketIO(app, async_mode='eventlet')

# Simple authentication
AUTH_USERNAME = os.getenv('AUTH_USERNAME')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD')

if not AUTH_USERNAME or not AUTH_PASSWORD:
    logging.getLogger(__name__).error(
        "❌ AUTH_USERNAME or AUTH_PASSWORD is not set in environment variables or .env file! Dashboard login will not be accessible."
    )
elif AUTH_USERNAME == 'admin' and AUTH_PASSWORD == 'admin123':
    logging.getLogger(__name__).warning(
        "⚠️  Using default admin credentials! Set AUTH_USERNAME and AUTH_PASSWORD environment variables for production."
    )


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated

# Global state
bot_thread: Optional[threading.Thread] = None
log_history: deque = deque(maxlen=500)  # Keep last 500 log entries for all clients
bot_start_time: Optional[float] = None  # Track when bot was started
server_start_time: float = time.time()  # Track server start

last_stats = {
    'posted_count': 0,
    'last_run_time': None,
    'target_url': '',
    'check_interval': 300,
    'status': 'stopped'
}

# Custom log handler to send logs to web interface
class WebLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname,
            'message': record.getMessage()
        }
        try:
            socketio.emit('log_update', log_entry)
        except:
            pass  # Socket might not be ready
        log_history.append(log_entry)

# Add web log handler to bot logger
web_handler = WebLogHandler()
web_handler.setLevel(logging.INFO)
bot_logger.addHandler(web_handler)

# Initialize database
try:
    database.init_database()
    bot_logger.info("Database initialized successfully")
except Exception as e:
    bot_logger.error(f"Failed to initialize database: {e}")

# ==================== PAGE ROUTES ====================

@app.route('/')
def index():
    """Main dashboard page."""
    try:
        config = load_config()
        posted_count = database.get_total_posted_count()
        return render_template('index.html', 
                             config=config,
                             posted_count=posted_count,
                             bot_running=Bot.bot_running_flag,
                             stats=last_stats,
                             authenticated=session.get('authenticated', False))
    except Exception as e:
        bot_logger.error(f"Error rendering index: {e}")
        return f"<h1>Server Error</h1><pre>{e}</pre>", 500

# ==================== AUTH ROUTES ====================

@app.route('/login', methods=['POST'])
def login():
    """Handle login request."""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        session['authenticated'] = True
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/logout', methods=['POST'])
def logout():
    """Handle logout request."""
    session.pop('authenticated', None)
    return jsonify({'success': True})

# ==================== API ROUTES ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint — no auth required."""
    now = time.time()
    uptime_seconds = int(now - server_start_time)
    bot_uptime = int(now - bot_start_time) if bot_start_time and Bot.bot_running_flag else 0
    
    # Memory usage
    process = psutil.Process(os.getpid())
    memory_mb = round(process.memory_info().rss / 1024 / 1024, 1)
    
    return jsonify({
        'status': 'healthy',
        'bot_status': 'running' if Bot.bot_running_flag else 'stopped',
        'server_uptime_seconds': uptime_seconds,
        'bot_uptime_seconds': bot_uptime,
        'memory_mb': memory_mb,
        'bot_thread_alive': bot_thread.is_alive() if bot_thread else False,
        'last_heartbeat': Bot.last_heartbeat,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/config', methods=['GET'])
@requires_auth
def get_config():
    """Get current bot configuration."""
    config = load_config()
    return jsonify({
        'target_urls': config.target_urls,
        'check_interval': config.check_interval,
        'hourly_post_limit': config.hourly_post_limit,
        'min_price': config.min_price,
        'max_price': config.max_price,
        'auction_types': config.auction_types,
        'include_keywords': config.include_keywords,
        'exclude_keywords': config.exclude_keywords,
        'business_hours_start': config.business_hours_start,
        'business_hours_end': config.business_hours_end,
        'business_days': config.business_days,
        'chat_ids': config.chat_ids
    })

@app.route('/api/config', methods=['POST'])
@requires_auth
def update_config():
    """Update bot configuration."""
    try:
        data = request.json
        target_urls = data.get('target_urls')
        check_interval = data.get('check_interval')
        hourly_post_limit = data.get('hourly_post_limit')
        min_price = data.get('min_price')
        max_price = data.get('max_price')
        auction_types = data.get('auction_types')
        include_keywords = data.get('include_keywords')
        exclude_keywords = data.get('exclude_keywords')
        business_hours_start = data.get('business_hours_start')
        business_hours_end = data.get('business_hours_end')
        business_days = data.get('business_days')
        chat_ids = data.get('chat_ids')
        
        if target_urls:
            if not isinstance(target_urls, list):
                return jsonify({'success': False, 'error': 'target_urls must be a list'}), 400
            for url in target_urls:
                if not validate_url(url):
                    return jsonify({'success': False, 'error': f'Invalid URL format: {url}'}), 400
        
        if check_interval:
            try:
                check_interval = int(check_interval)
                if check_interval < 60:
                    return jsonify({'success': False, 'error': 'Check interval must be at least 60 seconds'}), 400
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid check interval'}), 400
        
        if hourly_post_limit:
            try:
                hourly_post_limit = int(hourly_post_limit)
                if hourly_post_limit < 1:
                    return jsonify({'success': False, 'error': 'Hourly post limit must be at least 1'}), 400
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid hourly post limit'}), 400
        
        if min_price is not None:
            try:
                min_price = float(min_price)
                if min_price < 0:
                    return jsonify({'success': False, 'error': 'Min price must be non-negative'}), 400
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid min price'}), 400
        
        if max_price is not None:
            try:
                max_price = float(max_price)
                if max_price < 0:
                    return jsonify({'success': False, 'error': 'Max price must be non-negative'}), 400
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid max price'}), 400
        
        config = load_config()
        if target_urls is not None:
            config.target_urls = target_urls
        if check_interval is not None:
            config.check_interval = check_interval
        if hourly_post_limit is not None:
            config.hourly_post_limit = hourly_post_limit
        if min_price is not None:
            config.min_price = min_price
        if max_price is not None:
            config.max_price = max_price
        if auction_types is not None:
            config.auction_types = auction_types if isinstance(auction_types, list) else []
        if include_keywords is not None:
            config.include_keywords = include_keywords if isinstance(include_keywords, list) else []
        if exclude_keywords is not None:
            config.exclude_keywords = exclude_keywords if isinstance(exclude_keywords, list) else []
        if business_hours_start is not None:
            config.business_hours_start = business_hours_start
        if business_hours_end is not None:
            config.business_hours_end = business_hours_end
        if business_days is not None:
            config.business_days = business_days if isinstance(business_days, list) else []
        if chat_ids is not None:
            config.chat_ids = chat_ids if isinstance(chat_ids, list) else []
        
        if save_config(config):
            last_stats['target_url'] = config.target_urls[0] if config.target_urls else ''
            last_stats['check_interval'] = config.check_interval
            socketio.emit('config_updated', {
                'target_urls': config.target_urls,
                'check_interval': config.check_interval,
                'hourly_post_limit': config.hourly_post_limit,
                'min_price': config.min_price,
                'max_price': config.max_price,
                'auction_types': config.auction_types,
                'include_keywords': config.include_keywords,
                'exclude_keywords': config.exclude_keywords,
                'business_hours_start': config.business_hours_start,
                'business_hours_end': config.business_hours_end,
                'business_days': config.business_days,
                'chat_ids': config.chat_ids
            })
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500
    except Exception as e:
        bot_logger.error(f"Error updating config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
@requires_auth
def get_stats():
    """Get bot statistics."""
    posted_count = database.get_total_posted_count()
    config = load_config()
    analytics = load_analytics()
    now = time.time()
    bot_uptime = int(now - bot_start_time) if bot_start_time and Bot.bot_running_flag else 0
    return jsonify({
        'posted_count': posted_count,
        'target_urls': config.target_urls,
        'check_interval': config.check_interval,
        'status': 'running' if Bot.bot_running_flag else 'stopped',
        'last_run_time': last_stats.get('last_run_time'),
        'bot_uptime_seconds': bot_uptime,
        'analytics': analytics
    })

@app.route('/api/auctions/recent', methods=['GET'])
@requires_auth
def get_recent_auctions():
    """Get recently posted auctions."""
    limit = request.args.get('limit', 20, type=int)
    limit = min(limit, 100)  # Cap at 100
    auctions = database.get_recent_auctions(limit)
    return jsonify({'auctions': auctions})

@app.route('/api/db/stats', methods=['GET'])
@requires_auth
def get_db_stats():
    """Get database statistics."""
    stats = database.get_database_stats()
    return jsonify(stats)

@app.route('/api/logs/export', methods=['GET'])
@requires_auth
def export_logs():
    """Export recent logs as a downloadable text file."""
    logs = list(log_history)
    log_text = "\n".join(
        f"[{log['timestamp']}] [{log['level']}] {log['message']}"
        for log in logs
    )
    return Response(
        log_text,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename=bot_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'}
    )

# ==================== BOT CONTROL ====================

@app.route('/api/bot/start', methods=['POST'])
@requires_auth
def start_bot():
    """Start the bot."""
    global bot_thread, bot_start_time
    
    if Bot.bot_running_flag:
        return jsonify({'success': False, 'error': 'Bot is already running'}), 400
    
    try:
        bot_start_time = time.time()
        last_stats['status'] = 'running'
        socketio.emit('bot_status', {'status': 'running', 'start_time': bot_start_time})
        
        # Import and run bot in separate thread
        bot_thread = threading.Thread(target=lambda: run_bot_wrapper(), daemon=True)
        bot_thread.start()
        
        return jsonify({'success': True})
    except Exception as e:
        Bot.bot_running_flag = False
        bot_start_time = None
        last_stats['status'] = 'stopped'
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bot/stop', methods=['POST'])
@requires_auth
def stop_bot():
    """Stop the bot."""
    global bot_start_time
    if not Bot.bot_running_flag:
        return jsonify({'success': False, 'error': 'Bot is not running'}), 400
    
    Bot.bot_running_flag = False
    bot_start_time = None
    last_stats['status'] = 'stopped'
    socketio.emit('bot_status', {'status': 'stopped'})
    
    return jsonify({'success': True})

def run_bot_wrapper():
    """Wrapper to run bot and handle state updates."""
    global bot_start_time
    try:
        import asyncio
        from Bot import main as bot_main
        
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        loop.run_until_complete(bot_main())
    except Exception as e:
        bot_logger.error(f"Bot thread error: {e}")
    finally:
        Bot.bot_running_flag = False
        bot_start_time = None
        last_stats['status'] = 'stopped'
        socketio.emit('bot_status', {'status': 'stopped'})

# ==================== PERIODIC STATS BROADCAST ====================

def broadcast_stats():
    """Periodically broadcast stats to all connected clients."""
    while True:
        time.sleep(30)
        if Bot.bot_running_flag:
            try:
                posted_count = database.get_total_posted_count()
                now = time.time()
                bot_uptime = int(now - bot_start_time) if bot_start_time else 0
                socketio.emit('stats_update', {
                    'posted_count': posted_count,
                    'status': 'running',
                    'bot_uptime_seconds': bot_uptime
                })
            except Exception:
                pass  # Don't crash the broadcast thread

stats_thread = threading.Thread(target=broadcast_stats, daemon=True)
stats_thread.start()

# ==================== SOCKETIO EVENTS ====================

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    emit('connected', {'status': 'connected'})
    # Send current state
    now = time.time()
    bot_uptime = int(now - bot_start_time) if bot_start_time and Bot.bot_running_flag else 0
    emit('bot_status', {
        'status': 'running' if Bot.bot_running_flag else 'stopped',
        'bot_uptime_seconds': bot_uptime
    })
    
    # Send recent logs (non-destructive — all clients see the same history)
    emit('log_history', list(log_history)[-100:])

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    pass

if __name__ == '__main__':
    # Load initial stats
    config = load_config()
    last_stats['target_url'] = config.target_urls[0] if config.target_urls else ''
    last_stats['check_interval'] = config.check_interval
    last_stats['posted_count'] = database.get_total_posted_count()
    
    print("🚀 Web Interface starting on http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
