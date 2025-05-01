
# 🎲 Tournament Dice Game Telegram Bot

Telegram‑бот для проведения турниров с использованием кубика.

## Возможности
* Команды `/start`, `/game`, `/game_start`, `/dice`, `/help`. 
* Inline‑кнопки для подтверждения участия и готовности.
* Турнирная сетка (single‑elimination) генерируется автоматически.
* Таймаут 60 с для подтверждения готовности.
* Игры до двух побед («best‑of‑three»).
* Автоматическое объявление призовых мест.

## Запуск локально
```bash
git clone https://github.com/m0khm/dice-bot
cd dice-bot
cp .env.example .env            
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python -m bot.py
```

## Деплой на Aeza
1. Зарегистрируйтесь на <https://my.aeza.net>.
2. Необходимо приобрести сервер VDS.
3. Через cmd:
```bash
ssh user_name@ip_adress
your_password ( пароль невидим, надо просто нажать Enter)
cd dice
cd dice-bot 
source venv/bin/activate
python bot.py
```
5. В **.env** добавьте `TELEGRAM_BOT_TOKEN`.

Для постоянной работы на сервере:
```bash
---(единоразово)---
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker 
-------------------
Запуск: docker-compose up -d --build
Проверка статуса: docker ps
```

## Структура
```
tg_game_bot/
│
├── bot/
│   ├── __init__.py
│   ├── main.py          # точка входа
│   └── game.py          # логика турнира
│
├── requirements.txt
├── Dockerfile
└── README.md
```
### Документация
