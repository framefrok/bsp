from database import get_db

def ensure_user(user_id: int):
    """Создание записи пользователя, если её нет"""
    db = get_db()
    c = db.cursor()
    c.execute("INSERT OR IGNORE INTO users (id, buy_bonus, sell_bonus) VALUES (?, ?, ?)", (user_id, 0, 0))
    db.commit()


def set_user_bonus(user_id: int, buy_bonus: int, sell_bonus: int):
    """Сохраняем бонусы игрока"""
    db = get_db()
    c = db.cursor()
    c.execute("UPDATE users SET buy_bonus=?, sell_bonus=? WHERE id=?", (buy_bonus, sell_bonus, user_id))
    db.commit()


def get_user_bonus(user_id: int):
    """Получаем бонусы"""
    db = get_db()
    c = db.cursor()
    c.execute("SELECT buy_bonus, sell_bonus FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    return row if row else (0, 0)


def adjust_prices_for_user(user_id: int, buy: int, sell: int):
    """Применение бонусов игрока к ценам"""
    buy_bonus, sell_bonus = get_user_bonus(user_id)
    return buy - buy_bonus, sell + sell_bonus
