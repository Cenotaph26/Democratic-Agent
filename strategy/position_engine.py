"""
position_engine.py — Üç Katmanlı Pozisyon Motoru

Sistemin kalbi. Parlamentodan gelen kararı üç katmana yönlendirir:

KATMAN 1 — BİRİKİM  (Ana mantık, asla kesilmez)
  • Majör coinler (BTC, ETH, BNB, SOL) → 2x-3x, haftalık DCA
  • Onaylı projeler (proje skoru ≥ 65)  → 3x-5x, aylık DCA
  • Dip alımları: -%5 / -%12 / -%20 seviyelerinde ek alım
  • Kâr: +%30/+%60/+%100'de kısmi satış, kalan %55 SONSUZA dek tutulur

KATMAN 2 — FIRSAT  (Sinyal bazlı, bağımsız muhasebe)
  • Konsensüs ≥ ±55 → fırsat pozisyonu aç
  • 5x–15x kaldıraç, kısa vadeli
  • TP: +%5/+%10/+%20 kademeli çıkış · SL: -%3
  • Kâr → birikim bütçesine aktarılır

KATMAN 3 — KASA YÖNETİMİ
  • %60 birikim · %25 fırsat · %15 nakit rezerv
  • Fırsat kârı birikimi büyütür
  • Birikim alımları için rezerv asla tükenmez
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────────────────────

class AccumTier(Enum):
    MAJOR   = "majör"    # BTC ETH BNB SOL
    PROJECT = "proje"    # Onaylı altcoin

MAJOR_COINS = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}


# ──────────────────────────────────────────────────────────────
# Veri Sınıfları
# ──────────────────────────────────────────────────────────────

@dataclass
class AccumEntry:
    """Tek bir birikim alımı kaydı."""
    price: float
    quantity: float
    usdt_spent: float
    entry_num: int = 1
    trigger: str = "initial"   # initial | dip_5 | dip_12 | dip_20 | scheduled
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProfitEvent:
    """Gerçekleşen kısmi kâr satışı."""
    level_pct: float
    sold_qty: float
    sold_price: float
    realized_usdt: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AccumPosition:
    """
    Bir coinin birikim pozisyonu.
    KAPATILMAZ — sadece kısmi satış yapılır, çekirdek pozisyon sonsuza tutulur.
    """
    symbol: str
    tier: AccumTier
    leverage: int
    total_budget_usdt: float
    entries: list[AccumEntry] = field(default_factory=list)
    profit_events: list[ProfitEvent] = field(default_factory=list)
    dip_levels_triggered: set = field(default_factory=set)
    profit_levels_triggered: set = field(default_factory=set)
    next_scheduled_buy: datetime = field(default_factory=datetime.utcnow)

    @property
    def total_bought_qty(self) -> float:
        return sum(e.quantity for e in self.entries)

    @property
    def total_sold_qty(self) -> float:
        return sum(e.sold_qty for e in self.profit_events)

    @property
    def holding_qty(self) -> float:
        """Şu an elde tutulan miktar."""
        return max(self.total_bought_qty - self.total_sold_qty, 0.0)

    @property
    def total_invested(self) -> float:
        return sum(e.usdt_spent for e in self.entries)

    @property
    def average_cost(self) -> float:
        total_q = self.total_bought_qty
        if total_q == 0:
            return 0.0
        return sum(e.price * e.quantity for e in self.entries) / total_q

    def unrealized_pnl_pct(self, price: float) -> float:
        if self.average_cost == 0:
            return 0.0
        return (price - self.average_cost) / self.average_cost * 100

    def current_value(self, price: float) -> float:
        return self.holding_qty * price

    def total_realized(self) -> float:
        return sum(e.realized_usdt for e in self.profit_events)

    def summary(self, price: float) -> dict:
        return {
            "symbol":          self.symbol,
            "tier":            self.tier.value,
            "leverage":        self.leverage,
            "alim_sayisi":     len(self.entries),
            "ortalama_maliyet": round(self.average_cost, 6),
            "miktar":          round(self.holding_qty, 6),
            "yatirim_usdt":    round(self.total_invested, 2),
            "deger_usdt":      round(self.current_value(price), 2),
            "gerceklesmemis_kar_pct": round(self.unrealized_pnl_pct(price), 2),
            "realize_kar_usdt": round(self.total_realized(), 2),
            "dip_seviyeleri":  list(self.dip_levels_triggered),
        }


@dataclass
class OpportunityPosition:
    """
    Fırsat pozisyonu — kısa vadeli, tam çıkışla kapanır.
    Birikim ile ayrı muhasebe.
    """
    symbol: str
    direction: str       # "LONG" | "SHORT"
    leverage: int
    entry_price: float
    quantity: float
    usdt_allocated: float
    stop_loss: float
    take_profits: list[float]
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed: bool = False
    realized_pnl: float = 0.0
    tp_hits: list[float] = field(default_factory=list)

    def remaining_qty(self) -> float:
        closed_ratio = len(self.tp_hits) * 0.33
        return self.quantity * max(1.0 - closed_ratio, 0.0)


# ──────────────────────────────────────────────────────────────
# Ana Motor
# ──────────────────────────────────────────────────────────────

class PositionEngine:
    """
    Üç katmanlı pozisyon motoru.

    Kullanım akışı:
      1. Parliament bir karar üretir
      2. route_parliament_decision() katmanı belirler
      3. İlgili katman metotları çağrılır
      4. Binance client emirleri iletir
    """

    # ── Birikim DCA parametreleri ──────────────────────────────
    DIP_LEVELS = {
        "dip_5":  (5.0,  0.20),   # -%5  → bütçenin %20'si
        "dip_12": (12.0, 0.25),   # -%12 → %25
        "dip_20": (20.0, 0.20),   # -%20 → %20
        # %35 ilk girişte · toplam: %35+%20+%25+%20 = %100
    }

    ACCUM_PROFIT_LEVELS = [
        (30.0,  0.15),   # +%30  → %15 sat
        (60.0,  0.15),   # +%60  → %15 sat
        (100.0, 0.15),   # +%100 → %15 sat
        # Kalan %55 sonsuza tutulur
    ]

    SCHEDULED_INTERVAL = {
        AccumTier.MAJOR:   timedelta(days=7),   # Majör: haftalık
        AccumTier.PROJECT: timedelta(days=30),  # Proje: aylık
    }
    SCHEDULED_ALLOC_PCT = 0.05   # Her takvim alımında bütçenin %5'i

    # Majör coin maks bütçe payı
    MAJOR_MAX_SHARE = 0.25   # Tek majör coine birikim bütçesinin maks %25'i
    PROJECT_MAX_SHARE = 0.10

    # ── Fırsat parametreleri ───────────────────────────────────
    OPP_LEVERAGE_MAP = [
        (55, 69,  5),
        (70, 84, 10),
        (85, 100,15),
    ]
    OPP_TP_PCT   = [0.05, 0.10, 0.20]
    OPP_SL_PCT   = 0.03
    OPP_CLOSE_PER_TP = 0.33       # Her TP'de %33 kapat
    OPP_MAX_CONCURRENT = 3        # Aynı anda maks fırsat pozisyonu
    OPP_PER_TRADE_PCT  = 0.12     # Fırsat bütçesinin %12'si tek işlemde

    # ── Kasa dağılımı ──────────────────────────────────────────
    TREASURY_SPLIT = {
        "accum":   0.60,
        "opp":     0.25,
        "reserve": 0.15,
    }

    def __init__(self, total_capital_usdt: float):
        self.total_capital   = total_capital_usdt
        self.accum_budget    = total_capital_usdt * self.TREASURY_SPLIT["accum"]
        self.opp_budget      = total_capital_usdt * self.TREASURY_SPLIT["opp"]
        self.reserve         = total_capital_usdt * self.TREASURY_SPLIT["reserve"]

        self.accum_positions: dict[str, AccumPosition] = {}
        self.opp_positions:   dict[str, OpportunityPosition] = {}

        logger.info(
            f"[PositionEngine] Başlatıldı | Toplam: ${total_capital_usdt:.2f} | "
            f"Birikim: ${self.accum_budget:.2f} | "
            f"Fırsat: ${self.opp_budget:.2f} | "
            f"Rezerv: ${self.reserve:.2f}"
        )

    # ──────────────────────────────────────────────────────────
    # YÖNLENDIRME — Parliament kararını katmana atar
    # ──────────────────────────────────────────────────────────

    def route_parliament_decision(self, decision, market_data: dict) -> dict:
        """
        Parliament'ın ParliamentDecision nesnesini alır,
        doğru katmana yönlendirir, yapılacak işlemleri döndürür.

        Dönüş: {
          "accum_action": None | "open" | "dip_buy" | "scheduled",
          "accum_entry":  AccumEntry | None,
          "opp_action":   None | "open",
          "opp_position": OpportunityPosition | None,
          "profit_action": None | dict,
        }
        """
        symbol = decision.symbol
        price  = market_data.get("price", 0)
        result = {
            "accum_action":  None,
            "accum_entry":   None,
            "opp_action":    None,
            "opp_position":  None,
            "profit_action": None,
        }

        # ── 1. Birikim katmanı değerlendirmesi ────────────────
        project_score = market_data.get("project_score", 0)
        is_major      = symbol in MAJOR_COINS
        qualifies     = is_major or project_score >= 65

        if qualifies:
            if symbol not in self.accum_positions:
                # Yeni birikim pozisyonu → ilk giriş
                entry = self._open_accum(symbol, price, project_score)
                if entry:
                    result["accum_action"] = "open"
                    result["accum_entry"]  = entry
            else:
                # Mevcut birikim → dip / takvim kontrolü
                dip = self._check_dip_buy(symbol, price)
                if dip:
                    result["accum_action"] = "dip_buy"
                    result["accum_entry"]  = dip
                else:
                    sched = self._check_scheduled_buy(symbol, price)
                    if sched:
                        result["accum_action"] = "scheduled"
                        result["accum_entry"]  = sched

                # Kâr satış kontrolü
                profit = self._check_accum_profit(symbol, price)
                if profit:
                    result["profit_action"] = profit

        # ── 2. Fırsat katmanı değerlendirmesi ─────────────────
        consensus = decision.consensus_score
        action    = decision.action

        if action in ("LONG", "SHORT") and abs(consensus) >= 55:
            open_opps = sum(1 for p in self.opp_positions.values() if not p.closed)
            if open_opps < self.OPP_MAX_CONCURRENT:
                opp = self._open_opportunity(symbol, action, price, consensus)
                if opp:
                    result["opp_action"]   = "open"
                    result["opp_position"] = opp

        return result

    def tick_opportunity_exits(self, prices: dict[str, float]) -> list[dict]:
        """
        Her fiyat güncellemesinde tüm fırsat pozisyonlarında TP/SL kontrolü.
        Tetiklenen çıkışları liste olarak döndürür.
        """
        exits = []
        for symbol, pos in list(self.opp_positions.items()):
            if pos.closed:
                continue
            price  = prices.get(symbol)
            if price is None:
                continue
            result = self._check_opp_exit(symbol, price)
            if result:
                exits.append(result)
        return exits

    def tick_accum_checks(self, prices: dict[str, float]) -> list[dict]:
        """
        Her fiyat güncellemesinde tüm birikim pozisyonlarında
        dip alım + takvim alım + kâr satış kontrolü.
        """
        actions = []
        for symbol in list(self.accum_positions.keys()):
            price = prices.get(symbol)
            if price is None:
                continue

            dip = self._check_dip_buy(symbol, price)
            if dip:
                actions.append({"type": "dip_buy", "symbol": symbol, "entry": dip})

            sched = self._check_scheduled_buy(symbol, price)
            if sched:
                actions.append({"type": "scheduled_buy", "symbol": symbol, "entry": sched})

            profit = self._check_accum_profit(symbol, price)
            if profit:
                actions.append({"type": "profit_sell", "symbol": symbol, **profit})

        return actions

    # ──────────────────────────────────────────────────────────
    # KATMAN 1 — Birikim iç metodları
    # ──────────────────────────────────────────────────────────

    def _open_accum(self, symbol: str, price: float,
                    project_score: float) -> Optional[AccumEntry]:
        """İlk birikim girişi."""
        tier     = AccumTier.MAJOR if symbol in MAJOR_COINS else AccumTier.PROJECT
        leverage = 2 if tier == AccumTier.MAJOR else 3

        # Bu coine ayrılacak bütçe
        max_share = self.MAJOR_MAX_SHARE if tier == AccumTier.MAJOR else self.PROJECT_MAX_SHARE
        coin_budget = min(
            self.accum_budget * max_share,
            self.accum_budget / max(len(self.accum_positions) + 1, 1)
        )

        # İlk alım → bütçenin %35'i
        usdt_now = coin_budget * 0.35
        if usdt_now < 5:
            logger.warning(f"[AccumEngine] {symbol} için yetersiz bütçe, geçildi.")
            return None

        quantity = (usdt_now * leverage) / price
        entry = AccumEntry(
            price=price, quantity=quantity,
            usdt_spent=usdt_now, entry_num=1, trigger="initial"
        )

        pos = AccumPosition(
            symbol=symbol, tier=tier, leverage=leverage,
            total_budget_usdt=coin_budget, entries=[entry],
            next_scheduled_buy=datetime.utcnow() + self.SCHEDULED_INTERVAL[tier],
        )
        self.accum_positions[symbol] = pos

        logger.info(
            f"[AccumEngine] ✅ {symbol} BİRİKİM AÇILDI | "
            f"Tier:{tier.value} | {leverage}x | "
            f"İlk alım: ${usdt_now:.2f} @ {price:.4f} | "
            f"Toplam bütçe: ${coin_budget:.2f}"
        )
        return entry

    def _check_dip_buy(self, symbol: str, price: float) -> Optional[AccumEntry]:
        """Dip seviyesi tetiklendiğinde ek alım yap."""
        pos = self.accum_positions.get(symbol)
        if not pos or not pos.entries:
            return None

        first_price = pos.entries[0].price
        drop_pct    = (first_price - price) / first_price * 100
        if drop_pct <= 0:
            return None

        for level_name, (threshold, alloc_pct) in self.DIP_LEVELS.items():
            if level_name in pos.dip_levels_triggered:
                continue
            if drop_pct >= threshold:
                usdt     = pos.total_budget_usdt * alloc_pct
                quantity = (usdt * pos.leverage) / price
                entry    = AccumEntry(
                    price=price, quantity=quantity,
                    usdt_spent=usdt,
                    entry_num=len(pos.entries) + 1,
                    trigger=level_name
                )
                pos.entries.append(entry)
                pos.dip_levels_triggered.add(level_name)

                logger.info(
                    f"[AccumEngine] 📉 {symbol} DİP ALIM ({level_name}) | "
                    f"Düşüş:{drop_pct:.1f}% | {price:.4f} | "
                    f"${usdt:.2f} | Yeni ort.maliyet:{pos.average_cost:.4f}"
                )
                return entry
        return None

    def _check_scheduled_buy(self, symbol: str, price: float) -> Optional[AccumEntry]:
        """Takvim alımı — fiyattan bağımsız, düzenli."""
        pos = self.accum_positions.get(symbol)
        if not pos:
            return None
        if datetime.utcnow() < pos.next_scheduled_buy:
            return None

        usdt     = pos.total_budget_usdt * self.SCHEDULED_ALLOC_PCT
        quantity = (usdt * pos.leverage) / price
        entry    = AccumEntry(
            price=price, quantity=quantity,
            usdt_spent=usdt,
            entry_num=len(pos.entries) + 1,
            trigger="scheduled"
        )
        pos.entries.append(entry)
        pos.next_scheduled_buy = datetime.utcnow() + self.SCHEDULED_INTERVAL[pos.tier]

        logger.info(
            f"[AccumEngine] 🗓️  {symbol} TAKVİM ALIMI | "
            f"{price:.4f} | ${usdt:.2f} | "
            f"Ort.maliyet:{pos.average_cost:.4f}"
        )
        return entry

    def _check_accum_profit(self, symbol: str, price: float) -> Optional[dict]:
        """Kısmi kâr satışı — çekirdek pozisyon asla tamamen satılmaz."""
        pos = self.accum_positions.get(symbol)
        if not pos:
            return None

        pnl_pct = pos.unrealized_pnl_pct(price)

        for level_pct, sell_pct in self.ACCUM_PROFIT_LEVELS:
            if level_pct in pos.profit_levels_triggered:
                continue
            if pnl_pct >= level_pct:
                sell_qty  = pos.holding_qty * sell_pct
                realized  = sell_qty * price
                event     = ProfitEvent(
                    level_pct=level_pct, sold_qty=sell_qty,
                    sold_price=price, realized_usdt=realized
                )
                pos.profit_events.append(event)
                pos.profit_levels_triggered.add(level_pct)

                # Kârın %70'i birikim bütçesine döner
                reinvest = realized * 0.70
                self.accum_budget += reinvest

                logger.info(
                    f"[AccumEngine] 💰 {symbol} KÂR SATIŞI +{level_pct:.0f}% | "
                    f"Satış:{sell_qty:.4f} @ {price:.4f} | "
                    f"Realize:${realized:.2f} | "
                    f"Kasaya geri:${reinvest:.2f} | "
                    f"Kalan:{pos.holding_qty:.4f} (sonsuza tutulur)"
                )
                return {
                    "symbol":        symbol,
                    "sell_qty":      sell_qty,
                    "price":         price,
                    "realized_usdt": realized,
                    "level_pct":     level_pct,
                    "remaining_qty": pos.holding_qty,
                }
        return None

    # ──────────────────────────────────────────────────────────
    # KATMAN 2 — Fırsat iç metodları
    # ──────────────────────────────────────────────────────────

    def _open_opportunity(self, symbol: str, direction: str,
                          price: float, consensus: float) -> Optional[OpportunityPosition]:
        """Fırsat pozisyonu aç."""
        # Kaldıraç belirle
        abs_c    = abs(consensus)
        leverage = 5
        for lo, hi, lev in self.OPP_LEVERAGE_MAP:
            if lo <= abs_c <= hi:
                leverage = lev
                break

        # Bütçe
        alloc = self.opp_budget * self.OPP_PER_TRADE_PCT
        if alloc < 10 or self.opp_budget < alloc:
            logger.warning(f"[OppEngine] {symbol} fırsat bütçesi yetersiz")
            return None

        quantity = (alloc * leverage) / price

        # TP / SL
        if direction == "LONG":
            sl  = price * (1 - self.OPP_SL_PCT)
            tps = [price * (1 + tp) for tp in self.OPP_TP_PCT]
        else:
            sl  = price * (1 + self.OPP_SL_PCT)
            tps = [price * (1 - tp) for tp in self.OPP_TP_PCT]

        pos = OpportunityPosition(
            symbol=symbol, direction=direction, leverage=leverage,
            entry_price=price, quantity=quantity,
            usdt_allocated=alloc, stop_loss=sl, take_profits=tps,
        )
        self.opp_positions[symbol] = pos
        self.opp_budget -= alloc

        logger.info(
            f"[OppEngine] 🎯 {symbol} FIRSAT {direction} | "
            f"{leverage}x | Giriş:{price:.4f} | "
            f"SL:{sl:.4f} | TP1:{tps[0]:.4f} TP2:{tps[1]:.4f} TP3:{tps[2]:.4f} | "
            f"${alloc:.2f}"
        )
        return pos

    def _check_opp_exit(self, symbol: str, price: float) -> Optional[dict]:
        """TP / SL çıkış kontrolü."""
        pos = self.opp_positions.get(symbol)
        if not pos or pos.closed:
            return None

        # Stop-loss
        sl_hit = (pos.direction == "LONG"  and price <= pos.stop_loss) or \
                 (pos.direction == "SHORT" and price >= pos.stop_loss)
        if sl_hit:
            pnl = self._opp_pnl(pos, price, pos.remaining_qty())
            pos.realized_pnl += pnl
            pos.closed = True
            self.opp_budget += pos.usdt_allocated + pnl
            logger.warning(
                f"[OppEngine] ❌ {symbol} STOP-LOSS | {price:.4f} | PnL:${pnl:.2f}"
            )
            return {"action": "stop_loss", "symbol": symbol, "pnl": pnl}

        # Take-profit (kademeli)
        for tp_price in pos.take_profits:
            if tp_price in pos.tp_hits:
                continue
            tp_hit = (pos.direction == "LONG"  and price >= tp_price) or \
                     (pos.direction == "SHORT" and price <= tp_price)
            if not tp_hit:
                continue

            close_qty = pos.quantity * self.OPP_CLOSE_PER_TP
            pnl       = self._opp_pnl(pos, price, close_qty)
            pos.realized_pnl += pnl
            pos.tp_hits.append(tp_price)
            recovered = (close_qty / pos.quantity) * pos.usdt_allocated + pnl
            self.opp_budget += recovered

            # Son TP → pozisyon kapandı, kâr birikime
            is_last = len(pos.tp_hits) >= len(pos.take_profits)
            if is_last:
                pos.closed = True
                self._route_profit_to_accum(pos.realized_pnl)

            tp_num = len(pos.tp_hits)
            logger.info(
                f"[OppEngine] ✅ {symbol} TP {tp_num}/{len(pos.take_profits)} | "
                f"{price:.4f} | Bu TP:${pnl:.2f} | Toplam:${pos.realized_pnl:.2f}"
                + (" → Kâr birikime aktarıldı" if is_last else "")
            )
            return {
                "action":    "take_profit",
                "symbol":    symbol,
                "tp_num":    tp_num,
                "close_qty": close_qty,
                "pnl":       pnl,
                "total_pnl": pos.realized_pnl,
                "closed":    is_last,
            }
        return None

    def _opp_pnl(self, pos: OpportunityPosition, exit_price: float, qty: float) -> float:
        ratio = qty / pos.quantity if pos.quantity > 0 else 0
        base  = pos.usdt_allocated * ratio
        if pos.direction == "LONG":
            return base * (exit_price - pos.entry_price) / pos.entry_price * pos.leverage
        else:
            return base * (pos.entry_price - exit_price) / pos.entry_price * pos.leverage

    def _route_profit_to_accum(self, profit: float) -> None:
        """Fırsat kârını birikim bütçesine aktar."""
        if profit > 0:
            self.accum_budget += profit
            logger.info(
                f"[Treasury] 🔄 Fırsat kârı birikime → +${profit:.2f} | "
                f"Yeni birikim bütçesi: ${self.accum_budget:.2f}"
            )

    # ──────────────────────────────────────────────────────────
    # Portföy Raporu
    # ──────────────────────────────────────────────────────────

    def portfolio_snapshot(self, prices: dict[str, float]) -> dict:
        """Tüm portföyün anlık özeti."""
        accum_val  = sum(
            p.current_value(prices.get(s, p.average_cost))
            for s, p in self.accum_positions.items()
        )
        accum_cost = sum(p.total_invested for p in self.accum_positions.values())
        realized   = sum(p.total_realized() for p in self.accum_positions.values())
        open_opps  = [p for p in self.opp_positions.values() if not p.closed]

        return {
            "kasa": {
                "birikim_butcesi":  round(self.accum_budget, 2),
                "firsat_butcesi":   round(self.opp_budget, 2),
                "nakit_rezerv":     round(self.reserve, 2),
                "toplam":           round(self.accum_budget + self.opp_budget + self.reserve, 2),
            },
            "birikim_portfoy": {
                "pozisyon_sayisi":       len(self.accum_positions),
                "toplam_deger_usdt":     round(accum_val, 2),
                "toplam_maliyet_usdt":   round(accum_cost, 2),
                "gerceklesmeyen_kar":    round(accum_val - accum_cost, 2),
                "toplam_realize_kar":    round(realized, 2),
                "pozisyonlar": {
                    s: p.summary(prices.get(s, p.average_cost))
                    for s, p in self.accum_positions.items()
                },
            },
            "firsat_portfoy": {
                "acik_pozisyon":   len(open_opps),
                "pozisyonlar": [
                    {
                        "symbol":    p.symbol,
                        "yon":       p.direction,
                        "kaldırac":  p.leverage,
                        "giris":     p.entry_price,
                        "sl":        p.stop_loss,
                        "tp_hits":   len(p.tp_hits),
                        "pnl":       round(p.realized_pnl, 2),
                    }
                    for p in open_opps
                ],
            },
        }

    def print_dashboard(self, prices: dict[str, float]) -> None:
        """Terminal'e sade özet yazdır."""
        snap = self.portfolio_snapshot(prices)
        k    = snap["kasa"]
        bp   = snap["birikim_portfoy"]
        fp   = snap["firsat_portfoy"]

        print("\n" + "═" * 60)
        print("  🏛️  KASA DURUMU")
        print(f"  Toplam     : ${k['toplam']:>10.2f}")
        print(f"  Birikim    : ${k['birikim_butcesi']:>10.2f}")
        print(f"  Fırsat     : ${k['firsat_butcesi']:>10.2f}")
        print(f"  Rezerv     : ${k['nakit_rezerv']:>10.2f}")
        print("─" * 60)
        print(f"  📦 BİRİKİM ({bp['pozisyon_sayisi']} pozisyon)")
        print(f"  Deger      : ${bp['toplam_deger_usdt']:>10.2f}")
        print(f"  Maliyet    : ${bp['toplam_maliyet_usdt']:>10.2f}")
        print(f"  Gercek.kar : ${bp['toplam_realize_kar']:>10.2f}")
        for sym, d in bp["pozisyonlar"].items():
            kar = d["gerceklesmemis_kar_pct"]
            bar = ("▲" if kar >= 0 else "▼")
            print(f"    {bar} {sym:<12} ort:{d['ortalama_maliyet']:.4f}  "
                  f"{kar:+.1f}%  ${d['deger_usdt']:.2f}")
        print("─" * 60)
        print(f"  🎯 FIRSAT ({fp['acik_pozisyon']} açık)")
        for p in fp["pozisyonlar"]:
            print(f"    {p['yon']} {p['symbol']:<10} {p['kaldırac']}x  "
                  f"TP:{p['tp_hits']}/3  PnL:${p['pnl']:.2f}")
        print("═" * 60 + "\n")
