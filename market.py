from datetime import datetime
import re
import logging

from database import market_table, users_table

logger = logging.getLogger("Market")

# Маппинг ресурсов
EMOJI_TO_RESOURCE = {
    "🌲": "Дерево",
    "🪨": "Камень",
    "⛏️": "Железо",
    "🌾": "Пшеница",
}

# =====================================================
# Работа с бонусами
# =====================================================

def get_user_bonus(user_id: int) -> int:
    """Возвращает бонус торговли пользователя (в процентах)."""
    user = users_table.get(doc_id=user_id)
    return user.get("trade_bonus", 0) if user else 0

def adjust_prices_for_user(user_id: int, buy: float, sell: float):
    """
    Применяет бонус торговли игрока к ценам.
    Бонус = % снижения покупки и увеличения продажи.
    """
    bonus = get_user_bonus(user_id)
    factor = 1 + bonus / 100.0
    adj_buy = buy / factor
    adj_sell = sell * factor
    return adj_buy, adj_sell

def revert_prices_from_bonus(buy: float, sell: float, bonus: int):
    """
    Обратное преобразование: если игрок с бонусом прислал данные,
    пересчитаем к «чистым» (как будто без бонусов).
    """
    factor = 1 + bonus / 100.0
    raw_buy = buy * factor
    raw_sell = sell / factor
    return raw_buy, raw_sell

# =====================================================
# Работа с рынком
# =====================================================

def save_market_data(resource: str, buy: float, sell: float):
    """Сохраняет новые данные по ресурсу."""
    market_table.upsert({
        "resource": resource,
        "buy": buy,
        "sell": sell,
        "timestamp": datetime.now().timestamp()
    }, where("resource") == resource)

def get_latest_data(resource: str):
    """Возвращает последние данные по ресурсу."""
    rec = market_table.get(where("resource") == resource)
    return rec if rec else None

# =====================================================
# Парсинг форварда
# =====================================================

def handle_market_forward(bot, message, user_id):
    """
    Обработка форварда рынка.
    Определяем бонус игрока из /settings и пересчитываем к «чистым» ценам.
    """
    bonus = get_user_bonus(user_id)

    text = message.text
    lines = text.splitlines()

    for line in lines:
        match = re.match(r"(.+):\s*Покупка\s*(\d+(?:[\.,]\d+)?)\s*/\s*Продажа\s*(\d+(?:[\.,]\d+)?)", line)
        if not match:
            continue

        res = match.group(1).strip()
        buy = float(match.group(2).replace(",", "."))
        sell = float(match.group(3).replace(",", "."))

        if bonus > 0:
            raw_buy, raw_sell = revert_prices_from_bonus(buy, sell, bonus)
        else:
            raw_buy, raw_sell = buy, sell

        save_market_data(res, raw_buy, raw_sell)

    bot.reply_to(message, "✅ Рынок обновлён.")
