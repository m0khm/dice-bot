# main.py
import logging
import os

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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
ALLOWED_CHATS = {int(x) for x in os.getenv("ALLOWED_CHATS","").split(",") if x}
DB_PATH       = os.getenv("DB_PATH", "scores.db")
OWNER_IDS     = [int(x) for x in os.getenv("OWNER_IDS","").split(",") if x]
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")
ALLOWED_CHATS = {
    int(x) for x in os.getenv("ALLOWED_CHATS", "").split(",") if x.strip()
}
OWNER_IDS = [
    int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()
]
DB_PATH = os.getenv("DB_PATH", "scores.db")

# Список команд для /start и /help
COMMANDS_TEXT = (
    "Привет! Я бот-рандомайзер. Доступные команды:\n"
    "/start       — 🤖 Список команд\n"
    "/game        — 👤 Начать сбор участников (админ)\n"
    "/game_start  — 🎮 Запустить турнир (админ)\n"
    "/dice        — 🎲 Бросок кубика во время хода\n"
    "/exchange    — 💱 Обменять очки (в личке)\n"
    "/id          — 🆔 Показать ID чата\n"
)

# ─── Убираем старый вебхук и регистрируем подсказки /
async def remove_webhook(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted.")

async def set_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start",     "🤖 Список команд"),
        BotCommand("help",      "🛟 Помощь"),
        BotCommand("game",      "👤 Начать сбор участников (админ)"),
        BotCommand("game_start","🎮 Запустить турнир (админ)"),
        BotCommand("dice",      "🎲 Бросок кубика во время хода"),
        BotCommand("exchange",  "💱 Обменять очки (в личке)"),
        BotCommand("id",        "🆔 Показать ID чата"),
    ])
    logger.info("Bot commands set.")

# ──────────── Хендлеры ────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

# ──────────── Хендлер для /help ────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Текст с HTML-разметкой
        caption = (
            "<b>Привет, я бот-рандомайзер</b>\n"
            "cозданный для розыгрышей и интерактивов!\n\n"
            "✨ Хочешь испытать удачу? Этот бот для тебя! 🎯\n"
            "С помощью наших универсальных инструментов ты сможешь организовать или поучаствовать в любом розыгрыше или интерактиве👾 \n\n"
            "<b>Пиши /start и добавляй бота в свои чаты</b> 💥\n\n"
            " Создано для <a href='https://t.me/NookiqqOnTon'>@NookiqqOnTon</a>\n"
            " При поддержке <a href='https://t.me/rapuzan'>@rapuzan</a>\n\n"
            " Сайт/Site: <a href='https://dicebotdoc.glitch.me'>Tournament Dice Bot (Documentation)</a>"
        )
        
        # Создание inline-кнопок
        keyboard = [
            [
                InlineKeyboardButton("👤 Dev.", url="https://t.me/rapuzan"),
                InlineKeyboardButton("⚡️ The Best Community", url="https://t.me/nookiqqonton"),
                InlineKeyboardButton("🌐 Website | Documentation ", url="https://dicebotdoc.glitch.me")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправка фото с подписью
        try:
            with open('noki_rapu.jpg', 'rb') as photo:
                await update.effective_chat.send_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        except FileNotFoundError:
            # Если фото не найдено, отправляется только текст с кнопками
            await update.effective_chat.send_message(
                text="📷 " + caption,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )

    except Exception as e:
        await update.effective_chat.send_message(f"⚠️ Ошибка: {str(e)}")

# ─────────────────────────────────────

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await update.effective_chat.send_message(f"Chat ID: `{cid}`", parse_mode="Markdown")

def is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHATS

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате недоступен. (/help)")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Участвую", callback_data="join_game")]])
    await chat.send_message("🔔 Нажмите «Участвую» для регистрации", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cid = q.message.chat.id
    if not is_allowed_chat(cid):
        return
    if tournament.add_player(cid, q.from_user):
        lst = tournament.list_players(cid)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате недоступен. (/help)")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может запустить турнир.")
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat.id)
    except ValueError as e:
        return await update.message.reply_text(str(e))

    for bye in byes:
        await context.bot.send_message(chat.id, f"🎉 {bye} получает bye")
    m = await context.bot.send_message(chat.id, "🕸️ Сетки турнира:\n" + pairs_list)
    await context.bot.pin_chat_message(chat.id, m.message_id)
    await context.bot.send_message(chat.id, first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.callback_query.message.chat.id
    if not is_allowed_chat(cid):
        return
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed_chat(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате недоступен. (/help)")
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

# — обмен очков в личке —
async def exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return
    user = update.effective_user
    uname = user.username or user.full_name
    pts = tournament.get_points(uname)
    if pts <= 0:
        return await update.message.reply_text("У вас нет очков для обмена :(")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Обменять", callback_data="exchange")]])
    await update.message.reply_text(f"У вас {pts} очков", reply_markup=kb)

async def exchange_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    uname = user.username or user.full_name
    pts = tournament.get_points(uname)
    if pts <= 0:
        return await q.edit_message_text("У вас нет очков :(")
    # снимаем очки
    taken = tournament.exchange_points(uname)
    # уведомляем админов
    text = f"💱 {uname} обменял {taken} очков"
    for aid in OWNER_IDS:
        await context.bot.send_message(aid, text)
    await q.edit_message_text(f"✅ Вы успешно обменяли {taken} очков")

# ──────────── main ────────────
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(remove_webhook)
        .post_init(set_commands)
        .build()
    )
    global tournament
    tournament = TournamentManager(
        job_queue=app.job_queue,
        allowed_chats=ALLOWED_CHATS,
        db_path=DB_PATH,
        owner_ids=OWNER_IDS
    )

    # Регистрация хендлеров
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("id",         show_id))
    app.add_handler(CommandHandler("game",       game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,   pattern="^ready_"))
    app.add_handler(CommandHandler("dice",       dice))
    app.add_handler(CommandHandler("exchange",   exchange))
    app.add_handler(CallbackQueryHandler(exchange_cb, pattern="^exchange$"))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
