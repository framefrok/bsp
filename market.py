# market.py
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import threading
import time

import database
import users

logger = logging.getLogger(__name__)

EMOJI_TO_RESOURCE = {
    "🪵": "Дерево",
    "🪨": "Камень",
    "🍞": "Провизия",
    "🐴": "Лошади"
}


def parse_market_message(text: str) -> Optional[Dict[str, Dict[str, Any]]]:
    if not isinstance(text, str):
        return None

    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    resources = {}
    current_resource = None
    current_quantity = 0

    resource_pattern = r"^(.+?):\s*([0-9,]*)\s*([🪵🪨🍞🐴])$"
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
    try:
        forward_from = getattr(message, "forward_from", None)
        forward_sender_name = getattr(message, "forward_sender_name", None)
        if not forward_from and not forward_sender_name:
            bot.reply_to(message, "❌ Сообщение должно быть пересылкой от игрока или от бота рынка.")
            return

        text = getattr(message, "text", "") or ""
        msg_date = getattr(message, "date", None)
        timestamp = int(msg_date) if msg_date else int(time.time())

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

        forwarder_id = getattr(forward_from, "id", None) if forward_from else None
        forwarder_bonus = users.get_user_bonus(forwarder_id) if forwarder_id else 0.0

        saved = 0
        for resource, info in parsed.items():
            buy = float(info.get("buy", 0.0))
            sell = float(info.get("sell", 0.0))
            qty = int(info.get("quantity", 0) or 0)

            # Если форвард пришёл от игрока с бонусом — вернуть сырой
            if forwarder_bonus and forwarder_bonus > 0:
                raw_buy = buy * (1 + forwarder_bonus)
                raw_sell = sell / (1 + forwarder_bonus)
            else:
                raw_buy = buy
                raw_sell = sell

            date_iso = datetime.fromtimestamp(timestamp).isoformat()
            try:
                database.insert_market_record(resource, raw_buy, raw_sell, qty, timestamp, date_iso, forwarder_id)
                saved += 1
            except Exception as e:
                logger.error(f"Ошибка записи рынка для {resource}: {e}")

        if saved > 0:
            bot.reply_to(message, f"✅ Сохранено {saved} записей рынка (сырые цены).")
            try:
                import alerts
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


# -------------------------
# Extrapolation / helpers
# -------------------------
def calculate_speed(records: list, price_field: str = "buy") -> Optional[float]:
    if not records or len(records) < 2:
        return None
    first = records[0]
    last = records[-1]
    price_delta = last[price_field] - first[price_field]
    time_delta_minutes = (last['timestamp'] - first['timestamp']) / 60.0
    if time_delta_minutes < 0.1:
        return None
    speed = price_delta / time_delta_minutes
    return round(speed, 6)


def get_trend(records: list, price_field: str = "buy") -> str:
    if not records or len(records) < 2:
        return "stable"
    first_price = records[0][price_field]
    last_price = records[-1][price_field]
    if last_price > first_price:
        return "up"
    elif last_price < first_price:
        return "down"
    else:
        return "stable"


def compute_extrapolated_price(resource: str, user_id: int, lookback_minutes: int = 60):
    """
    Возвращает (pred_buy, pred_sell, trend, speed_adj, last_ts)
    pred_* — скорректированные для пользователя текущие (экстраполированные) цены.
    """
    latest = database.get_latest_market(resource)
    if not latest:
        return None, None, "unknown", None, None

    records = database.get_recent_market(resource, minutes=lookback_minutes)
    last_ts = latest['timestamp']
    raw_buy = latest['buy']
    raw_sell = latest['sell']

    bonus = users.get_user_bonus(user_id)
    adj_buy, adj_sell = users.adjust_prices_for_user(user_id, raw_buy, raw_sell)

    if not records or len(records) < 2:
        # Без тренда — просто вернуть последнее скорректированное
        return adj_buy, adj_sell, "stable", None, last_ts

    speed_raw = calculate_speed(records, "buy")
    if speed_raw is None:
        return adj_buy, adj_sell, "stable", None, last_ts

    # скорость для пользователя (скорректированная)
    speed_adj = speed_raw / (1 + bonus) if isinstance(bonus, float) else speed_raw

    # количество минут с последней записи
    now_ts = int(time.time())
    elapsed_minutes = (now_ts - last_ts) / 60.0
    pred_buy = adj_buy + (speed_adj * elapsed_minutes) if speed_adj is not None else adj_buy
    pred_sell = adj_sell + (speed_adj * elapsed_minutes) if speed_adj is not None else adj_sell

    trend = get_trend(records, "buy")
    return pred_buy, pred_sell, trend, speed_adj, last_ts
