# bot.py

import logging
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from game import TournamentManager

# ————————————————
# Настройка логирования
# ————————————————
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ————————————————
# Загрузка токена из .env
# ————————————————
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

# Текст для /start и /help
COMMANDS_TEXT = (
    "Привет! Я TournamentBot🎲\n\n"
    "/start — показать это сообщение\n"
    "/help — список команд\n"
    "/game — (админ) начать сбор участников\n"
    "/game_start — (админ) запустить турнир\n"
    "/dice — бросить кубик во время хода\n"
)

async def on_startup(app):
    # Регистрируем команды, чтобы Telegram-клиент показывал подсказки
    await app.bot.set_my_commands([
        BotCommand("start",       "Показать информацию о боте"),
        BotCommand("help",        "Список команд"),
        BotCommand("game",        "Начать сбор участников (админ)"),
        BotCommand("game_start",  "Запустить турнир (админ)"),
        BotCommand("dice",        "Бросить кубик во время хода"),
    ])

# ————————————————
# Хендлеры
# ————————————————
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — показывает список команд."""
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — тоже показывает список команд."""
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /game — админ открывает набор участников.
    Кнопка «Участвую» добавляет юзера в список.
    """
    chat = update.effective_chat
    user = update.effective_user
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    await chat.send_message("Набор на игру! Нажмите «Участвую»", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Кнопка «Участвую» — добавляем в список и редактируем сообщение.
    """
    q = update.callback_query
    await q.answer()
    if tournament.add_player(q.message.chat.id, q.from_user):
        lst = tournament.list_players(q.message.chat.id)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /game_start — админ формирует пары, показывает их все,
    а затем приглашает первую пару нажать «Готов?».
    """
    chat_id = update.effective_chat.id
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat_id)
    except ValueError as e:
        return await update.message.reply_text(str(e))

    # Сообщаем про «bye»
    for bye in byes:
        await context.bot.send_message(chat_id, f"🎉 {bye} сразу проходит в 2-й раунд (bye).")

    # Выводим все пары
    await context.bot.send_message(chat_id, text="Сетки турнира:\n" + pairs_list)
    # Запрашиваем готовность первой пары
    await context.bot.send_message(chat_id, text=first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «Готов?» — обрабатываем готовность."""
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dice — бросок кубика по очереди."""
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

# ————————————————
# Точка входа
# ————————————————
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(on_startup)  # регистрируем команды при старте
        .build()
    )
    global tournament
    tournament = TournamentManager(app.job_queue)

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_command))
    app.add_handler(CommandHandler("game",        game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start",  game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,  pattern="^ready_"))
    app.add_handler(CommandHandler("dice",        dice))

    app.run_polling()

if __name__ == "__main__":
    main()
