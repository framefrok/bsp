import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import threading
import time

from database import insert_market_record, get_latest_market, get_recent_market
from users import get_user_bonus, adjust_prices_for_user, get_user_settings

logger = logging.getLogger(__name__)

EMOJI_TO_RESOURCE = {
    "🪵": "Дерево",
    "🪨": "Камень",
    "🍞": "Провизия",
    "🐴": "Лошади"
}


def parse_market_message(text: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Парсит сообщение рынка.
    Возвращает словарь resource -> {buy, sell, quantity}
    Ожидаемый формат (поддерживаем несколько вариаций):
      Дерево: 96,342,449🪵
      📉Купить/продать: 8.31/6.80💰
    """
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    resources = {}
    current_resource = None
    current_quantity = 0

    # Паттерн для строки ресурса: "Название: число Эмодзи" (универсально)
    resource_pattern = r"^(.+?):\s*([0-9,]*)\s*([🪵🪨🍞🐴])$"
    # Паттерн для цен: "Купить/продать: 8.31/6.80" (возможен знак/эмодзи)
    price_pattern = r"(?:[📈📉]?\s*)?Купить/продать:\s*([0-9.]+)\s*/\s*([0-9.]+)\s*"

    for i, line in enumerate(lines):
        if line == "🎪 Рынок":
            continue

        res_match = re.match(resource_pattern, line)
        if res_match:
            name_part = res_match.group(1).strip()
            qty_str = res_match.group(2).replace(',', '').strip()
            emoji = res_match.group(3)

            current_resource = EMOJI_TO_RESOURCE.get(emoji, name_part)
            current_quantity = int(qty_str) if qty_str.isdigit() else 0
            continue

        price_match = re.search(price_pattern, line)
        if price_match and current_resource:
            try:
                buy_price = float(price_match.group(1))
                sell_price = float(price_match.group(2))
                resources[current_resource] = {
                    "buy": buy_price,
                    "sell": sell_price,
                    "quantity": current_quantity
                }
                logger.info(f"Распознано: {current_resource} — buy={buy_price}, sell={sell_price}, qty={current_quantity}")
                current_resource = None
                current_quantity = 0
            except ValueError as e:
                logger.error(f"Ошибка конвертации цен: {e}")
                continue

    if not resources:
        logger.warning("Не удалось распознать ни одного ресурса в тексте.")
        return None

    return resources


def handle_market_forward(bot, message):
    """
    Обработка пересланного сообщения с рынком.
    Учитывает настройки бонусов у игрока, который прислал форвард (если они есть),
    и сохраняет "сырые" цены в БД.
    """
    try:
        if not message.forward_from and not getattr(message, "forward_sender_name", None):
            bot.reply_to(message, "❌ Сообщение должно быть пересылкой от игрока или от бота рынка.")
            return

        logger.info(f"Обработка форварда от {message.forward_from} (date: {message.date})")

        parsed = parse_market_message(message.text)
        if not parsed:
            bot.reply_to(message, "❌ Не удалось распознать данные рынка. Проверьте формат сообщения.")
            return

        # Определяем id пользователя, от которого пришёл форвард (если есть)
        forwarder_id = message.forward_from.id if message.forward_from else None
        forwarder_bonus = get_user_bonus(forwarder_id) if forwarder_id else 0.0

        timestamp = int(message.date) if hasattr(message, "date") else int(time.time())
        saved = 0

        for resource, info in parsed.items():
            buy = float(info.get("buy", 0.0))
            sell = float(info.get("sell", 0.0))
            qty = int(info.get("quantity", 0) or 0)

            # Если форвард прислал игрок с бонусом, то его цены — скорректированы.
            # Надо вернуть "сырые" цены перед сохранением.
            if forwarder_bonus and forwarder_bonus > 0:
                raw_buy = buy * (1 + forwarder_bonus)
                raw_sell = sell / (1 + forwarder_bonus)
            else:
                raw_buy = buy
                raw_sell = sell

            date_iso = datetime.fromtimestamp(timestamp).isoformat()
            try:
                insert_market_record(resource, raw_buy, raw_sell, qty, timestamp, date_iso, forwarder_id)
                saved += 1
            except Exception as e:
                logger.error(f"Ошибка записи рынка для {resource}: {e}")

        if saved > 0:
            bot.reply_to(message, f"✅ Сохранено {saved} записей рынка (сырые цены).")
            # После обновления рынка — запустим пересчёт таймеров один раз
            try:
                import alerts
                threading.Thread(target=alerts.update_dynamic_timers_once, args=(bot,), daemon=True).start()
            except Exception as e:
                logger.error(f"Не удалось запустить пересчёт таймеров: {e}")
        else:
            bot.reply_to(message, "ℹ️ Данные рынка были распознаны, но ничего не сохранено.")

    except Exception as ex:
        logger.exception("Ошибка в handle_market_forward")
        bot.reply_to(message, f"❌ Ошибка при обработке форварда: {ex}")


def get_latest_data(resource: str) -> Optional[Dict[str, Any]]:
    return get_latest_market(resource)


def get_recent_data(resource: str, minutes: int = 15):
    return get_recent_market(resource, minutes)
