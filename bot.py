# bot.py
import os
import logging
import threading
import asyncio
from datetime import datetime
from dotenv import load_dotenv

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# Попытка импортировать модули проекта (предполагаются уже реализованные)
try:
    import database
except Exception as e:
    raise ImportError(f"Модуль database не загружен: {e}")

try:
    import users
except Exception as e:
    raise ImportError(f"Модуль users не загружен: {e}")

try:
    import market
except Exception as e:
    raise ImportError(f"Модуль market не загружен: {e}")

try:
    import alerts
except Exception as e:
    raise ImportError(f"Модуль alerts не загружен: {e}")


# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("market_bot")

# load token
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в окружении. Добавьте в .env: BOT_TOKEN=...")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")


# --- Инициализация БД и фоновые задачи ---
def init_app():
    # Инициализация БД (если есть init_db)
    if hasattr(database, "init_db"):
        try:
            database.init_db()
            logger.info("Database initialized.")
        except Exception as e:
            logger.exception(f"Ошибка инициализации БД: {e}")
    else:
        logger.warning("database.init_db не найден — предполагается, что БД уже инициализирована.")

    # Запуск фоновых задач alerts
    try:
        if hasattr(alerts, "start_background_tasks"):
            alerts.start_background_tasks(bot)
            logger.info("Started alerts background tasks.")
        else:
            logger.warning("alerts.start_background_tasks не найден — фоновые таски не запущены.")
    except Exception as e:
        logger.exception(f"Не удалось запустить фоновые таски alerts: {e}")


# --- Changelog для /history bot ---
CHANGELOG = [
    "v1.0 — Старт бота, парсинг рынка и сохранение.",
    "v1.1 — Таймеры и оповещения.",
    "v1.2 — Учет бонусов (якорь + уровень торговли).",
    "v1.3 — Push-уведомления и управление закрепами.",
    "v1.4 — Авто-уведомления при простое базы и алерты по объёму.",
]


# ----------------------------
# Команды
# ----------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    # ensure user exists (if module supports)
    try:
        if hasattr(users, "ensure_user"):
            users.ensure_user(uid, getattr(message.from_user, "username", None))
    except Exception:
        logger.debug("ensure_user failed or absent.")
    text = (
        "👋 Привет! Я бот для отслеживания рынка.\n\n"
        "Команды:\n"
        "/push — показать текущие цены и управление уведомлениями\n"
        "/timer <ресурс> <цена> — поставить таймер\n"
        "/settings — настроить бонусы (якорь/уровень торговли)\n"
        "/stat — показать статистику и экстраполированные цены\n"
        "/history <ресурс>|bot — история по ресурсу или changelog бота\n"
        "/help — справка"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.reply_to(message,
                 "Инструкция:\n"
                 "• Перешлите форвард сообщения рынка (с эмодзи 🎪) — бот распарсит и сохранит исходные (сырые) цены.\n"
                 "• /push — показать рынок + кнопки управления уведомлениями (личные/чат, интервал, закрепы).\n"
                 "• /timer <ресурс> <цель> — установить таймер оповещения.\n"
                 "• /stat — статистика и прогноз текущей цены (экстраполяция).\n"
                 "• /history <ресурс> — история по ресурсу; /history bot — changelog.\n")


# ----------------------------
# /settings — якорь и уровень торговли (flow)
# ----------------------------
STATE_SETTINGS_ANCHOR = "settings_anchor"
STATE_SETTINGS_TRADE_LEVEL = "settings_trade_level"
_user_states = {}   # user_id -> state
_user_tmp = {}      # user_id -> temporary data


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    uid = message.from_user.id
    _user_states[uid] = STATE_SETTINGS_ANCHOR
    _user_tmp[uid] = {}
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Есть Якорь (+2%)", callback_data="anchor_yes"))
    kb.add(types.InlineKeyboardButton("❌ Нет якоря", callback_data="anchor_no"))
    bot.reply_to(message, "⚓️ У вас есть Якорь (даёт +2%)?", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("anchor_"))
def cb_anchor(call):
    uid = call.from_user.id
    if _user_states.get(uid) != STATE_SETTINGS_ANCHOR:
        bot.answer_callback_query(call.id, "Сессия настроек неактивна.")
        return
    bot.answer_callback_query(call.id)
    has_anchor = call.data == "anchor_yes"
    _user_tmp[uid]["has_anchor"] = has_anchor
    _user_states[uid] = STATE_SETTINGS_TRADE_LEVEL
    bot.send_message(uid, "⚖️ Укажите уровень знания 'Основы торговли' (0-10):")


@bot.message_handler(func=lambda m: _user_states.get(m.from_user.id) == STATE_SETTINGS_TRADE_LEVEL)
def msg_trade_level(message):
    uid = message.from_user.id
    try:
        lvl = int(message.text.strip())
        if lvl < 0 or lvl > 10:
            bot.reply_to(message, "❌ Уровень должен быть 0..10.")
            return
    except Exception:
        bot.reply_to(message, "❌ Введите целое число 0..10.")
        return

    has_anchor = _user_tmp.get(uid, {}).get("has_anchor", False)
    # Сохраняем в users (если есть функция)
    try:
        if hasattr(users, "save_user_settings"):
            users.save_user_settings(uid, has_anchor, lvl)
        elif hasattr(database, "upsert_user_settings"):
            database.upsert_user_settings(uid, bool(has_anchor), int(lvl))
    except Exception as e:
        logger.exception(f"Ошибка сохранения настроек: {e}")

    # Ответ пользователю
    try:
        bonus = users.get_user_bonus(uid)
        bonus_text = f"{bonus*100:.0f}%" if bonus else "0%"
    except Exception:
        bonus_text = "—"

    bot.reply_to(message, f"✅ Настройки сохранены!\nЯкорь: {'✅' if has_anchor else '❌'}\nУровень торговли: {lvl}\nВыгода: {bonus_text}")

    _user_states.pop(uid, None)
    _user_tmp.pop(uid, None)


# ----------------------------
# /push — показать рынок и управление уведомлениями
# ----------------------------
def _build_push_keyboard(user_id: int, chat_id: int):
    kb = types.InlineKeyboardMarkup(row_width=1)

    # Личные настройки
    personal = {"notify_personal": 1, "notify_interval": 15}
    try:
        if hasattr(users, "get_user_notification_settings"):
            personal = users.get_user_notification_settings(user_id)
    except Exception:
        logger.debug("users.get_user_notification_settings недоступна, используем значения по умолчанию")

    personal_on = bool(personal.get("notify_personal", 1))
    personal_interval = int(personal.get("notify_interval", 15))
    kb.add(types.InlineKeyboardButton(f"🔔 Личные: {'ВКЛ' if personal_on else 'ВЫКЛ'}", callback_data="toggle_personal"))
    kb.add(types.InlineKeyboardButton(f"⏱ Интервал (лично): {personal_interval} мин", callback_data="choose_personal_interval"))

    # Чатовые настройки (только если это группа)
    if chat_id is not None:
        chat_cfg = {"notify_chat": 0, "notify_interval": 15, "pinned_message_id": None}
        try:
            if hasattr(database, "get_chat_settings"):
                chat_cfg = database.get_chat_settings(chat_id)
        except Exception:
            logger.debug("database.get_chat_settings недоступна")

        chat_on = bool(chat_cfg.get("notify_chat", 0))
        chat_interval = int(chat_cfg.get("notify_interval", 15))
        kb.add(types.InlineKeyboardButton(f"💬 Чат: {'ВКЛ' if chat_on else 'ВЫКЛ'}", callback_data=f"toggle_chat:{chat_id}"))
        kb.add(types.InlineKeyboardButton(f"⏱ Интервал (чат): {chat_interval} мин", callback_data=f"choose_chat_interval:{chat_id}"))
        kb.add(types.InlineKeyboardButton("📌 Закреплять сообщения в чате", callback_data=f"toggle_pin_chat:{chat_id}"))
        kb.add(types.InlineKeyboardButton("🗑 Удалить все закрепы", callback_data=f"clear_chat_pins:{chat_id}"))

    return kb


@bot.message_handler(commands=["push"])
def cmd_push(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    # Получаем все последние записи рынка (если есть функция)
    latest_list = []
    try:
        if hasattr(database, "get_all_latest"):
            latest_list = database.get_all_latest()
    except Exception:
        logger.debug("database.get_all_latest недоступна")

    if not latest_list:
        bot.reply_to(message, "⚠️ Рынок пуст — пришлите пересылку (форвард) с рынком.")
        return

    # Формируем текст с учётом бонусов пользователя
    lines = ["📊 <b>Текущий рынок (скорректирован под вас):</b>"]
    for rec in latest_list:
        resource = rec.get("resource", "resource")
        raw_buy = rec.get("buy", 0.0)
        raw_sell = rec.get("sell", 0.0)
        try:
            adj_buy, adj_sell = users.adjust_prices_for_user(uid, raw_buy, raw_sell)
        except Exception:
            # fallback: assume function adjust_prices_for_user takes (user_id, buy, sell)
            try:
                adj_buy, adj_sell = users.adjust_prices_for_user(uid, raw_buy, raw_sell)
            except Exception:
                adj_buy, adj_sell = raw_buy, raw_sell
        lines.append(f"{resource}: Купить {adj_buy:.3f} / Продать {adj_sell:.3f}")

    text = "\n".join(lines)
    try:
        sent = bot.send_message(chat_id, text, parse_mode="HTML")
    except ApiTelegramException as api_e:
        # Telegram API error - if conflict (409) -> give clear instruction and stop
        if getattr(api_e, "result_json", {}).get("error_code") == 409 or "409" in str(api_e):
            logger.error("Ошибка 409 Conflict: вероятно уже запущен другой экземпляр бота. Остановите другие процессы.")
            raise SystemExit("409 Conflict: Завершите другие экземпляры бота и попробуйте снова.")
        else:
            logger.exception("Ошибка при отправке /push сообщения")
            bot.reply_to(message, "❌ Не удалось отправить сообщение.")
            return

    # отправляем клавиатуру управления
    kb = _build_push_keyboard(uid, chat_id if message.chat.type in ['group', 'supergroup'] else None)
    bot.send_message(uid, "⚙️ Управление уведомлениями:", reply_markup=kb)


# --- callbacks: personal toggles / intervals ---
@bot.callback_query_handler(func=lambda c: c.data == "toggle_personal")
def cb_toggle_personal(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    try:
        cur = users.get_user_notification_settings(uid)
        new_state = 0 if cur.get("notify_personal", 1) == 1 else 1
        users.set_user_notification(uid, bool(new_state), cur.get("notify_interval", 15))
        bot.send_message(uid, f"🔔 Личные уведомления {'включены' if new_state else 'выключены'}.")
    except Exception:
        logger.exception("Ошибка при переключении личных уведомлений")
        bot.send_message(uid, "❌ Не удалось изменить личные уведомления.")


@bot.callback_query_handler(func=lambda c: c.data == "choose_personal_interval")
def cb_choose_personal_interval(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=4)
    for m in (5, 15, 30, 60):
        kb.add(types.InlineKeyboardButton(f"{m} мин", callback_data=f"set_personal_interval:{m}"))
    bot.send_message(uid, "Выберите интервал (лично):", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("set_personal_interval:"))
def cb_set_personal_interval(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    try:
        _, m = call.data.split(":", 1)
        m = int(m)
        cur = users.get_user_notification_settings(uid)
        users.set_user_notification(uid, bool(cur.get("notify_personal", 1)), m)
        bot.send_message(uid, f"⏱ Личный интервал установлен: {m} мин.")
    except Exception:
        logger.exception("Ошибка установки личного интервала")
        bot.send_message(uid, "❌ Не удалось установить интервал.")


# --- callbacks: chat toggles / intervals / pin management ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_chat:"))
def cb_toggle_chat(call):
    bot.answer_callback_query(call.id)
    try:
        _, chat_id_str = call.data.split(":", 1)
        chat_id = int(chat_id_str)
        cfg = database.get_chat_settings(chat_id)
        new_state = 0 if cfg.get("notify_chat", 0) == 1 else 1
        database.upsert_chat_settings(chat_id, bool(new_state), cfg.get("notify_interval", 15), cfg.get("pinned_message_id"))
        bot.send_message(call.from_user.id, f"💬 Уведомления в чате {'включены' if new_state else 'выключены'}.")
    except Exception:
        logger.exception("Ошибка toggle_chat")
        bot.answer_callback_query(call.id, "Ошибка изменения настроек чата.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("choose_chat_interval:"))
def cb_choose_chat_interval(call):
    bot.answer_callback_query(call.id)
    try:
        _, chat_id_str = call.data.split(":", 1)
        chat_id = int(chat_id_str)
        kb = types.InlineKeyboardMarkup(row_width=4)
        for m in (5, 15, 30, 60):
            kb.add(types.InlineKeyboardButton(f"{m} мин", callback_data=f"set_chat_interval:{chat_id}:{m}"))
        bot.send_message(call.from_user.id, "Выберите интервал (чат):", reply_markup=kb)
    except Exception:
        logger.exception("Ошибка choose_chat_interval")
        bot.answer_callback_query(call.id, "Ошибка.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("set_chat_interval:"))
def cb_set_chat_interval(call):
    bot.answer_callback_query(call.id)
    try:
        _, chat_id_str, m_str = call.data.split(":", 2)
        chat_id = int(chat_id_str)
        m = int(m_str)
        cfg = database.get_chat_settings(chat_id)
        database.upsert_chat_settings(chat_id, cfg.get("notify_chat", 1), m, cfg.get("pinned_message_id"))
        bot.send_message(call.from_user.id, f"⏱ Интервал чата установлен: {m} мин.")
    except Exception:
        logger.exception("Ошибка set_chat_interval")
        bot.answer_callback_query(call.id, "Ошибка установки интервала.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_pin_chat:"))
def cb_toggle_pin_chat(call):
    bot.answer_callback_query(call.id)
    try:
        _, chat_id_str = call.data.split(":", 1)
        chat_id = int(chat_id_str)
        cfg = database.get_chat_settings(chat_id)
        new_pin = 0 if cfg.get("pinned_message_id") else 1
        # Keep notify_interval and notify_chat as-is
        database.upsert_chat_settings(chat_id, cfg.get("notify_chat", 1), cfg.get("notify_interval", 15), pinned_message_id=cfg.get("pinned_message_id") if new_pin == 0 else None)
        bot.send_message(call.from_user.id, f"📌 Авто-закрепление {'включено' if new_pin else 'выключено'}.")
    except Exception:
        logger.exception("Ошибка toggle_pin_chat")
        bot.answer_callback_query(call.id, "Ошибка.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("clear_chat_pins:"))
def cb_clear_chat_pins(call):
    bot.answer_callback_query(call.id)
    try:
        _, chat_id_str = call.data.split(":", 1)
        chat_id = int(chat_id_str)
        pinned = database.get_pinned_messages(chat_id)
        for rec in pinned:
            try:
                bot.unpin_chat_message(chat_id, rec["message_id"])
                bot.delete_message(chat_id, rec["message_id"])
            except Exception:
                logger.exception("Ошибка удаления закрепа")
            database.delete_pinned_message(rec["id"])
        bot.send_message(call.from_user.id, "🗑 Все закрепы удалены.")
    except Exception:
        logger.exception("Ошибка clear_chat_pins")
        bot.answer_callback_query(call.id, "Ошибка при удалении закрепов.")


# ----------------------------
# /stat — статистика и экстраполяция
# ----------------------------
@bot.message_handler(commands=["stat"])
def cmd_stat(message):
    uid = message.from_user.id
    try:
        # сбор общей статистики (если доступны функции)
        total_records = database.get_total_market_records() if hasattr(database, "get_total_market_records") else None
        unique_resources = database.get_unique_resources_count() if hasattr(database, "get_unique_resources_count") else None
        users_count = database.get_users_count() if hasattr(database, "get_users_count") else None
        active_alerts = database.get_active_alerts_count() if hasattr(database, "get_active_alerts_count") else None

        parts = ["<b>📊 Статистика бота</b>"]
        if total_records is not None:
            parts.append(f"• Всего записей рынка: {total_records}")
        if unique_resources is not None:
            parts.append(f"• Уникальных ресурсов: {unique_resources}")
        if users_count is not None:
            parts.append(f"• Пользователей: {users_count}")
        if active_alerts is not None:
            parts.append(f"• Активных оповещений: {active_alerts}")
        parts.append("─" * 24)
        parts.append("<b>Текущие оценки цен (экстраполяция):</b>")

        # get latest resources
        latest_list = database.get_all_latest() if hasattr(database, "get_all_latest") else []
        if not latest_list:
            parts.append("Нет данных по рынку.")
            bot.reply_to(message, "\n".join(parts), parse_mode="HTML")
            return

        for rec in latest_list:
            resource = rec.get("resource", "res")
            pred_buy, pred_sell, trend, speed_adj, last_ts = market.compute_extrapolated_price(resource, uid, lookback_minutes=60)
            qty_week = database.get_week_max_qty(resource) if hasattr(database, "get_week_max_qty") else rec.get("quantity", 0)
            last_time = datetime.fromtimestamp(last_ts).strftime("%d.%m %H:%M") if last_ts else "—"
            trend_icon = "📈" if trend == "up" else "📉" if trend == "down" else "➡️"
            speed_text = f"{speed_adj:+.4f}/мин" if speed_adj is not None else "неизвестно"
            parts.append(
                f"{resource} — {trend_icon} {trend}\n"
                f"  ~Купить: {pred_buy:.3f if pred_buy is not None else '—'} / ~Продать: {pred_sell:.3f if pred_sell is not None else '—'}\n"
                f"  Последняя: {last_time} | Скорость: {speed_text}\n"
                f"  Макс. объём за неделю: {qty_week}"
            )

        bot.reply_to(message, "\n\n".join(parts), parse_mode="HTML")
    except Exception:
        logger.exception("Ошибка в /stat")
        bot.reply_to(message, "❌ Не удалось получить статистику.")


# ----------------------------
# /history
# ----------------------------
@bot.message_handler(commands=["history"])
def cmd_history(message):
    try:
        parts = message.text.split()[1:] if len(message.text.split()) > 1 else []
        if not parts:
            bot.reply_to(message, "Использование:\n/history <ресурс> — история по ресурсу\n/history bot — changelog")
            return
        arg = parts[0].lower()
        if arg in ("bot", "updates", "changelog"):
            text = "<b>📜 Changelog:</b>\n" + "\n".join(f"• {l}" for l in CHANGELOG)
            bot.reply_to(message, text, parse_mode="HTML")
            return
        resource = parts[0].capitalize()
        if hasattr(database, "get_market_history"):
            recs = database.get_market_history(resource, limit=50)
        else:
            recs = []
        if not recs:
            bot.reply_to(message, f"Нет данных по {resource}.")
            return
        lines = [f"📊 История по {resource} (последние {len(recs)}):"]
        for r in recs:
            t = datetime.fromtimestamp(r["timestamp"]).strftime("%d.%m %H:%M")
            try:
                adj_buy, adj_sell = users.adjust_prices_for_user(message.from_user.id, r["buy"], r["sell"])
            except Exception:
                adj_buy, adj_sell = r["buy"], r["sell"]
            lines.append(f"{t} — Купить: {adj_buy:.3f} / Продать: {adj_sell:.3f} (qty: {r.get('quantity', 0)})")
        bot.reply_to(message, "\n".join(lines))
    except Exception:
        logger.exception("Ошибка в /history")
        bot.reply_to(message, "❌ Ошибка при получении истории.")


# ----------------------------
# Обработчик форварда рынка (безопасно)
# ----------------------------
@bot.message_handler(func=lambda msg: isinstance(getattr(msg, "text", None), str) and "🎪" in msg.text)
def handler_market_forward(message):
    # Требуем, чтобы это была пересылка (forward_from или forward_sender_name)
    forward_from = getattr(message, "forward_from", None)
    forward_sender_name = getattr(message, "forward_sender_name", None)
    if not forward_from and not forward_sender_name:
        # не пересылка — игнорируем
        return
    try:
        market.handle_market_forward(bot, message)
    except Exception:
        logger.exception("Ошибка при обработке форварда")
        try:
            bot.reply_to(message, "❌ Ошибка при обработке форварда.")
        except Exception:
            pass


# ----------------------------
# /timer -> делегируем alerts
# ----------------------------
@bot.message_handler(commands=["timer"])
def cmd_timer(message):
    try:
        if hasattr(alerts, "cmd_timer_handler"):
            alerts.cmd_timer_handler(bot, message)
        else:
            bot.reply_to(message, "Команда /timer временно недоступна.")
    except Exception:
        logger.exception("Ошибка в /timer")
        bot.reply_to(message, "❌ Ошибка при установке таймера.")


# ----------------------------
# Запуск бота
# ----------------------------
def start_polling():
    try:
        logger.info("Запуск polling...")
        # Если возникает ApiTelegramException 409 — выходим с понятным логом
        try:
            bot.infinity_polling(skip_pending=True, timeout=20)
        except ApiTelegramException as api_e:
            # 409 Conflict -> вероятен уже запущенный экземпляр
            if "409" in str(api_e) or (hasattr(api_e, "result_json") and api_e.result_json.get("error_code") == 409):
                logger.error("ApiTelegramException 409: конфликт getUpdates. Убедитесь, что запущен только один экземпляр бота.")
                raise SystemExit("409 Conflict: остановите другой экземпляр бота и запустите снова.")
            else:
                raise
    except KeyboardInterrupt:
        logger.info("Остановлено по Ctrl+C")
    except SystemExit as sx:
        logger.error(f"Завершение: {sx}")
        raise
    except Exception:
        logger.exception("Неожиданная ошибка polling")


if __name__ == "__main__":
    init_app()

    # Запуск polling в отдельном потоке — чтобы event loop/background tasks могли работать
    polling_thread = threading.Thread(target=start_polling, daemon=True)
    polling_thread.start()

    # Если alerts предоставляет функцию запуска асинхронных задач — используем
    # иначе предполагаем, что alerts.start_background_tasks уже запустил потоки.
    try:
        if hasattr(alerts, "start_background_tasks"):
            # если start_background_tasks не вызван в init_app (возможно уже вызван), пропускаем
            pass
    except Exception:
        logger.exception("Не удалось запустить background tasks")

    # Если нужно, держим главный поток живым
    try:
        while True:
            # можно периодически проверять статус: например, убедиться что polling_thread жив
            if not polling_thread.is_alive():
                logger.error("Polling thread завершился. Выход.")
                break
            # даём другим потокам время поработать
            threading.Event().wait(1)
    except KeyboardInterrupt:
        logger.info("Завершение по Ctrl+C")
