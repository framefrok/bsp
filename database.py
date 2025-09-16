import sqlite3

DB_FILE = "marketbot.db"
_db = None

def get_db():
    """Глобальное подключение"""
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_FILE, check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db


def init_db():
    """Создание таблиц"""
    db = get_db()
    c = db.cursor()

    # пользователи
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            buy_bonus INTEGER DEFAULT 0,
            sell_bonus INTEGER DEFAULT 0
        )
    """)

    # рынок (храним сырые данные!)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buy INTEGER,
            sell INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # алерты
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            value INTEGER
        )
    """)

    db.commit()
