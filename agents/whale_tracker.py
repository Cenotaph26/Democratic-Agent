"""
whale_tracker.py — Balina & Kurumsal Yatırımcı Takip Ajanı

İzlediği sinyaller:
- Büyük on-chain transferler (borsaya giriş/çıkış)
- ETF alım verileri (BTC, ETH)
- Büyük OTC masaları haberleri
- Açık faiz (open interest) ani değişimleri
- Fonlama oranı (funding rate) anomalileri
"""

import logging
from orchestration.parliament import AgentVote

logger = logging.getLogger(__name__)


class WhaleTrackerAgent:
    """
    Balina ve kurumsal hareket takipçisi.

    Pozitif sinyal: Büyük walletlar birikiyor, borsadan çıkış var,
                    kurumsal alım haberleri var.
    Negatif sinyal: Borsaya büyük transfer, kurumsal satış,
                    yüksek açık faiz + negatif fonlama.
    """

    def __init__(self, config: dict):
        self.config = config
        self.min_transfer_usd = config.get("whale_min_transfer_usd", 500_000)

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        """
        Balina sinyali üret.

        market_data beklenen alanlar:
            exchange_inflow_24h   : Borsaya giren coin (negatif)
            exchange_outflow_24h  : Borsadan çıkan coin (pozitif)
            large_transfers_count : Büyük transfer sayısı
            funding_rate          : Fonlama oranı
            open_interest_change  : OI değişimi (%)
            institutional_news    : Kurumsal haber skoru (-1 ile +1)
        """
        score = 0.0
        reasons = []

        # — Borsa akışları —
        inflow = market_data.get("exchange_inflow_24h", 0)
        outflow = market_data.get("exchange_outflow_24h", 0)
        net_flow = outflow - inflow  # Pozitif = borsadan çıkış = birikme

        if net_flow > 0:
            score += min(net_flow / 1_000_000 * 15, 30)
            reasons.append(f"Borsadan çıkış: ${net_flow/1e6:.1f}M")
        elif net_flow < 0:
            score += max(net_flow / 1_000_000 * 15, -30)
            reasons.append(f"Borsaya giriş: ${abs(net_flow)/1e6:.1f}M (satış baskısı)")

        # — Büyük transferler —
        large_txs = market_data.get("large_transfers_count", 0)
        if large_txs > 5:
            score += 10
            reasons.append(f"{large_txs} büyük transfer")

        # — Fonlama oranı —
        funding = market_data.get("funding_rate", 0.0)
        if abs(funding) > 0.001:
            # Çok yüksek pozitif funding = kalabalık long = tehlike
            if funding > 0.003:
                score -= 20
                reasons.append(f"Aşırı yüksek funding: {funding:.4%}")
            elif funding < -0.001:
                score += 15  # Negatif funding = short baskısı = long fırsat
                reasons.append(f"Negatif funding (long fırsatı): {funding:.4%}")

        # — Açık faiz değişimi —
        oi_change = market_data.get("open_interest_change", 0)
        if oi_change > 20 and net_flow > 0:
            score += 15
            reasons.append(f"OI +{oi_change:.0f}% + birikim")
        elif oi_change > 20 and net_flow < 0:
            score -= 10
            reasons.append(f"OI +{oi_change:.0f}% ama satış var")

        # — Kurumsal haberler —
        inst_score = market_data.get("institutional_news", 0.0)
        score += inst_score * 25
        if abs(inst_score) > 0.3:
            direction = "olumlu" if inst_score > 0 else "olumsuz"
            reasons.append(f"Kurumsal haber {direction}: {inst_score:+.1f}")

        # Normalize [-100, +100]
        score = max(-100, min(100, score))
        confidence = min(0.4 + len(reasons) * 0.12, 0.95)

        logger.debug(f"[WhaleTracker] {symbol}: {score:.1f} | {' | '.join(reasons)}")

        return AgentVote(
            agent_name="whale_tracker",
            signal=score,
            confidence=confidence,
            reasoning="; ".join(reasons) if reasons else "Önemli balina hareketi yok",
        )
