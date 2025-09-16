import logging
import os
from datetime import datetime
import threading

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

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# Инициализация БД
database.init_db()

# Состояния для /settings
user_states = {}  # user_id -> state
user_data = {}    # temporary data during settings

STATE_SETTINGS_ANCHOR = "settings_anchor"
STATE_SETTINGS_TRADE_LEVEL = "settings_trade_level"


# -------------------------
# Команды
# -------------------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
                 "👋 Привет! Я бот для отслеживания рынка.\n\n"
                 "Команды:\n"
                 "/push — показать текущий рынок в чате (учитывает ваши бонусы)\n"
                 "/timer <ресурс> <цена> — установить таймер\n"
                 "/settings — настроить ваши бонусы (якорь и уровень торговли)\n"
                 "/help — помощь")


@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(message,
                 "Инструкция:\n"
                 "1) Пришлите форвард сообщения рынка (пересылка). Бот распарсит и сохранит 'сырые' цены.\n"
                 "2) /push — разместить текущие цены в чате (цены будут скорректированы под вас)\n"
                 "3) /timer <ресурс> <цель> — установить таймер оповещения.\n"
                 "4) /settings — настроить ваши бонусы.")


@bot.message_handler(commands=['settings'])
def cmd_settings(message):
    user_id = message.from_user.id
    user_states[user_id] = STATE_SETTINGS_ANCHOR
    user_data[user_id] = {}

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Да, есть Якорь", callback_data="anchor_yes"))
    keyboard.add(InlineKeyboardButton("❌ Нет", callback_data="anchor_no"))

    bot.reply_to(message, "⚓️ У вас есть Якорь (даёт +2%)?", reply_markup=keyboard)


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
    except ValueError:
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


@bot.message_handler(commands=['push'])
def cmd_push(message):
    """
    Публикация текущего рынка в чат (с учётом бонусов того, кто вызывает команду).
    """
    try:
        user_id = message.from_user.id
        resources = database.get_all_latest()
        if not resources:
            bot.reply_to(message, "⚠️ Рынок пока пуст. Пришлите форвард с рынка.")
            return

        lines = ["📊 <b>Текущий рынок:</b>"]
        for rec in resources:
            res = rec['resource']
            # Сырые цены из БД
            raw_buy = rec['buy']
            raw_sell = rec['sell']
            adj_buy, adj_sell = users.adjust_prices_for_user(user_id, raw_buy, raw_sell)
            # Форматируем
            lines.append(f"{res}: Купить {adj_buy:.2f} / Продать {adj_sell:.2f}")

        text = "\n".join(lines)
        sent = bot.send_message(message.chat.id, text, parse_mode="HTML")

        # Попытаться закрепить сообщение в группе (если группа)
        if message.chat.type in ['group', 'supergroup']:
            try:
                me = bot.get_me()
                chat_member = bot.get_chat_member(message.chat.id, me.id)
                if getattr(chat_member, "can_pin_messages", False) or getattr(chat_member, "status", "") == "creator":
                    bot.pin_chat_message(message.chat.id, sent.message_id, disable_notification=True)
                else:
                    bot.send_message(message.from_user.id, "⚠️ У бота нет прав закреплять сообщения в этой группе.")
            except Exception as e:
                logger.warning(f"Не удалось проверить/закрепить: {e}")

    except Exception as e:
        logger.exception("Ошибка в /push")
        bot.reply_to(message, "❌ Произошла ошибка при выполнении /push.")


# -------------------------
# Парсинг форвардов
# -------------------------
@bot.message_handler(func=lambda msg: isinstance(msg.text, str) and "🎪" in msg.text)
def forward_market_handler(message):
    """
    Обрабатываем пересланное сообщение с рынком (форвард).
    """
    # Проверим, что это пересылка
    if not getattr(message, "forward_from", None) and not getattr(message, "forward_sender_name", None):
        # Игнорируем обычные сообщения с эмодзи, ожидаем именно пересылку
        return

    try:
        market.handle_market_forward(bot, message)
    except Exception as e:
        logger.exception("Ошибка при обработке форварда")
        bot.reply_to(message, "❌ Ошибка при обработке форварда.")


# -------------------------
# Команда /timer
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
