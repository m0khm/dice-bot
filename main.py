
import logging, os, asyncio
from dotenv import load_dotenv
from telegram.ext import Application
from .handlers import setup

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    app = Application.builder().token(token).build()
    setup(app)
    app.run_polling()

if __name__ == "__main__":
    main()
