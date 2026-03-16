"""
election.py — Demokratik Lider Seçim Sistemi

Her 10-15 işlemde bir seçim yapılır.
Ajanlar hem performans puanlarına göre değerlendirilir
hem de birbirini oylar (kör oy).
"""

import logging
import random
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ElectionResult:
    winner: str
    winner_score: float
    loser: str
    loser_score: float
    vote_breakdown: dict[str, dict]  # Her ajanın aldığı oylar


class ElectionSystem:
    """
    Seçim mekanizması:

    1. PERFORMANS PUANI (%60 ağırlık)
       Son N işlemdeki katkı skoru.
       Kazanan işlemde +oy → puan artar
       Kaybeden işlemde +oy → puan düşer

    2. AKRAN OYU (%40 ağırlık)
       Her ajan diğer 4 ajanı puanlar (1-10 arası).
       Kör oy: kim kime oy verdiği gizli.
       (Gerçek hayatta: her ajan kendi modelini çalıştırır,
        diğerlerinin reasoning'ini görür, onları değerlendirir.)

    Final puan = 0.6 × performans + 0.4 × akran ortalaması
    """

    AGENT_NAMES = [
        "whale_tracker",
        "technical_analyst",
        "sentiment_analyst",
        "project_evaluator",
        "risk_manager",
    ]

    def __init__(self, scoreboard):
        self.scoreboard = scoreboard
        self.election_count = 0

    async def run_election(self, agents: dict) -> tuple[str, Optional[str]]:
        """
        Seçim başlat. Kazanan ve en kötü performanslı ajanı döndür.
        """
        self.election_count += 1
        logger.info(f"[Election] #{self.election_count}. Demokratik seçim başladı 🗳️")

        # Performans puanları
        perf_scores = self.scoreboard.get_agent_scores()

        # Akran oylaması
        peer_scores = await self._collect_peer_votes(agents, perf_scores)

        # Final puan hesapla
        final_scores = {}
        for name in self.AGENT_NAMES:
            perf = perf_scores.get(name, 50.0)    # 0-100 arası
            peer = peer_scores.get(name, 5.0)     # 1-10 arası → *10 ile normalize
            final_scores[name] = 0.6 * perf + 0.4 * (peer * 10)

        # Sıralama
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        winner = ranked[0][0]
        loser = ranked[-1][0]

        logger.info("[Election] Sonuçlar:")
        for name, score in ranked:
            marker = "🏆" if name == winner else ("⚠️" if name == loser else "  ")
            logger.info(f"  {marker} {name}: {score:.1f} puan")

        return winner, loser

    async def _collect_peer_votes(self, agents: dict, perf_scores: dict) -> dict[str, float]:
        """
        Her ajan diğerlerini değerlendirir.
        Basitleştirilmiş: performans + küçük rastgele gürültü.
        Gerçek implementasyonda: her ajan reasoning chain'i okuyup değerlendirir.
        """
        vote_totals: dict[str, list[float]] = {n: [] for n in self.AGENT_NAMES}

        for voter in self.AGENT_NAMES:
            for candidate in self.AGENT_NAMES:
                if voter == candidate:
                    continue  # Kendine oy vermez
                base = perf_scores.get(candidate, 50.0) / 10  # 0-10 arası
                noise = random.gauss(0, 0.5)
                vote = max(1.0, min(10.0, base + noise))
                vote_totals[candidate].append(vote)

        return {
            name: sum(votes) / len(votes) if votes else 5.0
            for name, votes in vote_totals.items()
        }
