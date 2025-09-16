# database.py
import sqlite3
import time
from typing import List, Optional, Dict

DB_PATH = "bsp.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            bonus REAL DEFAULT 0,
            notify_enabled INTEGER DEFAULT 1,
            notify_interval INTEGER DEFAULT 15,
            last_reminder INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource TEXT,
            buy REAL,
            sell REAL,
            timestamp INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            resource TEXT,
            target_price REAL,
            direction TEXT,
            speed REAL,
            current_price REAL,
            alert_time TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            chat_id INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            text TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            notify_enabled INTEGER DEFAULT 1,
            notify_interval INTEGER DEFAULT 15,
            pinned_message_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

def ensure_user(user_id: int, username: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def get_user(user_id: int) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_bonus(user_id: int, bonus: float):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET bonus = ? WHERE id = ?", (bonus, user_id))
    conn.commit()
    conn.close()

def get_active_alerts() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM alerts WHERE status='active'")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_alert_by_id(alert_id: int) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_alert_status(alert_id: int, status: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE alerts SET status=? WHERE id=?", (status, alert_id))
    conn.commit()
    conn.close()

def update_alert_fields(alert_id: int, fields: dict):
    conn = get_connection()
    c = conn.cursor()
    keys = ', '.join([f"{k}=?" for k in fields.keys()])
    values = list(fields.values())
    values.append(alert_id)
    c.execute(f"UPDATE alerts SET {keys} WHERE id=?", values)
    conn.commit()
    conn.close()

def insert_alert_record(user_id: int, resource: str, target_price: float, direction: str,
                        speed: float, current_price: float, alert_time: str, chat_id: Optional[int] = None) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = time.time()
    c.execute("""
        INSERT INTO alerts (user_id, resource, target_price, direction, speed, current_price, alert_time, created_at, chat_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, resource, target_price, direction, speed, current_price, alert_time, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), chat_id))
    alert_id = c.lastrowid
    conn.commit()
    conn.close()
    return alert_id

def insert_market_record(resource: str, buy: float, sell: float, timestamp: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO market (resource, buy, sell, timestamp) VALUES (?, ?, ?, ?)", (resource, buy, sell, timestamp))
    conn.commit()
    conn.close()

def get_latest_market(resource: str) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM market WHERE resource=? ORDER BY timestamp DESC LIMIT 1", (resource,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_recent_market(resource: str, minutes: int = 15) -> List[Dict]:
    cutoff = int(time.time()) - minutes * 60
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM market WHERE resource=? AND timestamp>=? ORDER BY timestamp ASC", (resource, cutoff))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_global_latest_timestamp() -> Optional[int]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT MAX(timestamp) as ts FROM market")
    row = c.fetchone()
    conn.close()
    return row['ts'] if row and row['ts'] else None

def get_users_with_notifications_enabled() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE notify_enabled=1")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_user_last_reminder(user_id: int, ts: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET last_reminder=? WHERE id=?", (ts, user_id))
    conn.commit()
    conn.close()

def get_chats_with_notifications_enabled() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM chats WHERE notify_enabled=1")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_chat_last_reminder(chat_id: int, ts: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE chats SET last_reminder=? WHERE chat_id=?", (ts, chat_id))
    conn.commit()
    conn.close()

def get_bot_stats() -> Dict:
    conn = get_connection()
    c = conn.cursor()
    stats = {}
    c.execute("SELECT COUNT(*) as cnt FROM users")
    stats['users'] = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM market")
    stats['resources'] = c.fetchone()['cnt']
    for res in ['Камень', 'Дерево', 'Провизия']:
        c.execute("SELECT buy FROM market WHERE resource=? ORDER BY timestamp DESC LIMIT 1", (res,))
        row = c.fetchone()
        stats[res.lower()] = row['buy'] if row else None
    c.execute("SELECT MAX(buy) as maxw FROM market WHERE timestamp>=?", (int(time.time()) - 7*24*3600,))
    stats['max_week'] = c.fetchone()['maxw']
    conn.close()
    return stats

def get_bot_history(limit: int = 20) -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM history ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_user_push_settings(user_id: int) -> Dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT notify_enabled as enabled, notify_interval as interval FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"enabled": True, "interval": 15}

def update_user_push_settings(user_id: int, enabled: bool = None, interval: int = None):
    conn = get_connection()
    c = conn.cursor()
    if enabled is not None:
        c.execute("UPDATE users SET notify_enabled=? WHERE id=?", (1 if enabled else 0, user_id))
    if interval is not None:
        c.execute("UPDATE users SET notify_interval=? WHERE id=?", (interval, user_id))
    conn.commit()
    conn.close()

def upsert_chat_settings(chat_id: int, notify_enabled: bool, interval: int, pinned_message_id: int = None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO chats (chat_id, notify_enabled, notify_interval, pinned_message_id) VALUES (?, ?, ?, ?) "
              "ON CONFLICT(chat_id) DO UPDATE SET notify_enabled=excluded.notify_enabled, notify_interval=excluded.notify_interval, pinned_message_id=excluded.pinned_message_id",
              (chat_id, 1 if notify_enabled else 0, interval, pinned_message_id))
    conn.commit()
    conn.close()

def get_chat_settings(chat_id: int) -> Dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {"notify_enabled": True, "notify_interval": 15, "pinned_message_id": None}

init_db()
