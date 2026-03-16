"""
settings.py — Sistem Konfigürasyonu

Tüm parametreler buradan yönetilir.
Hassas bilgiler (API anahtarları) .env dosyasından yüklenir.
"""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "secrets.env"))


CONFIG = {
    # ── Çalışma modu ──────────────────────────────────────────────
    "mode": os.getenv("BOT_MODE", "paper"),   # paper | live | scan

    # ── Binance API ───────────────────────────────────────────────
    "binance_api_key":    os.getenv("BINANCE_API_KEY", ""),
    "binance_api_secret": os.getenv("BINANCE_API_SECRET", ""),

    # ── Tarama ────────────────────────────────────────────────────
    "scan_interval_seconds": 60,
    "max_candidates_per_scan": 50,
    "min_volume_24h_usd": 5_000_000,
    "min_project_score": 50,

    # ── Risk limitleri ────────────────────────────────────────────
    "max_leverage": 25,
    "max_positions": 10,
    "per_trade_risk_pct": 2.0,        # Kasanın %2'si
    "max_position_pct": 5.0,          # Tek coin maks %5
    "daily_loss_limit_pct": 5.0,      # Günlük %5 zarar → dur
    "weekly_loss_limit_pct": 10.0,    # Haftalık %10 → manuel onay

    # ── DCA parametreleri ─────────────────────────────────────────
    "dca_entry_drops": [0.0, 5.0, 12.0],          # % düşüş tetikleyicileri
    "dca_entry_allocations": [0.30, 0.40, 0.30],  # Bütçe dağılımı
    "dca_profit_levels": [15.0, 30.0, 50.0],      # % kâr seviyeleri
    "dca_profit_close_pcts": [0.20, 0.20, 0.20],  # Kaçını kapat

    # ── Demokratik orkestrasyon ───────────────────────────────────
    "election_interval": 12,          # Kaç işlemde bir seçim
    "leader_weight_bonus": 1.75,      # Lider ajan ağırlık çarpanı
    "entry_consensus_threshold": 40.0,
    "wait_consensus_threshold": -20.0,

    # ── Bildirimler ───────────────────────────────────────────────
    "telegram_token":   os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),

    # ── Paper trading ─────────────────────────────────────────────
    "paper_initial_balance_usdt": 10_000.0,
}
