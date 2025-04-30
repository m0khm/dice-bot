# bot.py

import logging
import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from game import TournamentManager

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

COMMANDS_TEXT = (
    "Привет! Я TournamentBot🎲\n\n"
    "/start — показать информацию о боте\n"
    "/help — список команд\n"
    "/game — (админ) начать сбор участников\n"
    "/game_start — (админ) запустить турнир\n"
    "/dice — бросить кубик во время хода\n"
)

async def on_startup(app):
    # Регистрируем команды, чтобы Telegram-клиент показывал их при вводе `/`
    await app.bot.set_my_commands([
        BotCommand("start",      "Показать информацию о боте"),
        BotCommand("help",       "Список команд"),
        BotCommand("game",       "Начать сбор участников (админ)"),
        BotCommand("game_start","Запустить турнир (админ)"),
        BotCommand("dice",       "Бросить кубик во время хода"),
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    await chat.send_message("Набор на игру! Нажмите «Участвую»", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if tournament.add_player(q.message.chat.id, q.from_user):
        lst = tournament.list_players(q.message.chat.id)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat_id)
    except ValueError as e:
        return await update.message.reply_text(str(e))

    # 1) Bye
    for bye in byes:
        await context.bot.send_message(chat_id, f"🎉 {bye} сразу проходит в 2-й раунд (bye).")

    # 2) Публикуем и закрепляем сетку
    m = await context.bot.send_message(chat_id, text="Сетки турнира:\n" + pairs_list)
    await context.bot.pin_chat_message(chat_id=chat_id, message_id=m.message_id)

    # 3) Приглашаем первую пару
    await context.bot.send_message(chat_id, text=first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(on_startup)
        .build()
    )
    global tournament
    tournament = TournamentManager(app.job_queue)

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("game",       game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,  pattern="^ready_"))
    app.add_handler(CommandHandler("dice",       dice))

    app.run_polling()

if __name__ == "__main__":
    main()
