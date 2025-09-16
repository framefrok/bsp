# bot.py
import logging
import os
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
import telebot
from telebot import types
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import database
import market
import users
import alerts

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bsp_bot")

# Загрузка токена
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в окружении. Поместите токен в .env")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Инициализация БД
database.init_db()

# Состояния для /settings
user_states = {}
user_data = {}
STATE_SETTINGS_ANCHOR = "settings_anchor"
STATE_SETTINGS_TRADE_LEVEL = "settings_trade_level"

# Changelog (history of bot updates)
CHANGELOG = [
    "v1.0 — Первоначальная версия: парсинг рынка, сохранение сырых данных.",
    "v1.1 — Добавлены алерты и таймеры, пересчёт по тренду.",
    "v1.2 — Поддержка бонусов (якорь + уровень торговли), корректировка цен.",
    "v1.3 — Уведомления о просроченной базе и настройки интервала.",
    "v1.4 — Улучшенный /stat с экстраполяцией текущей цены и недельной статистикой.",
    # Добавляйте записи по мере изменений
]


# -------------------------
# Команды
# -------------------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
                 "👋 Привет! Я бот для отслеживания рынка.\n\n"
                 "Команды:\n"
                 "/push — показать текущий рынок в чате (учитывает ваши бонусы и позволяет настроить напоминания)\n"
                 "/timer <ресурс> <цена> — установить таймер\n"
                 "/settings — настроить ваши бонусы (якорь и уровень торговли)\n"
                 "/stat — показать статистику бота и экстраполированные цены\n"
                 "/history <ресурс>|bot — история по ресурсу или список обновлений бота\n"
                 "/help — помощь")


@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(message,
                 "Инструкция:\n"
                 "• Пришлите форвард сообщения рынка (пересылка). Бот распарсит и сохранит 'сырые' цены.\n"
                 "• /push — разместить текущие цены в чате (цены будут скорректированы под вас) и настроить напоминания.\n"
                 "• /timer <ресурс> <цель> — установить таймер оповещения.\n"
                 "• /settings — настроить ваши бонусы.\n"
                 "• /stat — полная статистика и оценка текущих цен.\n"
                 "• /history <ресурс> — история обновлений рынка по ресурсу.\n"
                 "• /history bot — список обновлений (changelog).")


# -------------------------
# Settings (/settings) flow
# -------------------------
@bot.message_handler(commands=['settings'])
def cmd_settings(message):
    user_id = message.from_user.id
    user_states[user_id] = STATE_SETTINGS_ANCHOR
    user_data[user_id] = {}

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Да, есть Якорь", callback_data="anchor_yes"))
    kb.add(InlineKeyboardButton("❌ Нет", callback_data="anchor_no"))
    bot.reply_to(message, "⚓️ У вас есть Якорь (даёт +2%)?", reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith('anchor_'))
def handle_anchor_choice(call):
    user_id = call.from_user.id
    if user_states.get(user_id) != STATE_SETTINGS_ANCHOR:
        bot.answer_callback_query(call.id, "Сессия настроек неактивна.")
        return
    bot.answer_callback_query(call.id)
    has_anchor = call.data == "anchor_yes"
    user_data[user_id]["has_anchor"] = has_anchor
    user_states[user_id] = STATE_SETTINGS_TRADE_LEVEL
    bot.send_message(user_id, "⚖️ Укажите уровень знания 'Основы торговли' (0-10):")


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_SETTINGS_TRADE_LEVEL)
def handle_trade_level(message):
    user_id = message.from_user.id
    try:
        level = int(message.text.strip())
        if level < 0 or level > 10:
            bot.reply_to(message, "❌ Уровень должен быть от 0 до 10.")
            return
    except Exception:
        bot.reply_to(message, "❌ Введите целое число от 0 до 10.")
        return

    has_anchor = user_data.get(user_id, {}).get("has_anchor", False)
    users.save_user_settings(user_id, has_anchor, level)

    bonus = users.get_user_bonus(user_id)
    bot.reply_to(message,
                 f"✅ Настройки сохранены!\n"
                 f"Якорь: {'✅' if has_anchor else '❌'}\n"
                 f"Уровень торговли: {level}\n"
                 f"Общая выгода: {bonus*100:.0f}%")

    user_states.pop(user_id, None)
    user_data.pop(user_id, None)


# -------------------------
# Push (показ рынка + настройки уведомлений)
# -------------------------
@bot.message_handler(commands=['push'])
def cmd_push(message):
    try:
        user_id = message.from_user.id
        resources = database.get_all_latest()
        if not resources:
            bot.reply_to(message, "⚠️ Рынок пока пуст. Пришлите форвард с рынка.")
            return

        lines = ["📊 <b>Текущий рынок (скорректированно под вас):</b>"]
        for rec in resources:
            res = rec['resource']
            raw_buy = rec['buy']
            raw_sell = rec['sell']
            adj_buy, adj_sell = users.adjust_prices_for_user(user_id, raw_buy, raw_sell)
            lines.append(f"{res}: Купить {adj_buy:.3f} / Продать {adj_sell:.3f}")

        text = "\n".join(lines)
        sent = bot.send_message(message.chat.id, text, parse_mode="HTML")

        # Отправляем клавиатуру с управлением уведомлениями
        user_settings = users.get_user_notification_settings(user_id)
        personal_on = bool(user_settings["notify_personal"])
        personal_interval = int(user_settings["notify_interval"])

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(f"🔔 Личные уведомления: {'ВКЛ' if personal_on else 'ВЫКЛ'}", callback_data="toggle_personal"))
        kb.add(InlineKeyboardButton(f"⏱ Интервал (лично): {personal_interval} мин", callback_data="choose_personal_interval"))

        # Если команда выполнена в группе — добавляем контроль чата
        if message.chat.type in ['group', 'supergroup']:
            chat_id = message.chat.id
            chat_cfg = database.get_chat_settings(chat_id)
            chat_on = bool(chat_cfg.get("notify_chat", 0))
            chat_interval = int(chat_cfg.get("notify_interval", 15))
            kb.add(InlineKeyboardButton(f"💬 Уведомления в чате: {'ВКЛ' if chat_on else 'ВЫКЛ'}", callback_data=f"toggle_chat:{chat_id}"))
            kb.add(InlineKeyboardButton(f"⏱ Интервал (чат): {chat_interval} мин", callback_data=f"choose_chat_interval:{chat_id}"))

            # Сохраняем pinned_message_id если удалось закрепить
            try:
                me = bot.get_me()
                member = bot.get_chat_member(chat_id, me.id)
                can_pin = getattr(member, "can_pin_messages", False) or getattr(member, "status", "") == "creator"
                if can_pin:
                    bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
                    database.upsert_chat_settings(chat_id, chat_on, chat_interval, pinned_message_id=sent.message_id)
            except Exception as e:
                logger.debug(f"Не удалось закрепить сообщение в чате: {e}")

        bot.send_message(user_id, "⚙️ Управление уведомлениями:", reply_markup=kb)

    except Exception as e:
        logger.exception("Ошибка в /push")
        bot.reply_to(message, "❌ Произошла ошибка при выполнении /push.")


# callback handlers для настроек уведомлений
@bot.callback_query_handler(func=lambda call: call.data == 'toggle_personal')
def handle_toggle_personal(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id, "Обновляю настройки...")
    cur = users.get_user_notification_settings(uid)
    new_state = 0 if cur["notify_personal"] == 1 else 1
    users.set_user_notification(uid, bool(new_state), cur["notify_interval"])
    bot.send_message(uid, f"🔔 Личные уведомления {'включены' if new_state else 'выключены'}.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('choose_personal_interval'))
def handle_choose_personal_interval(call):
    uid = call.from_user.id
    kb = InlineKeyboardMarkup(row_width=4)
    for m in [5, 15, 30, 60]:
        kb.add(InlineKeyboardButton(f"{m} мин", callback_data=f"set_personal_interval:{m}"))
    bot.answer_callback_query(call.id)
    bot.send_message(uid, "Выберите интервал напоминаний (лично):", reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_personal_interval:'))
def handle_set_personal_interval(call):
    uid = call.from_user.id
    try:
        _, m = call.data.split(':', 1)
        m = int(m)
        cur = users.get_user_notification_settings(uid)
        users.set_user_notification(uid, bool(cur["notify_personal"]), m)
        bot.answer_callback_query(call.id, f"Интервал установлен: {m} мин.")
        bot.send_message(uid, f"⏱ Интервал личных уведомлений установлен: {m} мин.")
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка установки интервала.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_chat:'))
def handle_toggle_chat(call):
    try:
        _, chat_id_str = call.data.split(':', 1)
        chat_id = int(chat_id_str)
        bot.answer_callback_query(call.id, "Обновляю настройки для чата...")
        cfg = database.get_chat_settings(chat_id)
        new_state = 0 if cfg.get("notify_chat", 0) == 1 else 1
        database.upsert_chat_settings(chat_id, bool(new_state), cfg.get("notify_interval", 15), cfg.get("pinned_message_id"))
        bot.send_message(call.from_user.id, f"💬 Уведомления в чате {'включены' if new_state else 'выключены'}.")
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка при изменении настроек чата.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('choose_chat_interval:'))
def handle_choose_chat_interval(call):
    try:
        _, chat_id_str = call.data.split(':', 1)
        chat_id = int(chat_id_str)
        uid = call.from_user.id
        kb = InlineKeyboardMarkup(row_width=4)
        for m in [5, 15, 30, 60]:
            kb.add(InlineKeyboardButton(f"{m} мин", callback_data=f"set_chat_interval:{chat_id}:{m}"))
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "Выберите интервал напоминаний (чат):", reply_markup=kb)
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_chat_interval:'))
def handle_set_chat_interval(call):
    try:
        _, chat_id_str, m_str = call.data.split(':', 2)
        chat_id = int(chat_id_str)
        m = int(m_str)
        cfg = database.get_chat_settings(chat_id)
        database.upsert_chat_settings(chat_id, cfg.get("notify_chat", 1), m, cfg.get("pinned_message_id"))
        bot.answer_callback_query(call.id, f"Интервал чата установлен: {m} мин.")
        bot.send_message(call.from_user.id, f"⏱ Интервал уведомлений в чате {chat_id} установлен: {m} мин.")
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка установки интервала.")


# -------------------------
# /stat — статистика и экстраполяция текущей цены
# -------------------------
@bot.message_handler(commands=['stat'])
def cmd_stat(message):
    try:
        user_id = message.from_user.id
        total_records = database.get_total_market_records()
        unique_resources = database.get_unique_resources_count()
        users_count = database.get_users_count()
        active_alerts = database.get_active_alerts_count()

        text_lines = [
            "<b>📊 Общая статистика бота</b>",
            f"• Всего записей рынка: {total_records}",
            f"• Уникальных ресурсов: {unique_resources}",
            f"• Пользователей в БД: {users_count}",
            f"• Активных оповещений: {active_alerts}",
            "─" * 24,
            "<b>Текущие (оценочные) цены по ресурсам:</b>"
        ]

        latest_list = database.get_all_latest()
        if not latest_list:
            text_lines.append("Нет данных по рынку.")
            bot.reply_to(message, "\n".join(text_lines), parse_mode="HTML")
            return

        for rec in latest_list:
            resource = rec['resource']
            pred_buy, pred_sell, trend, speed_adj, last_ts = market.compute_extrapolated_price(resource, user_id, lookback_minutes=60)
            qty_week = database.get_week_max_qty(resource)
            last_time_str = datetime.fromtimestamp(last_ts).strftime("%d.%m %H:%M") if last_ts else "—"
            speed_str = f"{speed_adj:+.4f}/мин" if speed_adj is not None else "неизвестно"
            trend_icon = "📈" if trend == "up" else "📉" if trend == "down" else "➡️"

            if pred_buy is None:
                pred_buy_text = "—"
            else:
                pred_buy_text = f"{pred_buy:.3f}"

            if pred_sell is None:
                pred_sell_text = "—"
            else:
                pred_sell_text = f"{pred_sell:.3f}"

            text_lines.append(
                f"{resource} — {trend_icon} {trend}\n"
                f"  ~Купить: {pred_buy_text}  /  ~Продать: {pred_sell_text}\n"
                f"  Последняя запись: {last_time_str} | Скорость: {speed_str}\n"
                f"  Макс. объём за неделю: {qty_week}"
            )

        bot.reply_to(message, "\n\n".join(text_lines), parse_mode="HTML")

    except Exception as e:
        logger.exception("Ошибка в /stat")
        bot.reply_to(message, "❌ Произошла ошибка при получении статистики.")


# -------------------------
# /history — либо история рынка, либо changelog бота
# -------------------------
@bot.message_handler(commands=['history'])
def cmd_history(message):
    try:
        parts = message.text.split()[1:] if len(message.text.split()) > 1 else []
        if not parts:
            bot.reply_to(message, "Использование:\n/history <ресурс> — показать историю по ресурсу\n/history bot — показать список обновлений бота")
            return

        arg = parts[0].strip().lower()
        if arg in ("bot", "updates", "changelog"):
            text = "<b>📜 Changelog бота:</b>\n\n"
            for ln in CHANGELOG:
                text += f"• {ln}\n"
            bot.reply_to(message, text, parse_mode="HTML")
            return

        resource = parts[0].capitalize()
        records = database.get_market_history(resource, limit=50)
        if not records:
            bot.reply_to(message, f"Нет данных по {resource}.")
            return

        text = f"📊 История по {resource} (последние {len(records)}):\n\n"
        for r in records:
            t = datetime.fromtimestamp(r['timestamp']).strftime("%d.%m %H:%M")
            # применяем корректировку для пользователя (тот, кто запрашивает)
            adj_buy, adj_sell = users.adjust_prices_for_user(message.from_user.id, r['buy'], r['sell'])
            text += f"{t} — Купить: {adj_buy:.3f} / Продать: {adj_sell:.3f} (qty: {r.get('quantity', 0)})\n"

        bot.reply_to(message, text)

    except Exception as e:
        logger.exception("Ошибка в /history")
        bot.reply_to(message, "❌ Произошла ошибка при получении истории.")


# -------------------------
# Парсинг форвардов
# -------------------------
@bot.message_handler(func=lambda msg: isinstance(getattr(msg, 'text', None), str) and "🎪" in msg.text)
def forward_market_handler(message):
    forward_from = getattr(message, "forward_from", None)
    forward_sender_name = getattr(message, "forward_sender_name", None)
    if not forward_from and not forward_sender_name:
        return

    try:
        market.handle_market_forward(bot, message)
    except Exception:
        logger.exception("Ошибка при обработке форварда")
        try:
            bot.reply_to(message, "❌ Ошибка при обработке форварда.")
        except Exception:
            pass


# -------------------------
# /timer
# -------------------------
@bot.message_handler(commands=['timer'])
def cmd_timer(message):
    alerts.cmd_timer_handler(bot, message)


# -------------------------
# Запуск
# -------------------------
if __name__ == '__main__':
    logger.info("Инициализация фоновых задач...")
    alerts.start_background_tasks(bot)
    logger.info("Запуск бота...")
    bot.polling(none_stop=True, interval=0, timeout=20)
