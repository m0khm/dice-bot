# bot.py

import logging
import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "Привет! Я TournamentBot🎲\n\n"
        "Команды:\n"
        "/game — собрать участников (только админ)\n"
        "/game_start — запустить турнир (только админ)\n"
        "/dice — бросить кубик (во время хода)\n"
    )

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("Только админ может собрать участников.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    await chat.send_message("Набор на игру! Нажмите «Участвую»", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    added = tournament.add_player(q.message.chat.id, q.from_user)
    if added:
        lst = tournament.list_players(q.message.chat.id)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        byes, msg, kb = tournament.start_tournament(chat_id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    # сообщаем о bye
    for bye in byes:
        await context.bot.send_message(chat_id, f"🎉 {bye} получает bye и сразу в следующий раунд!")
    # первая пара
    await context.bot.send_message(chat_id, text=msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await tournament.roll_dice(update, context)
    await update.message.reply_text(text)

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    global tournament
    tournament = TournamentManager(app.job_queue)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("game", game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb, pattern="^ready_"))
    app.add_handler(CommandHandler("dice", dice))

    app.run_polling()

if __name__ == "__main__":
    main()
