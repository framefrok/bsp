import sqlite3
import time
from datetime import datetime
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

    # Пользователи — настройки (якорь, уровень торговли)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            has_anchor INTEGER DEFAULT 0,
            trade_level INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    # Рынок — храним "сырые" цены (без учёта бонусов у игроков)
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

    # Индексы для ускорения выборок
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
    c.execute("""
        SELECT * FROM market WHERE resource = ? ORDER BY timestamp DESC LIMIT 1
    """, (resource,))
    row = c.fetchone()
    return dict(row) if row else None


def get_recent_market(resource: str, minutes: int = 15) -> List[Dict[str, Any]]:
    cutoff = int(time.time()) - minutes * 60
    db = get_db()
    c = db.cursor()
    c.execute("""
        SELECT * FROM market WHERE resource = ? AND timestamp >= ? ORDER BY timestamp ASC
    """, (resource, cutoff))
    rows = c.fetchall()
    return [dict(r) for r in rows]


def get_all_latest() -> List[Dict[str, Any]]:
    """
    Возвращает список последних записей для каждого ресурса.
    """
    db = get_db()
    c = db.cursor()
    # Получим список уникальных ресурсов, затем для каждого — последняя запись
    c.execute("SELECT DISTINCT resource FROM market")
    resources = [r["resource"] for r in c.fetchall()]
    result = []
    for res in resources:
        row = get_latest_market(res)
        if row:
            result.append(row)
    return result


# -------------------------
# Users helpers
# -------------------------
def upsert_user_settings(user_id: int, has_anchor: bool, trade_level: int):
    db = get_db()
    c = db.cursor()
    # INSERT OR REPLACE: перезапишем/вставим
    c.execute("""
        INSERT INTO users (id, has_anchor, trade_level, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            has_anchor = excluded.has_anchor,
            trade_level = excluded.trade_level,
            updated_at = excluded.updated_at
    """, (int(user_id), int(has_anchor), int(trade_level), int(time.time())))
    db.commit()


def get_user_settings_db(user_id: int) -> Dict[str, int]:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT has_anchor, trade_level FROM users WHERE id = ?", (int(user_id),))
    row = c.fetchone()
    if not row:
        return {"has_anchor": 0, "trade_level": 0}
    return {"has_anchor": int(row["has_anchor"]), "trade_level": int(row["trade_level"])}


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
    """
    fields: словарь name->value
    """
    if not fields:
        return
    db = get_db()
    c = db.cursor()
    keys = []
    vals = []
    for k, v in fields.items():
        keys.append(f"{k} = ?")
        vals.append(v)
    vals.append(int(alert_id))
    sql = f"UPDATE alerts SET {', '.join(keys)}, last_checked = ? WHERE id = ?"
    vals.insert(-1, datetime.now().isoformat())
    c.execute(sql, vals)
    db.commit()
