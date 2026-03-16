"""risk_manager.py — Risk Yönetim Ajanı (Veto Yetkili)"""
import logging
from orchestration.parliament import AgentVote
logger = logging.getLogger(__name__)

class RiskManagerAgent:
    VETO_FUNDING_RATE = 0.005
    VETO_DRAWDOWN_PCT = 8.0

    def __init__(self, config):
        self.config = config

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        funding = market_data.get("funding_rate", 0)
        reasons = []

        if abs(funding) > self.VETO_FUNDING_RATE:
            return AgentVote(
                "risk_manager", -100, 1.0,
                f"Aşırı funding {funding:.4%} → VETO", veto=True
            )

        score = 0.0
        if abs(funding) < 0.001:
            score += 20; reasons.append("Sağlıklı funding")
        elif funding < 0:
            score += 10; reasons.append("Negatif funding (long lehine)")

        oi_change = market_data.get("open_interest_change", 0)
        if abs(oi_change) > 40:
            score -= 20; reasons.append(f"Yüksek OI değişimi {oi_change:+.0f}%")

        score = max(-100, min(100, score))
        return AgentVote(
            "risk_manager", score, 0.85,
            "; ".join(reasons) or "Risk normal"
        )
