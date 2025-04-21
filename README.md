
# 🎲 Tournament Dice Game Telegram Bot

Telegram‑бот для проведения турниров с использованием кубика.

## Возможности
* Команды `/game`, `/game_start`, `/dice`.
* Inline‑кнопки для подтверждения участия и готовности.
* Турнирная сетка (single‑elimination) генерируется автоматически.
* Таймаут 60 с для подтверждения готовности.
* Игры до двух побед («best‑of‑three»).
* Автоматическое объявление призовых мест.

## Запуск локально
```bash
git clone https://github.com/yourname/tg_game_bot.git
cd tg_game_bot
cp .env.example .env            # вставьте токен
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

## Деплой на Railway
1. Зарегистрируйтесь на <https://railway.app>.
2. Создайте New Project → Deploy from GitHub, выберите репозиторий.
3. В **Variables** добавьте `TELEGRAM_BOT_TOKEN`.
4. Railway обнаружит `Dockerfile` и соберёт образ. Нажмите **Deploy**.

> **Heroku:** аналогично: `heroku create`, `heroku config:set TELEGRAM_BOT_TOKEN=...`, push в Git.

## Структура
```
tg_game_bot/
│
├── bot/
│   ├── __init__.py
│   ├── main.py          # точка входа
│   ├── game.py          # логика турнира
│   └── handlers.py      # обработчики Telegram
│
├── requirements.txt
├── Dockerfile
├── Procfile             # для Heroku
└── README.md
```
