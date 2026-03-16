"""
binance_client.py — Binance Futures API İstemcisi

Üç mod:
  testnet → https://testnet.binancefuture.com (gerçek emir, sahte para)
  live    → Gerçek Binance Futures
  paper   → Hiçbir yere bağlanmaz, sadece log

Testnet API key:
  https://testnet.binancefuture.com → sağ üst köşe → API Key
  (Normal Binance key'den farklı — ayrı key gerekir)
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TESTNET_FUTURES_URL = "https://testnet.binancefuture.com/fapi"


class BinanceFuturesClient:

    def __init__(self, config: dict):
        self.config   = config
        self.mode     = config.get("mode", "paper")   # paper | testnet | live
        self._client  = None
        self._testnet = (self.mode == "testnet")

        if self.mode in ("testnet", "live"):
            self._init_client()

    # ── Bağlantı ──────────────────────────────────────────────

    def _init_client(self):
        try:
            from binance.client import Client

            api_key    = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")

            if not api_key or not api_secret:
                raise ValueError(
                    "BINANCE_API_KEY veya BINANCE_API_SECRET eksik!\n"
                    "Testnet key: https://testnet.binancefuture.com → API Key"
                )

            self._client = Client(
                api_key=api_key,
                api_secret=api_secret,
                testnet=self._testnet,
            )

            # python-binance bazı sürümlerde testnet URL'yi otomatik ayarlamaz
            if self._testnet:
                self._client.FUTURES_URL = TESTNET_FUTURES_URL

            # Bağlantı testi
            bal = self._usdt_balance_sync()
            logger.info(
                f"[Binance] ✅ {'TESTNET' if self._testnet else 'CANLI'} bağlandı | "
                f"Bakiye: {bal:.2f} USDT"
            )

        except ImportError:
            raise RuntimeError("python-binance kurulu değil → pip install python-binance")

    def _usdt_balance_sync(self) -> float:
        try:
            for b in self._client.futures_account_balance():
                if b["asset"] == "USDT":
                    return float(b["balance"])
        except Exception:
            pass
        return 0.0

    # ── Piyasa Verisi ──────────────────────────────────────────

    async def get_futures_symbols(self) -> list[str]:
        """Tüm aktif perpetual USDT-M sembollerini çek."""
        if self.mode == "paper":
            return ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","AVAXUSDT",
                    "ADAUSDT","DOTUSDT","LINKUSDT","NEARUSDT","APTUSDT",
                    "ARBUSDT","OPUSDT","INJUSDT","SUIUSDT","TIAUSDT"]
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, self._client.futures_exchange_info)
            return [
                s["symbol"] for s in info["symbols"]
                if s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s["contractType"] == "PERPETUAL"
            ]
        except Exception as e:
            logger.error(f"[Binance] Sembol listesi alınamadı: {e}")
            return []

    async def get_all_tickers(self) -> dict[str, float]:
        """Tüm futures fiyatlarını tek istekte çek."""
        if self.mode == "paper":
            import random
            base = {"BTCUSDT":65000,"ETHUSDT":3200,"BNBUSDT":580,
                    "SOLUSDT":150,"AVAXUSDT":35,"ADAUSDT":0.45}
            return {
                k: v * random.uniform(0.98, 1.02)
                for k, v in base.items()
            }
        loop = asyncio.get_event_loop()
        try:
            tickers = await loop.run_in_executor(None, self._client.futures_symbol_ticker)
            return {t["symbol"]: float(t["price"]) for t in tickers}
        except Exception as e:
            logger.error(f"[Binance] Toplu ticker hatası: {e}")
            return {}

    async def get_klines(self, symbol: str, interval: str = "1h",
                         limit: int = 100) -> list[dict]:
        """Mum verisi — RSI / MACD / ATR hesabı için."""
        if self.mode == "paper":
            return []
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self._client.futures_klines(
                    symbol=symbol, interval=interval, limit=limit
                )
            )
            return [
                {
                    "open_time": k[0], "open":  float(k[1]),
                    "high":      float(k[2]),   "low":   float(k[3]),
                    "close":     float(k[4]),   "volume":float(k[5]),
                }
                for k in raw
            ]
        except Exception as e:
            logger.error(f"[Binance] {symbol} kline hatası: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> float:
        if self.mode == "paper":
            import random; return random.uniform(-0.002, 0.002)
        loop = asyncio.get_event_loop()
        try:
            fr = await loop.run_in_executor(
                None,
                lambda: self._client.futures_funding_rate(symbol=symbol, limit=1)
            )
            return float(fr[0]["fundingRate"]) if fr else 0.0
        except Exception:
            return 0.0

    async def get_open_interest(self, symbol: str) -> float:
        if self.mode == "paper":
            return 0.0
        loop = asyncio.get_event_loop()
        try:
            oi = await loop.run_in_executor(
                None, lambda: self._client.futures_open_interest(symbol=symbol)
            )
            return float(oi["openInterest"])
        except Exception:
            return 0.0

    async def get_account_balance(self) -> float:
        if self.mode == "paper":
            return float(self.config.get("initial_capital_usdt", 1000))
        loop = asyncio.get_event_loop()
        try:
            for b in await loop.run_in_executor(None, self._client.futures_account_balance):
                if b["asset"] == "USDT":
                    return float(b["balance"])
        except Exception as e:
            logger.error(f"[Binance] Bakiye hatası: {e}")
        return 0.0

    async def get_open_positions(self) -> list[dict]:
        if self.mode == "paper":
            return []
        loop = asyncio.get_event_loop()
        try:
            pos = await loop.run_in_executor(None, self._client.futures_position_information)
            return [p for p in pos if float(p["positionAmt"]) != 0]
        except Exception as e:
            logger.error(f"[Binance] Pozisyon hatası: {e}")
            return []

    # ── Emirler ───────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        if self.mode == "paper":
            return True
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
            )
            return True
        except Exception as e:
            logger.warning(f"[Binance] {symbol} kaldıraç hatası: {e}")
            return False

    async def open_long(self, symbol: str, quantity: float,
                        leverage: int, stop_loss: float,
                        take_profits: list[float]) -> dict:
        if self.mode == "paper":
            logger.info(f"[PAPER] 📈 LONG {symbol} | {quantity:.4f} | {leverage}x | SL:{stop_loss:.4f}")
            return {"orderId": f"PAPER-L-{symbol}", "status": "FILLED"}

        loop = asyncio.get_event_loop()
        tag  = "TESTNET" if self._testnet else "LIVE"
        try:
            await self.set_leverage(symbol, leverage)

            order = await loop.run_in_executor(
                None,
                lambda: self._client.futures_create_order(
                    symbol=symbol, side="BUY",
                    type="MARKET", quantity=round(quantity, 3),
                )
            )
            logger.info(f"[{tag}] ✅ LONG {symbol} açıldı | ID:{order.get('orderId')}")

            # Stop-loss emri
            if stop_loss > 0:
                await loop.run_in_executor(
                    None,
                    lambda: self._client.futures_create_order(
                        symbol=symbol, side="SELL",
                        type="STOP_MARKET",
                        stopPrice=round(stop_loss, 2),
                        closePosition=True,
                        timeInForce="GTE_GTC",
                    )
                )
            return order
        except Exception as e:
            logger.error(f"[{tag}] LONG hatası {symbol}: {e}")
            return {"error": str(e)}

    async def open_short(self, symbol: str, quantity: float,
                         leverage: int, stop_loss: float,
                         take_profits: list[float]) -> dict:
        if self.mode == "paper":
            logger.info(f"[PAPER] 📉 SHORT {symbol} | {quantity:.4f} | {leverage}x | SL:{stop_loss:.4f}")
            return {"orderId": f"PAPER-S-{symbol}", "status": "FILLED"}

        loop = asyncio.get_event_loop()
        tag  = "TESTNET" if self._testnet else "LIVE"
        try:
            await self.set_leverage(symbol, leverage)

            order = await loop.run_in_executor(
                None,
                lambda: self._client.futures_create_order(
                    symbol=symbol, side="SELL",
                    type="MARKET", quantity=round(quantity, 3),
                )
            )
            logger.info(f"[{tag}] ✅ SHORT {symbol} açıldı | ID:{order.get('orderId')}")

            if stop_loss > 0:
                await loop.run_in_executor(
                    None,
                    lambda: self._client.futures_create_order(
                        symbol=symbol, side="BUY",
                        type="STOP_MARKET",
                        stopPrice=round(stop_loss, 2),
                        closePosition=True,
                        timeInForce="GTE_GTC",
                    )
                )
            return order
        except Exception as e:
            logger.error(f"[{tag}] SHORT hatası {symbol}: {e}")
            return {"error": str(e)}

    async def close_partial(self, symbol: str, quantity: float, side: str) -> dict:
        close_side = "SELL" if side in ("LONG", "BUY") else "BUY"
        if self.mode == "paper":
            logger.info(f"[PAPER] 💰 Kısmi kapat {symbol} {quantity:.4f}")
            return {"orderId": f"PAPER-C-{symbol}", "status": "FILLED"}

        loop = asyncio.get_event_loop()
        tag  = "TESTNET" if self._testnet else "LIVE"
        try:
            order = await loop.run_in_executor(
                None,
                lambda: self._client.futures_create_order(
                    symbol=symbol, side=close_side,
                    type="MARKET", quantity=round(quantity, 3),
                    reduceOnly=True,
                )
            )
            logger.info(f"[{tag}] 💰 Kısmi kapatıldı {symbol}")
            return order
        except Exception as e:
            logger.error(f"[{tag}] Kısmi kapatma hatası {symbol}: {e}")
            return {"error": str(e)}

    # ── Yardımcılar ────────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._client is not None or self.mode == "paper"

    @property
    def mode_label(self) -> str:
        return {"testnet":"TESTNET 🧪","live":"CANLI ⚡","paper":"PAPER 📄"}.get(self.mode, self.mode)
