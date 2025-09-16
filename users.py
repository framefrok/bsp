import sqlite3
from database import get_connection

# Создаём таблицу пользователей если её нет
def init_users_table():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            bonus_active INTEGER DEFAULT 0,
            push_enabled INTEGER DEFAULT 1,
            group_push_enabled INTEGER DEFAULT 1,
            push_interval INTEGER DEFAULT 15,
            pin_enabled INTEGER DEFAULT 0
        )
        """)
        conn.commit()

# Проверка и добавление пользователя
def ensure_user(user_id: int, username: str = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        if cursor.fetchone() is None:
            cursor.execute("""
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            """, (user_id, username))
            conn.commit()

# Включить / выключить бонус
def set_user_bonus(user_id: int, active: bool):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET bonus_active=? WHERE user_id=?", (1 if active else 0, user_id))
        conn.commit()

# Настройка push
def set_push_settings(user_id: int, enabled: bool = None, interval: int = None, group_enabled: bool = None, pin_enabled: bool = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if enabled is not None:
            cursor.execute("UPDATE users SET push_enabled=? WHERE user_id=?", (1 if enabled else 0, user_id))
        if group_enabled is not None:
            cursor.execute("UPDATE users SET group_push_enabled=? WHERE user_id=?", (1 if group_enabled else 0, user_id))
        if interval is not None:
            cursor.execute("UPDATE users SET push_interval=? WHERE user_id=?", (interval, user_id))
        if pin_enabled is not None:
            cursor.execute("UPDATE users SET pin_enabled=? WHERE user_id=?", (1 if pin_enabled else 0, user_id))
        conn.commit()

# Получение настроек push
def get_push_settings(user_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT push_enabled, push_interval, group_push_enabled, pin_enabled FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {
                "push_enabled": bool(row[0]),
                "push_interval": row[1],
                "group_push_enabled": bool(row[2]),
                "pin_enabled": bool(row[3])
            }
        return None

# Проверка бонуса пользователя
def has_bonus(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bonus_active FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row else False

# Фильтрация цен с учётом бонуса
def adjust_prices_for_user(user_id: int, base_price: int) -> int:
    if has_bonus(user_id):
        return int(base_price * 1.1)  # бонус даёт +10% к цене
    return base_price
