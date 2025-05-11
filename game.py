# game.py  05-2025
import sqlite3
import random
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ContextTypes

logger = logging.getLogger(__name__)

class TournamentManager:
    FIRST_POINTS  = 0
    SECOND_POINTS = 25
    THIRD_POINTS  = 15
    READY_TIMEOUT = 60  # секунды на готовность
    ROLL_TIMEOUT  = 60  # секунды на ход

    def __init__(self, job_queue, allowed_chats=None, db_path="scores.db", owner_ids=None):
        self.job_queue     = job_queue
        self.allowed_chats = set(allowed_chats or [])
        self.owner_ids     = list(owner_ids or [])
        self.conn          = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self.chats         = {}

    # ─── ВСПОМОГАТЕЛЬНОЕ ───────────────────────────────────
    @staticmethod
    def _is_power_of_two(n: int) -> bool:
        """True, если n — степень двойки (2, 4, 8, …)."""
        return n >= 2 and (n & (n - 1) == 0)

    # ─── БД ────────────────────────────────────────────────
    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores(
                username TEXT PRIMARY KEY,
                points   INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def _format_username(self, name: str) -> str:
        return name if name.startswith("@") else f"@{name}"

    # ─── Работа с очками ───────────────────────────────────
    def _add_points(self, username: str, pts: int):
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        new = (row[0] + pts) if row else pts
        if row:
            cur.execute("UPDATE scores SET points=? WHERE username=?", (new, username))
        else:
            cur.execute("INSERT INTO scores(username,points) VALUES(?,?)", (username, pts))
        self.conn.commit()
        logger.info(f"Добавлено {pts} очков игроку @{username}. Всего: {new}")

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
        
    def exchange_points_amount(self, username: str, amount: int) -> int:
        pts = self.get_points(username)
        if pts < amount:
            return 0
        new = pts - amount
        cur = self.conn.cursor()
        cur.execute("UPDATE scores SET points=? WHERE username=?", (new, username))
        self.conn.commit()
        return amount  

    def get_leaderboard(self, limit: int = 10):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT username,points FROM scores ORDER BY points DESC, username LIMIT ?",
            (limit,)
        )
        return cur.fetchall()

    # ─── Signup ────────────────────────────────────────────
    def begin_signup(self, chat_id: int):
        # --- защита от повторного /game_start --------------------------- ◀ NEW
        current = self.chats.get(chat_id)
        if current and current.get("stage") in ("signup", "round"):
            raise ValueError("Турнир уже запущен или идёт сбор игроков.")
        # -----------------------------------------------------------------
        self.chats[chat_id] = {
            "players": [], "stage": "signup",
            "next_round": [], "pairs": [], "current_pair_idx": 0,
            "round_pairs_count": 0, "ready": {}, "first_ready_time": {},
            "pair_timers": {}, "roll_timers": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {}, "finished_pairs": set(),
            "semifinal_losers": [],
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

    # ─── Старт турнира ─────────────────────────────────────
    def start_tournament(self, chat_id: int):
        data = self.chats.get(chat_id)
        players = data and data["players"][:]

        if not players or len(players) < 2:
            raise ValueError("Нужно как минимум 2 игрока.")
        if not self._is_power_of_two(len(players)):
            raise ValueError("Количество игроков должно быть степенью двойки (2, 4, 8, 16 …).")

        random.shuffle(players)
        data["next_round"].clear()

        # (bye-логика осталась на случай дальнейших модификаций)
        byes = []
        if len(players) % 2 == 1:     # фактически не выполнится
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        pairs = [(players[i], players[i + 1]) for i in range(0, len(players), 2)]
        data.update({
            "stage": "round", "pairs": pairs,
            "current_pair_idx": 0,
            "round_pairs_count": len(pairs),
            "ready": {}, "first_ready_time": {},
            "pair_timers": {}, "roll_timers": {},
            "round_wins": {}, "round_rolls": {},
            "turn_order": {}, "finished_pairs": set(),
            "semifinal_losers": [],
        })

        # запускаем таймер готовности для первой пары
        if self.job_queue:
            job = self.job_queue.run_once(
                self._pair_timeout,
                when=self.READY_TIMEOUT,
                chat_id=chat_id,
                data={"idx": 0},
                name=f"pair_timeout_{chat_id}_0"
            )
            data["pair_timers"][0] = job

        pairs_list = "\n".join(
            f"Пара {i+1}: {self._format_username(a)} vs {self._format_username(b)}"
            for i, (a, b) in enumerate(pairs)
        )
        first_pair = pairs[0]
        first_msg = (
            f"Пара 1: {self._format_username(first_pair[0])} vs "
            f"{self._format_username(first_pair[1])}\nНажмите «Готов?»"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data="ready_0")]])

        return byes, pairs_list, first_msg, kb

    # ─── Подтверждаем готовность, таймауты, ход кубика, финал ─────────────
    # (без изменений; см. предыдущую версию)

    # … остальной код остаётся прежним …
