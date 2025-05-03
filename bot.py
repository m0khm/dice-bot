# main.py
import logging
import os

from dotenv import load_dotenv
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from game import TournamentManager

# ──────────── Логирование ────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────── Конфиг ────────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

ALLOWED_CHATS = {int(x) for x in os.getenv("ALLOWED_CHATS", "").split(",") if x.strip()}
OWNER_IDS    = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()]
DB_PATH      = os.getenv("DB_PATH", "scores.db")

MIN_EXCHANGE = 15  # порог в баллах

COMMANDS_TEXT = (
    "Привет! Я бот-рандомайзер. Доступные команды:\n"
    "/start        — 🤖 Список команд\n"
    "/game         — 👤 Начать сбор участников (админ)\n"
    "/game_start   — 🎮 Запустить турнир (админ)\n"
    "/dice         — 🎲 Бросок кубика во время хода\n"
    "/exchange     — 💱 Обменять очки (минимум 15)\n"
    "/points       — 📊 Мои очки\n"
    "/leaderboard  — 🏆 Рейтинг топ-10 (только ≥15)\n"
    "/id           — 🆔 Показать ID чата\n"
)

# ─── Helpers ────────────
async def remove_webhook(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted.")

async def set_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start",       "Список команд"),
        BotCommand("help",        "Помощь"),
        BotCommand("game",        "Начать сбор (админ)"),
        BotCommand("game_start",  "Запустить турнир (админ)"),
        BotCommand("dice",        "Бросок кубика"),
        BotCommand("exchange",    "Обменять очки"),
        BotCommand("points",      "Мои очки"),
        BotCommand("leaderboard", "Рейтинг топ-10"),
        BotCommand("id",          "Показать ID чата"),
    ])
    logger.info("Bot commands set.")

def is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHATS

# ─── Обработчики ────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await update.effective_chat.send_message(f"Chat ID: `{cid}`", parse_mode="Markdown")

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Участвую", callback_data="join_game")]])
    await chat.send_message("🔔 Нажмите «Участвую» для регистрации", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cid = q.message.chat.id
    if not is_allowed_chat(cid):
        return
    if tournament.add_player(cid, q.from_user):
        lst = tournament.list_players(cid)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может запустить турнир.")
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat.id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    for bye in byes:
        await context.bot.send_message(chat.id, f"🎉 {bye} получает bye")
    m = await context.bot.send_message(chat.id, "Сетки:\n" + pairs_list)
    await context.bot.pin_chat_message(chat.id, m.message_id)
    await context.bot.send_message(chat.id, first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

# — обмен очков в личке —
async def exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return
    uname = update.effective_user.username or update.effective_user.full_name
    pts = tournament.get_points(uname)
    if pts < MIN_EXCHANGE:
        return await update.message.reply_text(
            f"❌ У вас {pts} очков. Минимальный порог для обмена — {MIN_EXCHANGE}."
        )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Обменять", callback_data="exchange")]])
    await update.message.reply_text(f"У вас {pts} очков. Нажмите «Обменять»", reply_markup=kb)

async def exchange_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uname = q.from_user.username or q.from_user.full_name
    pts = tournament.get_points(uname)
    if pts < MIN_EXCHANGE:
        return await q.edit_message_text(
            f"❌ У вас {pts} очков. Минимальный порог для обмена — {MIN_EXCHANGE}."
        )
    taken = tournament.exchange_points(uname)
    for aid in OWNER_IDS:
        await context.bot.send_message(aid, f"💱 {uname} обменял {taken} очков")
    await q.edit_message_text(f"✅ Вы успешно обменяли {taken} очков")

# — мои очки (скрываем если < MIN_EXCHANGE) —
async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uname = update.effective_user.username or update.effective_user.full_name
    pts = tournament.get_points(uname)
    if pts < MIN_EXCHANGE:
        await update.effective_chat.send_message(
            f"ℹ️ У вас менее {MIN_EXCHANGE} очков."
        )
    else:
        await update.effective_chat.send_message(f"📊 {uname}, у вас {pts} очков.")
##ррр
async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = tournament.get_leaderboard(50)  # получаем побольше, отфильтруем до топ-10
    filtered = [(u, p) for u, p in top ]
    if not filtered:
        return await update.effective_chat.send_message("Рейтинг пуст.")
    text = "🏆 Топ-10:\n"
    for i, (user, pts) in enumerate(filtered[:10], start=1):
        text += f"{i}. {user}: {pts} очков\n"
    await update.effective_chat.send_message(text)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update:", exc_info=context.error)

def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(remove_webhook)
        .post_init(set_commands)
        .build()
    )
    app.add_error_handler(error_handler)

    global tournament
    tournament = TournamentManager(
        job_queue=app.job_queue,
        allowed_chats=ALLOWED_CHATS,
        db_path=DB_PATH,
        owner_ids=OWNER_IDS
    )

    # Регистрация хендлеров
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_command))
    app.add_handler(CommandHandler("id",          show_id))
    app.add_handler(CommandHandler("game",        game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start",  game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,    pattern="^ready_"))
    app.add_handler(CommandHandler("dice",        dice))
    app.add_handler(CommandHandler("exchange",    exchange))
    app.add_handler(CallbackQueryHandler(exchange_cb, pattern="^exchange$"))
    app.add_handler(CommandHandler("points",      points_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
