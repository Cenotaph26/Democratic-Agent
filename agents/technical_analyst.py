"""
technical_analyst.py — Teknik Analiz Ajanı
RSI, MACD hesabı pandas ile yapılır (harici C kütüphanesi yok).
"""
import logging
from orchestration.parliament import AgentVote

logger = logging.getLogger(__name__)


class TechnicalAnalystAgent:
    def __init__(self, config):
        self.config = config

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        rsi   = market_data.get("rsi_14", 50.0)
        macd  = market_data.get("macd_signal", 0.0)
        atr   = market_data.get("atr", 0.0)
        score = 0.0
        reasons = []

        # RSI
        if rsi < 25:
            score += 50; reasons.append(f"Aşırı satım RSI={rsi:.0f}")
        elif rsi < 35:
            score += 30; reasons.append(f"Düşük RSI={rsi:.0f}")
        elif rsi < 45:
            score += 15; reasons.append(f"RSI={rsi:.0f} alım bölgesi")
        elif rsi > 75:
            score -= 40; reasons.append(f"Aşırı alım RSI={rsi:.0f}")
        elif rsi > 65:
            score -= 20; reasons.append(f"Yüksek RSI={rsi:.0f}")

        # MACD
        if macd > 0.5:
            score += 25; reasons.append("MACD pozitif kesişim")
        elif macd > 0.1:
            score += 10; reasons.append("MACD pozitif")
        elif macd < -0.5:
            score -= 25; reasons.append("MACD negatif kesişim")
        elif macd < -0.1:
            score -= 10; reasons.append("MACD negatif")

        score = max(-100, min(100, score))
        return AgentVote(
            agent_name="technical_analyst",
            signal=round(score, 1),
            confidence=0.80,
            reasoning="; ".join(reasons) or "Teknik sinyal nötr",
        )
