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
    Поддерживает формат типа:
      Дерево: 96,342,449🪵
      📉Купить/продать: 8.31/6.80💰
    """
    if not isinstance(text, str):
        return None

    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    resources = {}
    current_resource = None
    current_quantity = 0

    # Паттерн для строки ресурса: "Название: число Эмодзи"
    resource_pattern = r"^(.+?):\s*([0-9,]*)\s*([🪵🪨🍞🐴])$"
    # Паттерн для цен: "Купить/продать: 8.31/6.80"
    price_pattern = r"(?:[📈📉]?\s*)?Купить/продать:\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)"

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
                buy_price = float(price_match.group(1).replace(',', '.'))
                sell_price = float(price_match.group(2).replace(',', '.'))
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
        # безопасное получение forward_from и forward_sender_name
        forward_from = getattr(message, "forward_from", None)
        forward_sender_name = getattr(message, "forward_sender_name", None)

        if not forward_from and not forward_sender_name:
            bot.reply_to(message, "❌ Сообщение должно быть пересылкой от игрока или от бота рынка.")
            return

        # безопасность: text и date
        text = getattr(message, "text", "") or ""
        msg_date = getattr(message, "date", None)
        timestamp = int(msg_date) if msg_date else int(time.time())

        # логируем аккуратно — без прямого обращения к несуществующим атрибутам
        if forward_from:
            f_username = getattr(forward_from, "username", None)
            f_id = getattr(forward_from, "id", None)
            forward_info = f"username={f_username or 'n/a'} id={f_id or 'n/a'}"
        else:
            forward_info = f"forward_sender_name={forward_sender_name}"

        logger.info(f"Обработка форварда: {forward_info} (date: {msg_date})")

        parsed = parse_market_message(text)
        if not parsed:
            bot.reply_to(message, "❌ Не удалось распознать данные рынка. Проверьте формат сообщения.")
            return

        # определяем id отправителя пересылки (если есть) и его бонус
        forwarder_id = getattr(forward_from, "id", None) if forward_from else None
        forwarder_bonus = get_user_bonus(forwarder_id) if forwarder_id else 0.0

        saved = 0
        for resource, info in parsed.items():
            buy = float(info.get("buy", 0.0))
            sell = float(info.get("sell", 0.0))
            qty = int(info.get("quantity", 0) or 0)

            # Если форвард прислал игрок с бонусом, то его цены — скорректированы.
            # Надо вернуть "сырые" цены перед сохранением.
            if forwarder_bonus and forwarder_bonus > 0:
                # forwarder_bonus — дробный (например 0.22)
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
            # После обновления рынка — запустим пересчёт таймеров один раз (если есть alerts)
            try:
                import alerts
                # update_dynamic_timers_once ожидает bot в нашем наборе модулей
                threading.Thread(target=alerts.update_dynamic_timers_once, args=(bot,), daemon=True).start()
            except Exception as e:
                logger.error(f"Не удалось запустить пересчёт таймеров: {e}")
        else:
            bot.reply_to(message, "ℹ️ Данные рынка распознаны, но ничего не сохранено.")

    except Exception as ex:
        logger.exception("Ошибка в handle_market_forward")
        try:
            bot.reply_to(message, f"❌ Ошибка при обработке форварда: {ex}")
        except Exception:
            logger.error("Не удалось отправить сообщение об ошибке пользователю.")
