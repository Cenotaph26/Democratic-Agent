"""
adaptive_weights.py — Adaptif Ajan Ağırlık Motoru

Exponentially Weighted Average (EWA) algoritması kullanarak
her ajanın ağırlığını başarısına göre otomatik ayarlar.

Nasıl çalışır:
  1. Her işlem kapandığında kazanan/kaybeden belli olur
  2. O işlemde hangi ajan ne yönde oy verdi bilinir
  3. Doğru oyu veren ajan → ağırlığı artar
  4. Yanlış oyu veren ajan → ağırlığı azalır
  5. Değişim oranı: büyük kayıp/kâr → büyük ağırlık değişimi
  6. Decay: zamanla eski hatalar affedilir (unutma katsayısı)

Sınırlar:
  - Hiçbir ajan %5'in altına düşemez (sesi kesilmez)
  - Hiçbir ajan %40'ın üstüne çıkamaz (tekeli olmaz)
  - Risk ajanının veto hakkı ağırlıktan bağımsız korunur
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

AGENTS = [
    "whale_tracker",
    "technical_analyst",
    "sentiment_analyst",
    "project_evaluator",
    "risk_manager",
]

# Ağırlık sınırları
MIN_WEIGHT = 0.05   # Hiçbir ajan %5'in altına inemez
MAX_WEIGHT = 0.40   # Hiçbir ajan %40'ın üstüne çıkamaz

# EWA parametreleri
LEARNING_RATE  = 0.08   # Her işlemde ne kadar öğrensin (0.01-0.15 arası mantıklı)
DECAY_FACTOR   = 0.995  # Her işlemde eski "ham skor" ne kadar azalsın (unutma)
REWARD_SCALE   = 0.5    # PnL'nin ödül/cezaya dönüşüm katsayısı

# Lider bonusu: seçim kazanan ajanın geçici ağırlık çarpanı
LEADER_BONUS   = 1.60   # x1.6 → maks %40 sınırıyla birlikte işler


@dataclass
class AgentStats:
    name: str
    weight: float
    raw_score: float = 0.0      # Ham EWA puanı (negatif olabilir)
    trade_count: int = 0
    win_count: int = 0
    last_updated: datetime = field(default_factory=datetime.utcnow)

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.win_count / self.trade_count

    @property
    def win_rate_pct(self) -> float:
        return round(self.win_rate * 100, 1)


class AdaptiveWeightEngine:
    """
    Ajan ağırlıklarını dinamik olarak yönetir.

    Kullanım:
        engine = AdaptiveWeightEngine(initial_weights)
        engine.update(votes, won=True, pnl_pct=5.2)
        weights = engine.get_weights()
    """

    def __init__(self, initial_weights: Optional[dict[str, float]] = None):
        defaults = {
            "whale_tracker":      0.20,
            "technical_analyst":  0.25,
            "sentiment_analyst":  0.20,
            "project_evaluator":  0.20,
            "risk_manager":       0.15,
        }
        base = initial_weights or defaults

        self.stats: dict[str, AgentStats] = {
            name: AgentStats(name=name, weight=base.get(name, 0.15))
            for name in AGENTS
        }
        self.current_leader: Optional[str] = None
        self.total_updates: int = 0

    # ── Öğrenme güncelleme ────────────────────────────────────

    def update(self, votes: list, won: bool, pnl_pct: float) -> dict[str, float]:
        """
        Bir işlem kapandığında ağırlıkları güncelle.

        votes: list[AgentVote] — her ajanın bu işlemdeki oyu
        won:   bool — işlem kazanıldı mı
        pnl_pct: float — gerçekleşen kâr/zarar yüzdesi

        Döndürür: güncellenmiş ağırlık sözlüğü
        """
        self.total_updates += 1

        # Ödül/ceza büyüklüğü: PnL'ye bağlı, REWARD_SCALE ile ölçeklendi
        reward_magnitude = min(abs(pnl_pct) * REWARD_SCALE, 10.0)

        vote_map = {v.agent_name: v.signal for v in votes}

        for agent_name, stats in self.stats.items():
            signal = vote_map.get(agent_name, 0.0)
            signal_dir = 1.0 if signal > 0 else (-1.0 if signal < 0 else 0.0)

            # Decay — eski puanlar zamanla solar
            stats.raw_score *= DECAY_FACTOR

            # Doğru oy → puan kazan, yanlış oy → puan kaybet
            if won:
                # Kazanıldı: pozitif oy veren iyi yaptı
                delta = signal_dir * reward_magnitude
            else:
                # Kaybedildi: pozitif oy veren hata yaptı
                delta = -signal_dir * reward_magnitude

            stats.raw_score += LEARNING_RATE * delta
            stats.trade_count += 1
            if won:
                stats.win_count += 1
            stats.last_updated = datetime.utcnow()

        # Ham puanları ağırlığa dönüştür
        self._normalize_weights()

        logger.info(
            f"[Weights] {'✅ Kazandı' if won else '❌ Kaybetti'} "
            f"({pnl_pct:+.2f}%) → Ağırlıklar güncellendi | "
            f"Toplam güncelleme: {self.total_updates}"
        )
        self._log_weights()

        return self.get_weights()

    def _normalize_weights(self):
        """
        Ham puanları softmax'a benzer şekilde normalize et.
        Sınırları uygula, toplamı 1.0'a getir.
        """
        # Softmax: negatif puanlar düşük ağırlık, pozitifler yüksek
        raw_vals = [s.raw_score for s in self.stats.values()]
        max_raw  = max(raw_vals) if raw_vals else 0

        exp_vals = {}
        for name, stats in self.stats.items():
            # Softmax (sayısal kararlılık için max çıkar)
            exp_vals[name] = math.exp(
                max(-10, min(10, (stats.raw_score - max_raw) * 2))
            )

        total_exp = sum(exp_vals.values()) or 1.0

        # Normalize + sınır uygula
        for name, stats in self.stats.items():
            raw_w = exp_vals[name] / total_exp
            # Sınır uygula
            stats.weight = max(MIN_WEIGHT, min(MAX_WEIGHT, raw_w))

        # Sınır uygulandı, toplam artık 1.0 olmayabilir → yeniden normalize
        total_w = sum(s.weight for s in self.stats.values())
        if total_w > 0:
            for stats in self.stats.values():
                stats.weight = round(stats.weight / total_w, 4)

    # ── Lider bonusu ──────────────────────────────────────────

    def get_weights(self, leader: Optional[str] = None) -> dict[str, float]:
        """
        Güncel ağırlıkları döndür.
        Lider varsa onun ağırlığına bonus uygula (yeniden normalize edilir).
        """
        ldr = leader or self.current_leader
        weights = {name: s.weight for name, s in self.stats.items()}

        if ldr and ldr in weights:
            # Lider bonusu uygula
            weights[ldr] = min(weights[ldr] * LEADER_BONUS, MAX_WEIGHT)
            # Kalan ağırlıkları yeniden normalize et
            total = sum(weights.values())
            if total > 0:
                weights = {k: round(v / total, 4) for k, v in weights.items()}

        return weights

    def set_leader(self, leader: str):
        """Demokratik seçim sonucunda lider güncelle."""
        self.current_leader = leader
        logger.info(f"[Weights] 🏆 Yeni lider: {leader} (ağırlık bonusu x{LEADER_BONUS})")

    # ── İstatistik ────────────────────────────────────────────

    def get_stats(self) -> dict[str, dict]:
        """Her ajanın detaylı istatistiği."""
        return {
            name: {
                "weight":      round(s.weight * 100, 1),
                "raw_score":   round(s.raw_score, 3),
                "trade_count": s.trade_count,
                "win_rate":    s.win_rate_pct,
                "leader":      name == self.current_leader,
            }
            for name, s in self.stats.items()
        }

    def load_from_db(self, db_rows: list[dict]):
        """DB'den yüklenen ağırlık verisiyle güncelle."""
        for row in db_rows:
            name = row.get("agent_name")
            if name in self.stats:
                self.stats[name].weight      = row.get("weight", self.stats[name].weight)
                self.stats[name].raw_score   = row.get("raw_score", 0.0)
                self.stats[name].trade_count = row.get("trade_count", 0)
                self.stats[name].win_count   = row.get("win_count", 0)
        logger.info("[Weights] DB'den öğrenilmiş ağırlıklar yüklendi")
        self._log_weights()

    def _log_weights(self):
        parts = [
            f"{name[:6]}:{s.weight*100:.0f}%({s.win_rate_pct:.0f}%W)"
            for name, s in self.stats.items()
        ]
        logger.info(f"[Weights] {' | '.join(parts)}")

    def summary_line(self) -> str:
        weights = self.get_weights()
        return " | ".join(
            f"{'★' if n == self.current_leader else ' '}{n[:5]}:{w*100:.0f}%"
            for n, w in weights.items()
        )
