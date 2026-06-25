import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

import os
DATA_DIR = os.getenv("DATA_DIR", "")
DB_FILE = os.path.join(DATA_DIR, "bot_database.db") if DATA_DIR else "bot_database.db"


@contextmanager
def get_db_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()

def init_database():
    """Initialize the database with required tables."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Posted auctions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posted_auctions (
                lot_no TEXT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                name TEXT,
                price TEXT
            )
        """)
        
        # Daily posts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                category TEXT,
                count INTEGER DEFAULT 0,
                UNIQUE(date, category)
            )
        """)
        
        # Analytics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                total_posts INTEGER DEFAULT 0,
                successful_posts INTEGER DEFAULT 0,
                failed_posts INTEGER DEFAULT 0,
                posts_by_category TEXT
            )
        """)
        
        # Scrape cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrape_cache (
                lot_no TEXT PRIMARY KEY,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data TEXT
            )
        """)
        
        # Performance indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_posted_auctions_posted_at ON posted_auctions(posted_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_posts_date ON daily_posts(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics(date)")
        
        logger.info("Database initialized successfully")

def load_posted_auctions() -> List[str]:
    """Load all posted auction lot numbers."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT lot_no FROM posted_auctions")
        return [row[0] for row in cursor.fetchall()]

def get_recent_auctions(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recently posted auctions with metadata."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lot_no, posted_at, category, name, price
            FROM posted_auctions
            ORDER BY posted_at DESC
            LIMIT ?
        """, (limit,))
        return [{
            'lot_no': row['lot_no'],
            'posted_at': row['posted_at'],
            'category': row['category'] or '',
            'name': row['name'] or 'Unknown',
            'price': row['price'] or 'N/A'
        } for row in cursor.fetchall()]

def get_total_posted_count() -> int:
    """Get total count of posted auctions (optimized, no full load)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM posted_auctions")
        return cursor.fetchone()[0]

def clear_posted_auctions():
    """Clear all posted auctions history."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM posted_auctions")
        logger.info("Posted auctions history cleared")

def save_posted_auctions(lot_numbers: List[str], category: str = "", name: str = "", price: str = ""):
    """Save posted auctions to database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for lot_no in lot_numbers:
            cursor.execute("""
                INSERT OR IGNORE INTO posted_auctions (lot_no, category, name, price)
                VALUES (?, ?, ?, ?)
            """, (lot_no, category, name, price))

def add_posted_auction(lot_no: str, category: str = "", name: str = "", price: str = ""):
    """Add a single posted auction."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO posted_auctions (lot_no, category, name, price)
            VALUES (?, ?, ?, ?)
        """, (lot_no, category, name, price))

def load_daily_posts() -> Dict[str, Dict[str, int]]:
    """Load daily post counts."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT date, category, count FROM daily_posts")
        result = {}
        for row in cursor.fetchall():
            date = row[0]
            if date not in result:
                result[date] = {}
            result[date][row[1]] = row[2]
        return result

def save_daily_posts(daily_posts: Dict[str, Dict[str, int]]):
    """Save daily post counts."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for date, categories in daily_posts.items():
            for category, count in categories.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_posts (date, category, count)
                    VALUES (?, ?, ?)
                """, (date, category, count))

def get_daily_post_count(date: str, category: str) -> int:
    """Get post count for a specific date and category."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT count FROM daily_posts 
            WHERE date = ? AND category = ?
        """, (date, category))
        row = cursor.fetchone()
        return row[0] if row else 0

def increment_daily_post(date: str, category: str):
    """Increment daily post count for a category."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_posts (date, category, count)
            VALUES (?, ?, 1)
            ON CONFLICT(date, category) DO UPDATE SET count = count + 1
        """, (date, category))

def load_analytics() -> Dict[str, Any]:
    """Load analytics data."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM analytics ORDER BY date DESC LIMIT 30")
        rows = cursor.fetchall()
        
        analytics = {
            "total_posts": 0,
            "successful_posts": 0,
            "failed_posts": 0,
            "posts_by_category": {},
            "daily_stats": [],
            "posts_per_day": {},
            "success_rate": 0.0,
            "last_updated": None
        }
        
        for row in rows:
            analytics["total_posts"] += row["total_posts"]
            analytics["successful_posts"] += row["successful_posts"]
            analytics["failed_posts"] += row["failed_posts"]
            
            if row["posts_by_category"]:
                category_data = json.loads(row["posts_by_category"])
                for cat, count in category_data.items():
                    if cat not in analytics["posts_by_category"]:
                        analytics["posts_by_category"][cat] = 0
                    analytics["posts_by_category"][cat] += count
            
            analytics["daily_stats"].append({
                "date": row["date"],
                "total_posts": row["total_posts"],
                "successful_posts": row["successful_posts"],
                "failed_posts": row["failed_posts"],
                "posts_by_category": json.loads(row["posts_by_category"]) if row["posts_by_category"] else {}
            })
            
            # Also populate posts_per_day for backward compatibility
            analytics["posts_per_day"][row["date"]] = row["total_posts"]
        
        # Calculate success rate
        if analytics["total_posts"] > 0:
            analytics["success_rate"] = (analytics["successful_posts"] / analytics["total_posts"] * 100)
        
        # Set last updated to most recent date
        if rows:
            analytics["last_updated"] = rows[0]["date"]
        
        return analytics

def save_analytics(analytics: Dict[str, Any]):
    """Save analytics data."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d %H:00")
        
        cursor.execute("""
            INSERT OR REPLACE INTO analytics 
            (date, total_posts, successful_posts, failed_posts, posts_by_category)
            VALUES (?, ?, ?, ?, ?)
        """, (
            today,
            analytics.get("total_posts", 0),
            analytics.get("successful_posts", 0),
            analytics.get("failed_posts", 0),
            json.dumps(analytics.get("posts_by_category", {}))
        ))

def record_post_attempt(success: bool, category: str = "default"):
    """Record a post attempt in analytics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d %H:00")
        
        # Get current analytics for today
        cursor.execute("SELECT * FROM analytics WHERE date = ?", (today,))
        row = cursor.fetchone()
        
        if row:
            total = row["total_posts"] + 1
            successful = row["successful_posts"] + (1 if success else 0)
            failed = row["failed_posts"] + (0 if success else 1)
            posts_by_category = json.loads(row["posts_by_category"]) if row["posts_by_category"] else {}
        else:
            total = 1
            successful = 1 if success else 0
            failed = 0 if success else 1
            posts_by_category = {}
        
        # Update category count
        if category not in posts_by_category:
            posts_by_category[category] = 0
        posts_by_category[category] += 1
        
        cursor.execute("""
            INSERT OR REPLACE INTO analytics 
            (date, total_posts, successful_posts, failed_posts, posts_by_category)
            VALUES (?, ?, ?, ?, ?)
        """, (today, total, successful, failed, json.dumps(posts_by_category)))

def load_scrape_cache() -> Dict[str, Dict[str, Any]]:
    """Load scrape cache."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT lot_no, data FROM scrape_cache")
        return {row[0]: json.loads(row[1]) for row in cursor.fetchall()}

def save_scrape_cache(cache: Dict[str, Dict[str, Any]]):
    """Save scrape cache."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for lot_no, data in cache.items():
            cursor.execute("""
                INSERT OR REPLACE INTO scrape_cache (lot_no, data)
                VALUES (?, ?)
            """, (lot_no, json.dumps(data)))

def clear_scrape_cache():
    """Clear scrape cache."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scrape_cache")

def cleanup_old_data(days: int = 30):
    """Clean up data older than specified days."""
    from datetime import timedelta
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:00")
        
        # Clean up old analytics
        cursor.execute("DELETE FROM analytics WHERE date < ?", (cutoff_date,))
        
        # Clean up old daily posts
        cursor.execute("DELETE FROM daily_posts WHERE date < ?", (cutoff_date,))
        
        # Clean up old scrape cache (keep only 7 days)
        cursor.execute("DELETE FROM scrape_cache WHERE date(cached_at) < date('now', '-7 days')")
        
        logger.info(f"Cleaned up data older than {days} days (cutoff: {cutoff_date})")

def get_database_stats() -> Dict[str, int]:
    """Get database statistics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM posted_auctions")
        posted_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM daily_posts")
        daily_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM analytics")
        analytics_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scrape_cache")
        cache_count = cursor.fetchone()[0]
        
        return {
            "posted_auctions": posted_count,
            "daily_posts": daily_count,
            "analytics": analytics_count,
            "scrape_cache": cache_count
        }
