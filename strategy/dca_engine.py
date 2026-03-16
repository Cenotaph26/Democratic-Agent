"""
dca_engine.py — Düzenli Alım & Maliyet Ortalaması Motoru

Her coin için DCA grid'i yönetir:
- İlk giriş, ek alımlar, ortalama maliyet takibi
- Futures pozisyonu için maliyet bazlı kâr hesabı
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class DCAEntry:
    """Tek bir alım kaydı."""
    symbol: str
    price: float
    quantity: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    entry_num: int = 1   # Kaçıncı alım (1=ilk, 2=ek, 3=daha derin)


@dataclass
class DCAPosition:
    """Bir coin için tüm DCA pozisyonu."""
    symbol: str
    entries: list[DCAEntry] = field(default_factory=list)

    @property
    def total_quantity(self) -> float:
        return sum(e.quantity for e in self.entries)

    @property
    def total_cost(self) -> float:
        return sum(e.price * e.quantity for e in self.entries)

    @property
    def average_cost(self) -> float:
        return self.total_cost / self.total_quantity if self.total_quantity > 0 else 0.0

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.average_cost) * self.total_quantity

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.average_cost == 0:
            return 0.0
        return (current_price - self.average_cost) / self.average_cost * 100


class DCAEngine:
    """
    Düzenli Alım Motoru.

    Strateji:
    ─────────
    Hedef pozisyon büyüklüğünü üç dilime böl:

    ALIM 1 → İlk sinyal geldiğinde → %30
    ALIM 2 → Fiyat %5 düştüğünde  → %40
    ALIM 3 → Fiyat %12 düştüğünde → %30

    Böylece ortalama maliyet düşer, risk yayılır.
    """

    # Ek alım tetikleyici seviyeleri (ilk girişe göre düşüş yüzdesi)
    ENTRY_LEVELS = [
        (0.0,  0.30),   # İlk giriş → %30
        (5.0,  0.40),   # -%5 düşüşte → %40
        (12.0, 0.30),   # -%12 düşüşte → %30
    ]

    # Kısmi kâr satış seviyeleri (ortalama maliyetten yüzde kâr)
    PROFIT_LEVELS = [
        (15.0, 0.20),   # +%15 kârda → %20 kapat
        (30.0, 0.20),   # +%30 kârda → %20 kapat
        (50.0, 0.20),   # +%50 kârda → %20 kapat
        # Kalan %40 uzun vadeli tutulur
    ]

    def __init__(self):
        self.positions: dict[str, DCAPosition] = {}

    def open_position(self, symbol: str, price: float, budget_usdt: float) -> DCAEntry:
        """
        İlk giriş — bütçenin %30'u ile başla.
        """
        first_alloc = budget_usdt * self.ENTRY_LEVELS[0][1]
        quantity = first_alloc / price

        entry = DCAEntry(symbol=symbol, price=price, quantity=quantity, entry_num=1)

        if symbol not in self.positions:
            self.positions[symbol] = DCAPosition(symbol=symbol)
        self.positions[symbol].entries.append(entry)

        logger.info(
            f"[DCA] {symbol} ilk giriş: {price:.4f} × {quantity:.4f} = ${first_alloc:.2f} USDT"
        )
        return entry

    def check_additional_entry(self, symbol: str, current_price: float, budget_usdt: float) -> Optional[DCAEntry]:
        """
        Fiyat düştükçe ek alım yap. Uygun seviye varsa DCAEntry döndür, yoksa None.
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        entry_num = pos.entry_count + 1

        if entry_num > len(self.ENTRY_LEVELS):
            logger.debug(f"[DCA] {symbol} tüm alım seviyeleri dolu.")
            return None

        required_drop_pct, allocation_pct = self.ENTRY_LEVELS[entry_num - 1]
        first_price = pos.entries[0].price
        actual_drop_pct = (first_price - current_price) / first_price * 100

        if actual_drop_pct >= required_drop_pct:
            alloc = budget_usdt * allocation_pct
            quantity = alloc / current_price
            entry = DCAEntry(
                symbol=symbol,
                price=current_price,
                quantity=quantity,
                entry_num=entry_num,
            )
            pos.entries.append(entry)
            logger.info(
                f"[DCA] {symbol} EK ALIM #{entry_num}: {current_price:.4f} × {quantity:.4f}"
                f" | Ortalama maliyet: {pos.average_cost:.4f}"
            )
            return entry

        return None

    def check_profit_take(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        Kâr hedeflerine ulaşıldığında kısmi satış miktarını hesapla.
        Returns: {'quantity': float, 'level': float} veya None
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        pnl_pct = pos.unrealized_pnl_pct(current_price)

        for level_pct, close_pct in self.PROFIT_LEVELS:
            # Bu seviye daha önce kapatıldı mı? (basit kontrol)
            tag = f"profit_{level_pct}"
            if not getattr(pos, tag, False) and pnl_pct >= level_pct:
                sell_qty = pos.total_quantity * close_pct
                object.__setattr__(pos, tag, True)
                logger.info(
                    f"[DCA] {symbol} KÂR SATIŞI +{level_pct}%: {sell_qty:.4f} adet satılıyor"
                )
                return {"quantity": sell_qty, "level": level_pct, "close_pct": close_pct}

        return None

    def get_position_summary(self, symbol: str, current_price: float) -> dict:
        """Pozisyon özetini döndür."""
        if symbol not in self.positions:
            return {}

        pos = self.positions[symbol]
        return {
            "symbol": symbol,
            "entries": pos.entry_count,
            "avg_cost": round(pos.average_cost, 6),
            "total_qty": round(pos.total_quantity, 4),
            "total_invested": round(pos.total_cost, 2),
            "current_value": round(current_price * pos.total_quantity, 2),
            "unrealized_pnl_usdt": round(pos.unrealized_pnl(current_price), 2),
            "unrealized_pnl_pct": round(pos.unrealized_pnl_pct(current_price), 2),
        }

    def close_position(self, symbol: str):
        """Pozisyonu tamamen kapat (kayıt sil)."""
        if symbol in self.positions:
            del self.positions[symbol]
            logger.info(f"[DCA] {symbol} pozisyon kapatıldı.")

    def portfolio_summary(self, prices: dict[str, float]) -> list[dict]:
        """Tüm pozisyonların özeti."""
        return [
            self.get_position_summary(sym, prices.get(sym, 0))
            for sym in self.positions
        ]
