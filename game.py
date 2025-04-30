import asyncio
import random
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

class TournamentManager:
    def __init__(self):
        # { chat_id: {... state ...} }
        self.chats = {}

    def begin_signup(self, chat_id):
        self.chats[chat_id] = {
            "players": [],
            "stage": "signup",
            "pairs": [],
            "results": {},
        }

    def add_player(self, chat_id, user):
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        uname = user.username or user.full_name
        if uname in data["players"]:
            return False
        data["players"].append(uname)
        return True

    def list_players(self, chat_id):
        return ", ".join(self.chats[chat_id]["players"])

    def start_tournament(self, chat_id):
        data = self.chats.get(chat_id)
        players = data and data["players"]
        if not players or len(players) < 2:
            raise ValueError("Недостаточно участников (< 2).")
        random.shuffle(players)
        # пары первого раунда
        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data["pairs"] = pairs
        data["stage"] = "round"
        data["results"] = {p: [] for p in pairs}
        # первая пара
        p0 = pairs[0]
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data=f"ready_{0}")
        )
        return (f"Раунд 1 — пара 1: {p0[0]} vs {p0[1]}", kb)

    async def confirm_ready(self, chat_id, user, bot):
        data = self.chats[chat_id]
        idx = int(user.id) # храним по индексу пары, имитируем
        # Упрощение: считаем, что оба готовы сразу
        # На практике надо хранить таймер, запускать wait_for
        # Для демонстрации просто стартуем игру
        pair = data["pairs"][0]
        return f"{pair[0]} ходит первым! Используйте /dice"

    def roll_dice(self, chat_id, user):
        rnd = random.randint(1, 6)
        # Вставить логику ведения счёта, смены хода, определения побед
        return f"{user.username or user.full_name} бросил 🎲 и выпало {rnd}"