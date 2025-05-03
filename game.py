# game.py
import sqlite3
import random
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CallbackContext, ContextTypes

logger = logging.getLogger(__name__)

class TournamentManager:
    FIRST_POINTS = 50
    SECOND_POINTS = 25
    THIRD_POINTS = 15

    def __init__(
        self,
        job_queue,
        allowed_chats=None,
        db_path: str = "scores.db",
        owner_ids=None
    ):
        self.job_queue = job_queue
        self.allowed_chats = set(allowed_chats or [])
        self.owner_ids    = list(owner_ids or [])
        self.chats = {}

        # Инициализируем SQLite
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                username TEXT PRIMARY KEY,
                points   INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def _format_username(self, name: str) -> str:
        if not name:
            return ""
        return name if name.startswith("@") else f"@{name}"

    # ─── работа с очками ─────────
    def _add_points(self, username: str, pts: int):
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            new = row[0] + pts
            cur.execute("UPDATE scores SET points=? WHERE username=?", (new, username))
        else:
            new = pts
            cur.execute("INSERT INTO scores(username,points) VALUES(?,?)", (username, pts))
        self.conn.commit()
        logger.info(f"Добавлено {pts} очков игроку {username}. Всего: {new}")

    def get_points(self, username: str) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        return row[0] if row else 0

    def exchange_points(self, username: str) -> int:
        pts = self.get_points(username)
        if pts > 0:
            cur = self.conn.cursor()
            cur.execute("UPDATE scores SET points=0 WHERE username=?", (username,))
            self.conn.commit()
        return pts

    # ───────── signup ─────────
    def begin_signup(self, chat_id: int):
        self.chats[chat_id] = {
            "players": [], "stage": "signup",
            # ... остальное без изменений
            "next_round": [], "pairs": [], "current_pair_idx": 0,
            "round_pairs_count": 0, "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {}, "round_rolls": {},
            "turn_order": {}, "semifinal_losers": [], "pair_timers": {},
            "finished_pairs": set(),
        }

    # ... все остальные методы без изменений до конца турнира ...

    async def _proceed_next(self, chat_id: int, bot):
        data = self.chats[chat_id]
        # ... логика перехода по раундам ...
        # Когда остаётся один победитель:
        champ = data["next_round"][0]
        w = data["round_wins"].get(0, {})
        runner = None
        if w:
            p, q = data["pairs"][0]
            runner = p if w.get(p, 0) < w.get(q, 0) else q
        thirds = data["semifinal_losers"]

        # Начисляем очки
        self._add_points(champ, self.FIRST_POINTS)
        if runner:
            self._add_points(runner, self.SECOND_POINTS)
        if len(thirds) >= 2:
            # берем двух проигравших в полуфинале
            self._add_points(thirds[0], self.THIRD_POINTS)
            self._add_points(thirds[1], self.THIRD_POINTS)

        # Собираем текст отчёта
        text = f"🏆 Победитель: {self._format_username(champ)} (+{self.FIRST_POINTS} очков)\n"
        if runner:
            text += f"🥈 Второе место: {self._format_username(runner)} (+{self.SECOND_POINTS} очков)\n"
        if len(thirds) >= 2:
            text += (
                f"🥉 Третьи места: {self._format_username(thirds[0])}, "
                f"{self._format_username(thirds[1])} (+{self.THIRD_POINTS} очков каждому)\n"
            )
        await bot.send_message(chat_id, text)
        data["stage"] = "finished"

    # ... остальные методы без изменений ...
