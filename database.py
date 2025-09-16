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
            resource TEXT DEFAULT 'generic',
            amount INTEGER DEFAULT 0,
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

    # настройки пушей
    c.execute("""
        CREATE TABLE IF NOT EXISTS push_settings (
            chat_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            interval INTEGER DEFAULT 15,
            pin_messages INTEGER DEFAULT 0
        )
    """)

    # закрепленные сообщения
    c.execute("""
        CREATE TABLE IF NOT EXISTS pinned_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()


def save_pinned_message(chat_id: int, message_id: int):
    """Сохраняем новый закреп"""
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO pinned_messages (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))
    db.commit()


def get_pinned_messages(chat_id: int):
    """Берем список закрепленных сообщений (макс 5 последних)"""
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, message_id FROM pinned_messages WHERE chat_id=? ORDER BY timestamp ASC", (chat_id,))
    return c.fetchall()


def delete_pinned_message(record_id: int):
    """Удаляем закреп из базы"""
    db = get_db()
    c = db.cursor()
    c.execute("DELETE FROM pinned_messages WHERE id=?", (record_id,))
    db.commit()
