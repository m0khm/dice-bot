import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

class TournamentManager:
    def __init__(self):
        # state per chat_id
        self.chats = {}

    def begin_signup(self, chat_id: int):
        self.chats[chat_id] = {
            "players": [],
            "stage": "signup",
        }

    def add_player(self, chat_id: int, user) -> bool:
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        uname = user.username or user.full_name
        if uname in data["players"]:
            return False
        data["players"].append(uname)
        return True

    def list_players(self, chat_id: int) -> str:
        return "\n".join(f"- {u}" for u in self.chats[chat_id]["players"])

    def start_tournament(self, chat_id: int):
        data = self.chats.get(chat_id)
        players = data and data.get("players", [])
        if not players or len(players) < 2:
            raise ValueError("❌ Недостаточно участников (минимум 2).")
        # инициализируем структуру
        data.update({
            "stage": "round",
            "round_number": 1,
            "round_players": players.copy(),
            "next_round_players": [],
            "current_pair_idx": 0,
            "scores": {},          # { pair_idx: {player: wins} }
            "turns": {},           # { pair_idx: current mover }
            "last_roll": {},       # { pair_idx: last roll or None }
            "semifinal_losers": [],# соберём здесь аутсайдеров полуфинала
        })
        # первый раунд
        return self._start_round(chat_id)

    def _start_round(self, chat_id: int):
        data = self.chats[chat_id]
        rp = data["round_players"]
        random.shuffle(rp)
        data["pairs"] = [(rp[i], rp[i+1]) for i in range(0, len(rp), 2)]
        data["current_pair_idx"] = 0
        data["next_round_players"] = []

        # инициализируем счёт и очередность
        for idx, (a, b) in enumerate(data["pairs"]):
            data["scores"][idx] = {a: 0, b: 0}
            data["turns"][idx] = a
            data["last_roll"][idx] = None

        # отправляем первую пару
        p0 = data["pairs"][0]
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data="ready_0")
        )
        return f"🏁 Раунд {data['round_number']} — пара 1:\n{p0[0]} vs {p0[1]}", kb

    def confirm_ready(self, chat_id: int, user, cb_data: str):
        """Вызыввается при нажатии 'Готов?'."""
        data = self.chats[chat_id]
        idx = int(cb_data.split("_", 1)[1])
        mover = data["turns"][idx]
        # сразу стартуем ход
        return f"🕹 {mover}, ваш ход! Используйте /dice"

    def roll_dice(self, chat_id: int, user):
        data = self.chats[chat_id]
        idx = data["current_pair_idx"]
        pair = data["pairs"][idx]
        uname = user.username or user.full_name

        # Проверяем очередь
        if data["turns"][idx] != uname:
            return f"Сейчас ходит {data['turns'][idx]}. Пожалуйста, дождитесь своей очереди."

        # Бросок
        roll = random.randint(1, 6)
        prev = data["last_roll"][idx]
        data["last_roll"][idx] = roll

        # если это первый бросок — ждем второго
        if prev is None:
            # меняем очередь
            other = pair[0] if uname == pair[1] else pair[1]
            data["turns"][idx] = other
            return f"{uname} 🎲 {roll}\nТеперь {other} /dice"

        # это второй бросок — определяем победителя
        # сравнение
        if roll > prev:
            winner = uname; loser = pair[0] if uname == pair[1] else pair[1]
        elif roll < prev:
            loser = uname; winner = pair[0] if uname == pair[1] else pair[1]
        else:
            # ничья — обнуляем и начинаем заново
            data["last_roll"][idx] = None
            return f"Ничья ({prev} vs {roll}), бросайте заново — {uname} /dice"

        # подсчет побед
        data["scores"][idx][winner] += 1
        w = data["scores"][idx][winner]
        data["last_roll"][idx] = None  # сброс для следующего раунда пары

        # проверка конца пары
        if w >= 2:
            # зафиксируем полуфинальных аутсайдеров
            if len(data["round_players"]) == 4:
                data["semifinal_losers"].append(loser)

            text = f"🏆 Пара {idx+1}: {winner} выиграл ({w}–{data['scores'][idx][loser]})!"
            # заносим победителя в следующий раунд
            data["next_round_players"].append(winner)
            data["current_pair_idx"] += 1

            # следующая пара или конец раунда
            if data["current_pair_idx"] < len(data["pairs"]):
                # новая пара
                np = data["pairs"][data["current_pair_idx"]]
                kb = InlineKeyboardMarkup.from_button(
                    InlineKeyboardButton(
                        "Готов?", callback_data=f"ready_{data['current_pair_idx']}"
                    )
                )
                return f"{text}\n\nПара {data['current_pair_idx']+1}:\n{np[0]} vs {np[1]}", kb

            # все пары раунда отыграны
            # если в next_round_players >1 — стартуем новый раунд
            if len(data["next_round_players"]) > 1:
                data["round_number"] += 1
                data["round_players"] = data["next_round_players"].copy()
                return self._start_round(chat_id)
            # иначе — турнир окончен
            champ = data["next_round_players"][0]
            # зафиксируем вице-чемпиона из последнего loser
            runner_up = loser
            thirds = data["semifinal_losers"]
            return (
                f"🥇 Чемпион: {champ}\n"
                f"🥈 Финалист: {runner_up}\n"
                f"🥉 Места: {thirds[0]}, {thirds[1]}\n"
                "Поздравляем всех участников!"
            )

        # если еще не 2 побед, продолжаем текущую пару
        other = pair[0] if winner == pair[1] else pair[1]
        # сохраняем очередь — следующий ход делает другой
        data["turns"][idx] = other
        return (
            f"{winner} выиграл этот раунд ({roll} vs {prev}).\n"
            f"Счет: {data['scores'][idx][pair[0]]}–{data['scores'][idx][pair[1]]}.\n"
            f"{other}, ваш ход — /dice"
        )
