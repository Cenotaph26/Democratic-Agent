"""
scoreboard.py — Ajan Performans Skorbordı

Her işlemin ardından hangi ajanın ne kadar katkı yaptığını izler.
Seçim sistemine girdi sağlar.
"""

from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

AGENT_NAMES = [
    "whale_tracker",
    "technical_analyst",
    "sentiment_analyst",
    "project_evaluator",
    "risk_manager",
]


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    action: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    votes: dict[str, float]     # agent_name → signal
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def won(self) -> bool:
        return self.pnl_pct > 0


class Scoreboard:
    """
    Her ajanın son 50 işlemdeki katkısını izler.
    Katkı = İşlem kazandıysa olumlu oy verdi mi?
    """

    WINDOW = 50   # Kaç işlemi geriye bakacağız

    def __init__(self):
        self.trades: list[TradeRecord] = []
        self.agent_cumulative: dict[str, float] = defaultdict(float)

    def record_decision(self, decision) -> None:
        """Yeni işlem kararını kaydet (henüz sonuç yok)."""
        # Sonuç geldiğinde record_outcome çağrılacak
        pass

    def record_outcome(self, trade_id: str, symbol: str, action: str,
                       entry: float, exit_: float, votes: dict[str, float]) -> None:
        """İşlem kapandığında sonucu kaydet ve puanları güncelle."""
        pnl_pct = (exit_ - entry) / entry * 100 if action == "LONG" else (entry - exit_) / entry * 100

        record = TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            action=action,
            entry_price=entry,
            exit_price=exit_,
            pnl_pct=pnl_pct,
            votes=votes,
        )
        self.trades.append(record)

        # Katkı puanlarını güncelle
        for agent, signal in votes.items():
            # Kazandık ve olumlu oy verdiyse → puan
            # Kaybettik ve olumlu oy verdiyse → ceza
            if record.won:
                contribution = signal / 100 * abs(pnl_pct)
            else:
                contribution = -(signal / 100) * abs(pnl_pct)
            self.agent_cumulative[agent] += contribution

        logger.info(
            f"[Scoreboard] {symbol} {action}: PnL={pnl_pct:+.2f}% | "
            + " | ".join(f"{a}: {s:+.0f}" for a, s in votes.items())
        )

    def get_agent_scores(self) -> dict[str, float]:
        """
        Son WINDOW işlemdeki kümülatif katkıyı 0-100 arasına normalize et.
        """
        recent = self.trades[-self.WINDOW:]

        raw: dict[str, float] = defaultdict(float)
        for record in recent:
            for agent, signal in record.votes.items():
                if record.won:
                    raw[agent] += signal / 100 * abs(record.pnl_pct)
                else:
                    raw[agent] -= (signal / 100) * abs(record.pnl_pct)

        # 0-100 arası normalize et
        if not raw:
            return {a: 50.0 for a in AGENT_NAMES}

        min_val = min(raw.values())
        max_val = max(raw.values())
        span = max_val - min_val if max_val != min_val else 1.0

        normalized = {}
        for agent in AGENT_NAMES:
            val = raw.get(agent, 0.0)
            normalized[agent] = ((val - min_val) / span) * 100

        return normalized

    def print_leaderboard(self) -> None:
        scores = self.get_agent_scores()
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print("\n═══ AJAN SKORBORD ═══")
        for i, (name, score) in enumerate(ranked, 1):
            bar = "█" * int(score / 5)
            print(f"{i}. {name:<22} {bar:<20} {score:.1f}")
        print(f"Toplam işlem: {len(self.trades)}")
        win_rate = sum(1 for t in self.trades if t.won) / len(self.trades) * 100 if self.trades else 0
        print(f"Kazanma oranı: {win_rate:.1f}%\n")
