# game.py
import sqlite3
import random
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CallbackContext, ContextTypes

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
            "next_round": [], "pairs": [],
            "current_pair_idx": 0, "round_pairs_count": 0,
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
            "semifinal_losers": [], "pair_timers": {},
            "finished_pairs": set(),
        }

    def add_player(self, chat_id: int, user) -> bool:
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        name = user.username or user.full_name
        if name in data["players"]:
            return False
        data["players"].append(name)
        return True

    def list_players(self, chat_id: int) -> str:
        data = self.chats.get(chat_id, {})
        return ", ".join(self._format_username(n) for n in data.get("players", []))

    # ───────── start_tournament, confirm_ready, _ready_timeout, _pair_timeout,
    # _reset_pair_timer, _proceed_next, roll_dice ─────────
    # (всё как ранее, с сохранением начисления очков в _proceed_next)
    # Для краткости здесь опущены — см. предыдущий полный пример.
