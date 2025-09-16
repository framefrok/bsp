# database.py
import sqlite3
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

DB_FILE = "marketbot.db"
_db: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_FILE, check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db


def init_db():
    db = get_db()
    c = db.cursor()

    # Пользователи — настройки (якорь, уровень торговли) + уведомления
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            has_anchor INTEGER DEFAULT 0,
            trade_level INTEGER DEFAULT 0,
            notify_personal INTEGER DEFAULT 1,
            notify_interval INTEGER DEFAULT 15,
            last_reminder INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    # Группы/чаты — настройки уведомлений для чата
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            notify_chat INTEGER DEFAULT 1,
            notify_interval INTEGER DEFAULT 15,
            last_reminder INTEGER DEFAULT 0,
            pinned_message_id INTEGER
        )
    """)

    # Рынок — сырые данные (без бонусов)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource TEXT NOT NULL,
            buy REAL NOT NULL,
            sell REAL NOT NULL,
            quantity INTEGER DEFAULT 0,
            timestamp INTEGER NOT NULL,
            date TEXT,
            forwarded_by INTEGER
        )
    """)

    # Алерты/таймеры
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resource TEXT NOT NULL,
            target_price REAL NOT NULL,
            direction TEXT NOT NULL,
            speed REAL,
            current_price REAL,
            alert_time TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'active',
            chat_id INTEGER,
            message_id INTEGER,
            last_checked TEXT
        )
    """)

    # Индексы
    c.execute("CREATE INDEX IF NOT EXISTS idx_market_resource_ts ON market(resource, timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)")

    db.commit()


# -------------------------
# Market helpers
# -------------------------
def insert_market_record(resource: str, buy: float, sell: float, quantity: int, timestamp: int, date_iso: str, forwarded_by: Optional[int]) -> int:
    db = get_db()
    c = db.cursor()
    c.execute("""
        INSERT INTO market (resource, buy, sell, quantity, timestamp, date, forwarded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (resource, float(buy), float(sell), int(quantity), int(timestamp), date_iso, forwarded_by))
    db.commit()
    return c.lastrowid


def get_latest_market(resource: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM market WHERE resource = ? ORDER BY timestamp DESC LIMIT 1", (resource,))
    row = c.fetchone()
    return dict(row) if row else None


def get_recent_market(resource: str, minutes: int = 15) -> List[Dict[str, Any]]:
    cutoff = int(time.time()) - minutes * 60
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM market WHERE resource = ? AND timestamp >= ? ORDER BY timestamp ASC", (resource, cutoff))
    rows = c.fetchall()
    return [dict(r) for r in rows]


def get_market_history(resource: str, limit: int = 50) -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM market WHERE resource = ? ORDER BY timestamp DESC LIMIT ?", (resource, limit))
    rows = c.fetchall()
    return [dict(r) for r in rows]


def get_global_latest_timestamp() -> Optional[int]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT MAX(timestamp) as ts FROM market")
    r = c.fetchone()
    return int(r["ts"]) if r and r["ts"] is not None else None


def get_week_max_qty(resource: str) -> int:
    cutoff = int((datetime.now() - timedelta(days=7)).timestamp())
    db = get_db()
    c = db.cursor()
    c.execute("SELECT MAX(quantity) as mq FROM market WHERE resource = ? AND timestamp >= ?", (resource, cutoff))
    r = c.fetchone()
    return int(r["mq"]) if r and r["mq"] is not None else 0


def get_all_latest() -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT DISTINCT resource FROM market")
    resources = [r["resource"] for r in c.fetchall()]
    result = []
    for res in resources:
        m = get_latest_market(res)
        if m:
            result.append(m)
    return result


def get_total_market_records() -> int:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM market")
    return int(c.fetchone()["cnt"])


def get_unique_resources_count() -> int:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(DISTINCT resource) as cnt FROM market")
    return int(c.fetchone()["cnt"])


# -------------------------
# Users helpers
# -------------------------
def upsert_user_settings(user_id: int, has_anchor: bool, trade_level: int):
    db = get_db()
    c = db.cursor()
    # Создаём запись, если её нет
    c.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (int(user_id),))
    c.execute("UPDATE users SET has_anchor = ?, trade_level = ?, updated_at = ? WHERE id = ?",
              (1 if has_anchor else 0, int(trade_level), int(time.time()), int(user_id)))
    db.commit()


def get_user_settings_db(user_id: int) -> Dict[str, Any]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT has_anchor, trade_level, notify_personal, notify_interval, last_reminder FROM users WHERE id = ?", (int(user_id),))
    row = c.fetchone()
    if not row:
        return {"has_anchor": 0, "trade_level": 0, "notify_personal": 1, "notify_interval": 15, "last_reminder": 0}
    return {"has_anchor": int(row["has_anchor"]), "trade_level": int(row["trade_level"]),
            "notify_personal": int(row["notify_personal"]), "notify_interval": int(row["notify_interval"]),
            "last_reminder": int(row["last_reminder"])}


def set_user_notify(user_id: int, enabled: bool, interval: int):
    db = get_db()
    c = db.cursor()
    c.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (int(user_id),))
    c.execute("UPDATE users SET notify_personal = ?, notify_interval = ?, updated_at = ? WHERE id = ?",
              (1 if enabled else 0, int(interval), int(time.time()), int(user_id)))
    db.commit()


def set_user_last_reminder(user_id: int, ts: int):
    db = get_db()
    c = db.cursor()
    c.execute("UPDATE users SET last_reminder = ? WHERE id = ?", (int(ts), int(user_id)))
    db.commit()


def get_users_with_notifications_enabled() -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, notify_interval, last_reminder FROM users WHERE notify_personal = 1")
    return [dict(r) for r in c.fetchall()]


def get_users_count() -> int:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM users")
    return int(c.fetchone()["cnt"])


# -------------------------
# Chats / group helpers
# -------------------------
def upsert_chat_settings(chat_id: int, notify_chat: bool, notify_interval: int, pinned_message_id: Optional[int] = None):
    db = get_db()
    c = db.cursor()
    c.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (int(chat_id),))
    # update fields
    c.execute("UPDATE chats SET notify_chat = ?, notify_interval = ?, pinned_message_id = ? WHERE chat_id = ?",
              (1 if notify_chat else 0, int(notify_interval), pinned_message_id, int(chat_id)))
    db.commit()


def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT notify_chat, notify_interval, last_reminder, pinned_message_id FROM chats WHERE chat_id = ?", (int(chat_id),))
    r = c.fetchone()
    if not r:
        return {"notify_chat": 0, "notify_interval": 15, "last_reminder": 0, "pinned_message_id": None}
    return {"notify_chat": int(r["notify_chat"]), "notify_interval": int(r["notify_interval"]),
            "last_reminder": int(r["last_reminder"]), "pinned_message_id": r["pinned_message_id"]}


def get_chats_with_notifications_enabled() -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT chat_id, notify_interval, last_reminder FROM chats WHERE notify_chat = 1")
    return [dict(r) for r in c.fetchall()]


def set_chat_last_reminder(chat_id: int, ts: int):
    db = get_db()
    c = db.cursor()
    c.execute("UPDATE chats SET last_reminder = ? WHERE chat_id = ?", (int(ts), int(chat_id)))
    db.commit()


# -------------------------
# Alerts helpers
# -------------------------
def insert_alert_record(user_id: int, resource: str, target_price: float, direction: str, speed: float, current_price: float,
                        alert_time_iso: str, chat_id: Optional[int]) -> int:
    db = get_db()
    c = db.cursor()
    created_at = datetime.now().isoformat()
    last_checked = created_at
    c.execute("""
        INSERT INTO alerts (user_id, resource, target_price, direction, speed, current_price, alert_time, created_at, status, chat_id, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
    """, (int(user_id), resource, float(target_price), direction, float(speed) if speed is not None else None,
          float(current_price) if current_price is not None else None, alert_time_iso, created_at, chat_id, last_checked))
    db.commit()
    return c.lastrowid


def get_active_alerts() -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM alerts WHERE status = 'active'")
    rows = c.fetchall()
    return [dict(r) for r in rows]


def get_alert_by_id(alert_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM alerts WHERE id = ?", (int(alert_id),))
    row = c.fetchone()
    return dict(row) if row else None


def get_alerts_for_user(user_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM alerts WHERE user_id = ?", (int(user_id),))
    return [dict(r) for r in c.fetchall()]


def update_alert_status(alert_id: int, status: str):
    db = get_db()
    c = db.cursor()
    c.execute("UPDATE alerts SET status = ?, last_checked = ? WHERE id = ?", (status, datetime.now().isoformat(), int(alert_id)))
    db.commit()


def update_alert_fields(alert_id: int, fields: Dict[str, Any]):
    if not fields:
        return
    db = get_db()
    c = db.cursor()
    keys = []
    vals = []
    for k, v in fields.items():
        keys.append(f"{k} = ?")
        vals.append(v)
    vals.append(datetime.now().isoformat())
    vals.append(int(alert_id))
    sql = f"UPDATE alerts SET {', '.join(keys)}, last_checked = ? WHERE id = ?"
    c.execute(sql, vals)
    db.commit()


def get_active_alerts_count() -> int:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM alerts WHERE status = 'active'")
    return int(c.fetchone()["cnt"])
