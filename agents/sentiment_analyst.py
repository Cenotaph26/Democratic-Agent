"""
sentiment_analyst.py — Gerçek Haber & Duygu Analizi Ajanı

CryptoPanic + Fear & Greed Index verilerini kullanır.
news_feed.py tarafından sağlanan gerçek API verisiyle çalışır.
"""

import logging
from orchestration.parliament import AgentVote

logger = logging.getLogger(__name__)


class SentimentAnalystAgent:
    """
    Haber ve piyasa duygusu uzmanı.

    Sinyal kaynakları (gerçek API):
      - Fear & Greed Index (alternative.me) — ücretsiz
      - CryptoPanic haber akışı — ücretsiz tier

    Veto koşulu: Ekstrem korku + kötü haberler aynı anda → negatif sinyal
    """

    def __init__(self, config: dict, news_feed=None):
        self.config    = config
        self.news_feed = news_feed   # NewsFeed instance (main.py'den inject edilir)

    async def analyze(self, symbol: str, market_data: dict) -> AgentVote:
        """
        Duygu sinyali üret.
        market_data'da önceden çekilmiş sentiment varsa onu kullan,
        yoksa news_feed'den direkt çek.
        """
        reasons = []
        score   = 0.0

        # Sentiment verisi: scanner veya news_feed'den gelir
        if self.news_feed is not None:
            try:
                sentiment = await self.news_feed.get_full_sentiment(symbol)
                fg_val    = sentiment["fear_greed"]
                fg_label  = sentiment["fg_label"]
                news_score= sentiment["news_score"]
                combined  = sentiment["combined_score"]
                news_count= sentiment["news_count"]
                top_news  = sentiment["top_news"]
            except Exception as e:
                logger.warning(f"[Sentiment] {symbol} haber alınamadı: {e}")
                # Fallback: market_data'daki mock veriye dön
                combined   = market_data.get("institutional_news", 0.0)
                fg_val     = market_data.get("fear_greed_index", 50)
                fg_label   = "Unknown"
                news_score = 0.0
                news_count = 0
                top_news   = []
        else:
            # news_feed bağlı değil → market_data'daki mock veriyi kullan
            combined   = market_data.get("institutional_news", 0.0)
            fg_val     = market_data.get("fear_greed_index", 50)
            fg_label   = "N/A"
            news_score = market_data.get("institutional_news", 0.0)
            news_count = 0
            top_news   = []

        # ── Fear & Greed değerlendirmesi ──────────────────────
        # Tersine trading: aşırı korku = al fırsatı, aşırı açgözlülük = dikkat
        if fg_val <= 20:
            score += 40
            reasons.append(f"Ekstrem korku F&G={fg_val} → güçlü alım fırsatı")
        elif fg_val <= 35:
            score += 20
            reasons.append(f"Korku bölgesi F&G={fg_val} → alım fırsatı")
        elif fg_val >= 80:
            score -= 30
            reasons.append(f"Ekstrem açgözlülük F&G={fg_val} → dikkat, düzeltme riski")
        elif fg_val >= 65:
            score -= 10
            reasons.append(f"Açgözlülük bölgesi F&G={fg_val}")
        else:
            reasons.append(f"Nötr F&G={fg_val} ({fg_label})")

        # ── Haber duygusu ─────────────────────────────────────
        news_contrib = news_score * 35   # -35 ile +35 arası katkı
        score += news_contrib
        if news_count > 0:
            direction = "olumlu" if news_score > 0.2 else ("olumsuz" if news_score < -0.2 else "nötr")
            reasons.append(f"{news_count} haber, duygu {direction} ({news_score:+.2f})")
            if top_news:
                reasons.append(f"Öne çıkan: '{top_news[0][:50]}'")

        # ── Birleşik skor ─────────────────────────────────────
        # combined zaten ağırlıklı birleşim (-1 ile +1)
        score = combined * 100   # Diğer ajanlarla aynı ölçeğe getir

        # Sınırla
        score = max(-100, min(100, score))

        # Güven: haber sayısı arttıkça güven artar
        confidence = min(0.50 + news_count * 0.02, 0.85)
        if self.news_feed is None:
            confidence = 0.40   # Mock veri → düşük güven

        return AgentVote(
            agent_name="sentiment_analyst",
            signal=round(score, 1),
            confidence=round(confidence, 2),
            reasoning="; ".join(reasons) or "Sentiment nötr",
        )
