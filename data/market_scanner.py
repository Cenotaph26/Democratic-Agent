"""
market_scanner.py — Gerçek Veri ile Coin Tarayıcı

Binance Testnet veya Canlı API'den veri çeker:
  - Tüm USDT-M perpetual semboller
  - Güncel fiyatlar (tek toplu istek)
  - Mum verisi → RSI, ATR hesabı
  - Funding rate, Open Interest

Paper modda: sabit sembol listesi + yaklaşık rastgele veri
"""

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# Sabit proje kalite skorları (whitepaper + ortaklık araştırması sonucu)
# Gerçek implementasyonda bir DB veya harici servis besler
PROJECT_SCORES: dict[str, float] = {
    "BTCUSDT":  95, "ETHUSDT":  93, "BNBUSDT":  80, "SOLUSDT":  82,
    "AVAXUSDT": 74, "ADAUSDT":  71, "DOTUSDT":  72, "LINKUSDT": 76,
    "NEARUSDT": 73, "APTUSDT":  70, "ARBUSDT":  71, "OPUSDT":   70,
    "INJUSDT":  68, "SUIUSDT":  66, "TIAUSDT":  65, "SEIUSDT":  64,
    "STXUSDT":  63, "FTMUSDT":  60, "ATOMUSDT": 71, "MATICUSDT":69,
    "LTCUSDT":  67, "BCHUSDT":  65, "XLMUSDT":  62, "ALGOUSDT": 60,
    "SANDUSDT": 52, "MANAUSDT": 51, "GALAUSDT": 50, "APEUSDT":  48,
    "SHIBUSDT": 30, "DOGEUSDT": 35, "PEPEUSDT": 20,  # Meme — düşük skor
}

# Asla alım yapılmayacaklar
BLACKLIST = {
    "USDCUSDT","BUSDUSDT","TUSDUSDT","USDTUSDT","FDUSDUSDT",
    "WBTCUSDT","WETHUSDT",
    "BTCUPUSDT","BTCDOWNUSDT","ETHUPUSDT","ETHDOWNUSDT",
}


class MarketScanner:
    """
    Tüm coinleri tarar ve piyasa verisiyle birlikte aday listesi döndürür.
    BinanceFuturesClient üzerinden çalışır — testnet veya canlı.
    """

    def __init__(self, config: dict, client=None):
        self.config        = config
        self.client        = client          # BinanceFuturesClient (dışarıdan inject edilir)
        self.min_volume    = config.get("min_volume_24h_usd", 5_000_000)
        self.min_score     = config.get("min_project_score", 50)
        self._price_cache: dict[str, float] = {}

    def set_client(self, client):
        """Client'ı sonradan bağla (circular import önlemek için)."""
        self.client = client

    # ── Ana tarama ─────────────────────────────────────────────

    async def get_candidates(self) -> dict[str, dict]:
        """
        Filtrelerden geçen coinleri piyasa verisiyle döndür.
        """
        if self.client is None:
            logger.error("[Scanner] Client bağlı değil!")
            return {}

        # 1. Tüm aktif semboller
        all_symbols = await self.client.get_futures_symbols()
        all_symbols = [s for s in all_symbols if s not in BLACKLIST]

        # 2. Tüm fiyatları tek seferde çek
        all_prices = await self.client.get_all_tickers()
        self._price_cache = all_prices

        # 3. Proje skoru filtrelemesi
        scored = [
            s for s in all_symbols
            if PROJECT_SCORES.get(s, 0) >= self.min_score
        ]

        logger.info(
            f"[Scanner] {len(all_symbols)} sembol → "
            f"{len(scored)} proje skoru filtresi geçti"
        )

        # 4. Her sembol için detaylı veri topla (paralel)
        tasks = {s: self._build_market_data(s, all_prices) for s in scored}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        candidates = {}
        for symbol, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.debug(f"[Scanner] {symbol} atlandı: {result}")
                continue
            if result is not None:
                candidates[symbol] = result

        logger.info(f"[Scanner] {len(candidates)} aday parlamentoya sunuluyor")
        return candidates

    async def get_prices(self, symbols: list[str]) -> dict[str, float]:
        """Belirli sembollerin güncel fiyatlarını döndür."""
        if not symbols:
            return {}
        if self.client is None:
            return self._price_cache

        all_tickers = await self.client.get_all_tickers()
        self._price_cache.update(all_tickers)
        return {s: all_tickers[s] for s in symbols if s in all_tickers}

    # ── Detaylı veri toplama ───────────────────────────────────

    async def _build_market_data(self, symbol: str,
                                  all_prices: dict[str, float]) -> Optional[dict]:
        """Tek sembol için tam piyasa verisi paketi oluştur."""
        price = all_prices.get(symbol)
        if not price:
            return None

        # Mum verisi → RSI + ATR (1h mumlar)
        klines = await self.client.get_klines(symbol, interval="1h", limit=50)
        rsi    = self._calc_rsi(klines)
        atr    = self._calc_atr(klines, price)
        macd_s = self._calc_macd_signal(klines)
        volume_24h = self._estimate_volume(klines, price)

        # Hacim filtresi
        if volume_24h < self.min_volume:
            return None

        # Funding rate + OI (asenkron)
        funding = await self.client.get_funding_rate(symbol)
        # OI: her sembol için ayrı istek → yük azaltmak için %20 ihtimalle çek
        oi_change = 0.0
        if random.random() < 0.20:
            try:
                oi = await self.client.get_open_interest(symbol)
                oi_change = random.uniform(-15, 15)  # Önceki değerle karşılaştırma gerekir
            except Exception:
                pass

        project_score = PROJECT_SCORES.get(symbol, 50.0)

        return {
            "symbol":               symbol,
            "price":                price,
            "volume_24h_usd":       volume_24h,
            "project_score":        project_score,
            # Teknik göstergeler
            "rsi_14":               rsi,
            "macd_signal":          macd_s,
            "atr":                  atr,
            # Balina / on-chain
            "exchange_inflow_24h":  self._mock_onchain("inflow",  symbol),
            "exchange_outflow_24h": self._mock_onchain("outflow", symbol),
            "funding_rate":         funding,
            "open_interest_change": oi_change,
            "large_transfers_count":random.randint(0, 15),
            # Sentiment
            "institutional_news":   self._mock_sentiment(symbol),
            "fear_greed_index":     self._fear_greed_index(),
            "social_volume_change": random.uniform(-20, 80),
        }

    # ── Teknik Göstergeler ─────────────────────────────────────

    def _calc_rsi(self, klines: list[dict], period: int = 14) -> float:
        """RSI hesapla. Kline yoksa 50 (nötr) döndür."""
        if len(klines) < period + 1:
            return 50.0
        closes = [k["close"] for k in klines]
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs  = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def _calc_atr(self, klines: list[dict], price: float,
                   period: int = 14) -> float:
        """ATR (Average True Range) hesapla."""
        if len(klines) < 2:
            return price * 0.015   # Varsayılan: fiyatın %1.5'i
        trs = []
        for i in range(1, len(klines)):
            h, l, pc = klines[i]["high"], klines[i]["low"], klines[i-1]["close"]
            trs.append(max(h-l, abs(h-pc), abs(l-pc)))
        atr = sum(trs[-period:]) / min(len(trs), period)
        return round(atr, 6)

    def _calc_macd_signal(self, klines: list[dict]) -> float:
        """
        MACD sinyal yönü: +1 pozitif kesişim, -1 negatif, 0 nötr.
        Tam hesaplama için yeterli mum gerekir.
        """
        if len(klines) < 26:
            return 0.0
        closes = [k["close"] for k in klines]
        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        macd  = ema12 - ema26
        return round(macd / closes[-1], 6) if closes[-1] else 0.0

    def _ema(self, values: list[float], period: int) -> float:
        k = 2 / (period + 1)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _estimate_volume(self, klines: list[dict], price: float) -> float:
        """24 saatlik hacim tahmini (USD)."""
        if not klines:
            return random.uniform(5_000_000, 500_000_000)
        last_24 = klines[-24:] if len(klines) >= 24 else klines
        vol = sum(k["volume"] for k in last_24) * price
        return vol

    # ── Mock Veriler (on-chain & sentiment) ───────────────────
    # Gerçek implementasyonda: Glassnode, CryptoQuant, Santiment API'leri

    def _mock_onchain(self, flow_type: str, symbol: str) -> float:
        seed = hash(symbol + flow_type) % 1000
        rng  = random.Random(seed)
        return rng.uniform(0, 80_000) if flow_type == "outflow" else rng.uniform(0, 50_000)

    def _mock_sentiment(self, symbol: str) -> float:
        seed = hash(symbol) % 100
        rng  = random.Random(seed)
        return rng.uniform(-0.5, 0.8)

    def _fear_greed_index(self) -> float:
        """
        Gerçek implementasyonda: https://api.alternative.me/fng/
        Şimdilik sabit ~50 civarı.
        """
        return random.uniform(35, 65)
