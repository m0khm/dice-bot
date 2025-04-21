
from __future__ import annotations
import random
from collections import deque
from dataclasses import dataclass, field
from telegram import User

JOIN_TIMEOUT = 60        # секунд на подтверждение готовности
BEST_OF = 3              # до двух побед

@dataclass
class PairState:
    players: tuple[int, int]         # user_id
    ready: set[int] = field(default_factory=set)
    scores: dict[int, int] = field(default_factory=lambda: {})
    current_turn: int | None = None

    def winner(self) -> int | None:
        for uid, w in self.scores.items():
            if w >= BEST_OF//2 + 1:
                return uid
        return None

@dataclass
class Tournament:
    players: list[int] = field(default_factory=list)
    bracket: deque[PairState] = field(default_factory=deque)
    winners: list[int] = field(default_factory=list)

    def make_bracket(self) -> None:
        random.shuffle(self.players)
        self.bracket.clear()
        for i in range(0, len(self.players), 2):
            pair = self.players[i:i+2]
            if len(pair) == 2:
                self.bracket.append(PairState(players=tuple(pair)))
            else:
                # нечётное число — игрок проходит дальше без боя
                self.winners.append(pair[0])

    def advance(self) -> None:
        # если текущий этап завершён
        if not self.bracket:
            if len(self.winners) <= 1:
                return
            self.players = self.winners.copy()
            self.winners.clear()
            self.make_bracket()
