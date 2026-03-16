"""
technical_analyst.py — Teknik Analiz Ajanı
RSI, MACD, Bollinger Bands, hacim analizi
"""
import logging
from orchestration.parliament import AgentVote
logger = logging.getLogger(__name__)

class TechnicalAnalystAgent:
    def __init__(self, config): self.config = config

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        rsi = market_data.get("rsi_14", 50)
        macd = market_data.get("macd_signal", 0)
        reasons = []
        score = 0.0

        if rsi < 30:
            score += 40; reasons.append(f"Aşırı satım RSI={rsi:.0f}")
        elif rsi < 45:
            score += 20; reasons.append(f"Düşük RSI={rsi:.0f}")
        elif rsi > 70:
            score -= 35; reasons.append(f"Aşırı alım RSI={rsi:.0f}")
        elif rsi > 60:
            score -= 15; reasons.append(f"Yüksek RSI={rsi:.0f}")

        if macd > 0.5:
            score += 25; reasons.append("MACD pozitif kesişim")
        elif macd < -0.5:
            score -= 25; reasons.append("MACD negatif kesişim")

        score = max(-100, min(100, score))
        return AgentVote("technical_analyst", score, 0.80,
                         "; ".join(reasons) or "Teknik sinyal nötr")
