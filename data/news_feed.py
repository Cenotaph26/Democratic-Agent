"""
news_feed.py — Gerçek Haber ve Duygu Verisi

CryptoPanic API: Kripto haber akışı (ücretsiz tier: 5 istek/dakika)
  Kayıt: https://cryptopanic.com/developers/api/

Fear & Greed Index: alternative.me — tamamen ücretsiz, sınırsız
  https://api.alternative.me/fng/

Önbellekleme:
  Her API isteğinin sonucu PostgreSQL'de önbelleğe alınır.
  CryptoPanic: 15 dakika TTL
  Fear & Greed: 1 saat TTL
  Bot çöküp yeniden başlasa bile önbellek devam eder.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# CryptoPanic
CRYPTOPANIC_BASE  = "https://cryptopanic.com/api/v1/posts/"
CRYPTOPANIC_TOKEN = os.environ.get("CRYPTOPANIC_API_TOKEN", "")

# Fear & Greed — ücretsiz, key gerektirmiyor
FEAR_GREED_URL    = "https://api.alternative.me/fng/?limit=1"

# Sembol → CryptoPanic arama terimi eşlemesi
COIN_SYMBOLS = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "BNBUSDT": "BNB",
    "SOLUSDT": "SOL", "AVAXUSDT": "AVAX", "ADAUSDT": "ADA",
    "DOTUSDT": "DOT", "LINKUSDT": "LINK", "NEARUSDT": "NEAR",
    "APTUSDT": "APT", "ARBUSDT": "ARB",  "OPUSDT": "OP",
    "INJUSDT": "INJ", "SUIUSDT": "SUI",  "TIAUSDT": "TIA",
}


class NewsFeed:
    """
    Gerçek zamanlı haber ve duygu verisi sağlayıcı.

    BotMemory entegrasyonu: önbellek otomatik olarak DB'den yüklenir/kaydedilir.
    """

    def __init__(self, memory=None):
        self.memory   = memory     # BotMemory instance (önbellekleme için)
        self._session: Optional[aiohttp.ClientSession] = None
        self._fg_cache: Optional[dict] = None
        self._fg_cache_time: Optional[datetime] = None

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "DemocraticTradingBot/2.0"},
        )

    async def close(self):
        if self._session:
            await self._session.close()

    # ── Fear & Greed Index ─────────────────────────────────────

    async def get_fear_greed(self) -> dict:
        """
        0 = Ekstrem Korku, 100 = Ekstrem Açgözlülük
        Günde birkaç kez değişir. 1 saat önbellek yeterli.
        """
        # Önbellek kontrolü (bellek içi)
        if self._fg_cache and self._fg_cache_time:
            age = (datetime.utcnow() - self._fg_cache_time).seconds
            if age < 3600:
                return self._fg_cache

        # DB önbelleği kontrolü
        if self.memory:
            cached = await self.memory.get_news_cache("fear_greed")
            if cached:
                self._fg_cache      = cached
                self._fg_cache_time = datetime.utcnow()
                return cached

        # API isteği
        result = await self._fetch_fear_greed()

        # Önbelleğe al
        self._fg_cache      = result
        self._fg_cache_time = datetime.utcnow()
        if self.memory:
            await self.memory.set_news_cache("fear_greed", result, ttl_minutes=60)

        return result

    async def _fetch_fear_greed(self) -> dict:
        try:
            if self._session is None:
                await self.start()
            async with self._session.get(FEAR_GREED_URL) as resp:
                data = await resp.json()
                item = data["data"][0]
                value      = int(item["value"])
                label      = item["value_classification"]
                normalized = value / 100.0   # 0.0 - 1.0

                result = {
                    "value":      value,
                    "label":      label,
                    "normalized": normalized,
                    "timestamp":  item.get("timestamp", ""),
                }
                logger.info(f"[NewsFeed] Fear & Greed: {value} ({label})")
                return result
        except Exception as e:
            logger.warning(f"[NewsFeed] Fear & Greed alınamadı: {e} → varsayılan kullanılıyor")
            return {"value": 50, "label": "Neutral", "normalized": 0.5, "timestamp": ""}

    # ── CryptoPanic Haber Akışı ────────────────────────────────

    async def get_coin_sentiment(self, symbol: str) -> dict:
        """
        Bir coin için son haberlerin duygu skoru.

        Döner:
          score:      -1.0 ile +1.0 arası (negatif=kötü haber, pozitif=iyi)
          news_count: son 24 saatteki haber sayısı
          top_news:   en önemli 3 haber başlığı
        """
        coin = COIN_SYMBOLS.get(symbol, symbol.replace("USDT", ""))
        cache_key = f"cryptopanic_{coin}"

        # DB önbelleği
        if self.memory:
            cached = await self.memory.get_news_cache(cache_key)
            if cached:
                return cached

        result = await self._fetch_cryptopanic(coin)

        if self.memory:
            await self.memory.set_news_cache(cache_key, result, ttl_minutes=15)

        return result

    async def _fetch_cryptopanic(self, coin: str) -> dict:
        """CryptoPanic API'den haber çek ve duygu skoru hesapla."""
        if not CRYPTOPANIC_TOKEN:
            # Token yoksa sadece F&G'yi kullan
            logger.debug(f"[NewsFeed] CryptoPanic token yok, {coin} için mock döndürülüyor")
            return {"score": 0.0, "news_count": 0, "top_news": [], "source": "mock"}

        try:
            if self._session is None:
                await self.start()

            params = {
                "auth_token":  CRYPTOPANIC_TOKEN,
                "currencies":  coin,
                "kind":        "news",
                "filter":      "hot",
                "public":      "true",
            }
            async with self._session.get(CRYPTOPANIC_BASE, params=params) as resp:
                if resp.status == 429:
                    logger.warning(f"[NewsFeed] CryptoPanic rate limit aşıldı")
                    return {"score": 0.0, "news_count": 0, "top_news": [], "source": "rate_limit"}

                data = await resp.json()
                results = data.get("results", [])

                if not results:
                    return {"score": 0.0, "news_count": 0, "top_news": [], "source": "empty"}

                # Duygu puanı hesapla
                score = self._calc_sentiment_score(results)
                top   = [r.get("title", "")[:80] for r in results[:3]]

                result = {
                    "score":      round(score, 3),
                    "news_count": len(results),
                    "top_news":   top,
                    "source":     "cryptopanic",
                }
                logger.info(
                    f"[NewsFeed] {coin}: {len(results)} haber | "
                    f"Duygu skoru: {score:+.2f}"
                )
                return result

        except asyncio.TimeoutError:
            logger.warning(f"[NewsFeed] CryptoPanic timeout ({coin})")
        except Exception as e:
            logger.warning(f"[NewsFeed] CryptoPanic hatası ({coin}): {e}")

        return {"score": 0.0, "news_count": 0, "top_news": [], "source": "error"}

    def _calc_sentiment_score(self, news_items: list) -> float:
        """
        Haber listesinden -1.0 ile +1.0 arası duygu skoru hesapla.

        CryptoPanic votes alanı: bullish / bearish oy sayıları içerir.
        """
        if not news_items:
            return 0.0

        total_bull = 0
        total_bear = 0

        for item in news_items[:20]:   # Son 20 haber
            votes = item.get("votes", {})
            # Bazı haberlerde 'liked' / 'disliked' olabilir
            bull = votes.get("positive", 0) + votes.get("liked", 0)
            bear = votes.get("negative", 0) + votes.get("disliked", 0)
            total_bull += bull
            total_bear += bear

            # Haber başlığı anahtar kelime analizi (kaba ama etkili)
            title = item.get("title", "").lower()
            if any(w in title for w in [
                "partnership", "integration", "launch", "upgrade",
                "bullish", "surge", "rally", "adoption", "listing",
                "institutional", "etf", "approval"
            ]):
                total_bull += 2
            if any(w in title for w in [
                "hack", "exploit", "crash", "ban", "regulation",
                "bearish", "dump", "sell-off", "fraud", "lawsuit",
                "sec", "warning", "decline"
            ]):
                total_bear += 2

        total = total_bull + total_bear
        if total == 0:
            return 0.0
        return (total_bull - total_bear) / total

    # ── Birleşik Duygu Verisi ─────────────────────────────────

    async def get_full_sentiment(self, symbol: str) -> dict:
        """
        CryptoPanic + Fear & Greed birleştirilerek tek duygu paketi döndürür.
        Sentiment ajanı bunu kullanır.
        """
        # Paralel çek
        fg_task   = asyncio.create_task(self.get_fear_greed())
        news_task = asyncio.create_task(self.get_coin_sentiment(symbol))

        fg   = await fg_task
        news = await news_task

        # F&G → -1 ile +1 arasına dönüştür (50 nötr)
        fg_score = (fg["normalized"] - 0.5) * 2   # -1.0 ile +1.0

        # Ağırlıklı birleştir: haber %60, F&G %40
        combined = 0.6 * news["score"] + 0.4 * fg_score

        return {
            "symbol":         symbol,
            "combined_score": round(combined, 3),
            "fear_greed":     fg["value"],
            "fg_label":       fg["label"],
            "news_score":     news["score"],
            "news_count":     news["news_count"],
            "top_news":       news["top_news"],
        }
