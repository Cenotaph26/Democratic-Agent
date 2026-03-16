"""
project_evaluator.py — Proje Kalite Değerlendirme Ajanı

Whitepaper, tokenomics, takım, iş ortaklıkları, geliştirici aktivitesi
temelinde her projeye 0-100 arası puan verir.
"""

import logging
from orchestration.parliament import AgentVote

logger = logging.getLogger(__name__)


class ProjectEvaluatorAgent:
    """
    Temel analiz uzmanı. Uzun vadeli kalite sinyali üretir.

    Puan bileşenleri:
    ─────────────────
    Whitepaper kalitesi    : 0-20 puan
    Kurumsal ortaklıklar   : 0-20 puan
    GitHub aktivitesi      : 0-15 puan
    Token dağılımı         : 0-15 puan
    Mainnet / kullanım     : 0-15 puan
    Likidite yeterliliği   : 0-15 puan
    """

    def __init__(self, config: dict):
        self.config = config

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        """
        Projeyi değerlendir ve sinyal üret.
        """
        project_score = market_data.get("project_score", 50.0)
        reasons = []

        # Proje skoru → ticaret sinyaline dönüştür
        if project_score >= 80:
            signal = 60.0
            reasons.append(f"Yüksek kaliteli proje ({project_score:.0f}/100)")
        elif project_score >= 65:
            signal = 30.0
            reasons.append(f"Orta-iyi proje ({project_score:.0f}/100)")
        elif project_score >= 50:
            signal = 0.0
            reasons.append(f"Orta proje ({project_score:.0f}/100)")
        else:
            signal = -50.0
            reasons.append(f"Düşük kaliteli proje ({project_score:.0f}/100) — İşlem önerilmez")

        # Ortaklık bonusu
        partnerships = market_data.get("institutional_partnerships", 0)
        if partnerships > 3:
            signal = min(signal + 20, 100)
            reasons.append(f"{partnerships} kurumsal ortaklık")

        # Likidite puanı
        volume = market_data.get("volume_24h_usd", 0)
        if volume > 100_000_000:
            signal = min(signal + 10, 100)
            reasons.append(f"Güçlü likidite: ${volume/1e6:.0f}M")
        elif volume < 10_000_000:
            signal -= 15
            reasons.append(f"Düşük likidite: ${volume/1e6:.1f}M")

        signal = max(-100, min(100, signal))
        confidence = 0.75 if project_score != 50.0 else 0.4

        return AgentVote(
            agent_name="project_evaluator",
            signal=signal,
            confidence=confidence,
            reasoning="; ".join(reasons),
        )
