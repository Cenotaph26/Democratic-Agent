"""
memory.py — PostgreSQL Kalıcı Hafıza Katmanı

Bot çökse, yeniden başlasa, Railway redeploy yapsa —
pozisyonlar, ajan ağırlıkları, karar geçmişi, haber önbelleği
PostgreSQL'de yaşamaya devam eder.

Railway'de PostgreSQL ekle:
  Dashboard → New → Database → PostgreSQL
  Otomatik olarak DATABASE_URL environment variable'ı enjekte edilir.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# asyncpg: async PostgreSQL sürücüsü
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    logger.warning("[Memory] asyncpg kurulu değil → pip install asyncpg")


# ──────────────────────────────────────────────────────────────
# Şema — tüm tablolar
# ──────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Birikim pozisyonları
CREATE TABLE IF NOT EXISTS accum_positions (
    symbol          TEXT PRIMARY KEY,
    tier            TEXT NOT NULL,
    leverage        INTEGER NOT NULL,
    total_budget    REAL NOT NULL,
    entries         JSONB NOT NULL DEFAULT '[]',
    profit_events   JSONB NOT NULL DEFAULT '[]',
    dip_triggered   JSONB NOT NULL DEFAULT '[]',
    profit_triggered JSONB NOT NULL DEFAULT '[]',
    next_sched_buy  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Fırsat pozisyonları
CREATE TABLE IF NOT EXISTS opp_positions (
    symbol          TEXT PRIMARY KEY,
    direction       TEXT NOT NULL,
    leverage        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    quantity        REAL NOT NULL,
    usdt_allocated  REAL NOT NULL,
    stop_loss       REAL NOT NULL,
    take_profits    JSONB NOT NULL DEFAULT '[]',
    tp_hits         JSONB NOT NULL DEFAULT '[]',
    realized_pnl    REAL NOT NULL DEFAULT 0,
    closed          BOOLEAN NOT NULL DEFAULT FALSE,
    opened_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Kasa durumu
CREATE TABLE IF NOT EXISTS treasury (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    accum_budget    REAL NOT NULL,
    opp_budget      REAL NOT NULL,
    reserve         REAL NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);

-- Ajan ağırlıkları (öğrenilen, kalıcı)
CREATE TABLE IF NOT EXISTS agent_weights (
    agent_name      TEXT PRIMARY KEY,
    weight          REAL NOT NULL,
    raw_score       REAL NOT NULL DEFAULT 0,
    trade_count     INTEGER NOT NULL DEFAULT 0,
    win_count       INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Karar geçmişi (her parliament kararı)
CREATE TABLE IF NOT EXISTS decision_log (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    consensus_score REAL NOT NULL,
    leverage        INTEGER NOT NULL,
    leader_agent    TEXT NOT NULL,
    votes           JSONB NOT NULL DEFAULT '{}',
    outcome         TEXT,         -- 'win' | 'loss' | 'pending'
    pnl_pct         REAL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Haber önbelleği (API limitini aşmamak için)
CREATE TABLE IF NOT EXISTS news_cache (
    cache_key       TEXT PRIMARY KEY,
    data            JSONB NOT NULL,
    expires_at      TIMESTAMP NOT NULL
);

-- Performans özeti (günlük snapshot)
CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date   DATE PRIMARY KEY,
    total_capital   REAL NOT NULL,
    accum_value     REAL NOT NULL,
    realized_pnl    REAL NOT NULL,
    win_rate        REAL NOT NULL,
    agent_weights   JSONB NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

# Varsayılan ajan ağırlıkları
DEFAULT_WEIGHTS = {
    "whale_tracker":      0.20,
    "technical_analyst":  0.25,
    "sentiment_analyst":  0.20,
    "project_evaluator":  0.20,
    "risk_manager":       0.15,
}


class BotMemory:
    """
    Tüm bot durumunu PostgreSQL'de saklar ve yükler.

    Kullanım:
        memory = BotMemory()
        await memory.connect()
        await memory.save_accum_position(pos)
        weights = await memory.load_agent_weights()
    """

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._db_url = os.environ.get("DATABASE_URL", "")

    # ── Bağlantı ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """PostgreSQL bağlantı havuzu oluştur, şemayı kur."""
        if not HAS_ASYNCPG:
            logger.error("[Memory] asyncpg yüklü değil!")
            return False

        if not self._db_url:
            logger.warning(
                "[Memory] DATABASE_URL bulunamadı — hafıza devre dışı.\n"
                "Railway'de PostgreSQL ekle: Dashboard → New → Database → Add PostgreSQL"
            )
            return False

        try:
            # Railway SSL gerektirir
            self._pool = await asyncpg.create_pool(
                self._db_url,
                ssl="require",
                min_size=1,
                max_size=5,
                command_timeout=30,
            )
            await self._setup_schema()
            await self._seed_defaults()
            logger.info("[Memory] ✅ PostgreSQL bağlantısı kuruldu")
            return True
        except Exception as e:
            logger.error(f"[Memory] ❌ PostgreSQL bağlantı hatası: {e}")
            return False

    async def close(self):
        if self._pool:
            await self._pool.close()

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def _setup_schema(self):
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)

    async def _seed_defaults(self):
        """İlk çalışmada varsayılan ajan ağırlıklarını ekle."""
        async with self._pool.acquire() as conn:
            for agent, weight in DEFAULT_WEIGHTS.items():
                await conn.execute("""
                    INSERT INTO agent_weights (agent_name, weight, raw_score)
                    VALUES ($1, $2, 0)
                    ON CONFLICT (agent_name) DO NOTHING
                """, agent, weight)

    # ── Pozisyon Kaydetme / Yükleme ───────────────────────────

    async def save_accum_position(self, pos) -> None:
        """Birikim pozisyonunu DB'ye yaz (upsert)."""
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO accum_positions
                        (symbol, tier, leverage, total_budget, entries,
                         profit_events, dip_triggered, profit_triggered,
                         next_sched_buy, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        entries          = EXCLUDED.entries,
                        profit_events    = EXCLUDED.profit_events,
                        dip_triggered    = EXCLUDED.dip_triggered,
                        profit_triggered = EXCLUDED.profit_triggered,
                        next_sched_buy   = EXCLUDED.next_sched_buy,
                        updated_at       = NOW()
                """,
                    pos.symbol,
                    pos.tier.value,
                    pos.leverage,
                    pos.total_budget_usdt,
                    json.dumps([
                        {"price": e.price, "quantity": e.quantity,
                         "usdt_spent": e.usdt_spent, "entry_num": e.entry_num,
                         "trigger": e.trigger,
                         "timestamp": e.timestamp.isoformat()}
                        for e in pos.entries
                    ]),
                    json.dumps([
                        {"level_pct": pe.level_pct, "sold_qty": pe.sold_qty,
                         "sold_price": pe.sold_price, "realized_usdt": pe.realized_usdt,
                         "timestamp": pe.timestamp.isoformat()}
                        for pe in pos.profit_events
                    ]),
                    json.dumps(list(pos.dip_levels_triggered)),
                    json.dumps(list(pos.profit_levels_triggered)),
                    pos.next_scheduled_buy,
                )
        except Exception as e:
            logger.error(f"[Memory] accum kaydetme hatası {pos.symbol}: {e}")

    async def load_accum_positions(self) -> list[dict]:
        """Tüm birikim pozisyonlarını DB'den yükle."""
        if not self.is_connected:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM accum_positions")
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[Memory] accum yükleme hatası: {e}")
            return []

    async def delete_accum_position(self, symbol: str) -> None:
        if not self.is_connected:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM accum_positions WHERE symbol=$1", symbol)

    async def save_opp_position(self, pos) -> None:
        """Fırsat pozisyonunu DB'ye yaz."""
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO opp_positions
                        (symbol, direction, leverage, entry_price, quantity,
                         usdt_allocated, stop_loss, take_profits, tp_hits,
                         realized_pnl, closed, opened_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        tp_hits       = EXCLUDED.tp_hits,
                        realized_pnl  = EXCLUDED.realized_pnl,
                        closed        = EXCLUDED.closed,
                        updated_at    = NOW()
                """,
                    pos.symbol, pos.direction, pos.leverage,
                    pos.entry_price, pos.quantity, pos.usdt_allocated,
                    pos.stop_loss,
                    json.dumps(pos.take_profits),
                    json.dumps(pos.tp_hits),
                    pos.realized_pnl, pos.closed, pos.opened_at,
                )
        except Exception as e:
            logger.error(f"[Memory] opp kaydetme hatası {pos.symbol}: {e}")

    async def load_opp_positions(self) -> list[dict]:
        if not self.is_connected:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM opp_positions WHERE closed=FALSE"
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[Memory] opp yükleme hatası: {e}")
            return []

    # ── Kasa ──────────────────────────────────────────────────

    async def save_treasury(self, accum: float, opp: float, reserve: float) -> None:
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO treasury (id, accum_budget, opp_budget, reserve, updated_at)
                    VALUES (1, $1, $2, $3, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        accum_budget = EXCLUDED.accum_budget,
                        opp_budget   = EXCLUDED.opp_budget,
                        reserve      = EXCLUDED.reserve,
                        updated_at   = NOW()
                """, accum, opp, reserve)
        except Exception as e:
            logger.error(f"[Memory] treasury kaydetme hatası: {e}")

    async def load_treasury(self) -> Optional[dict]:
        if not self.is_connected:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM treasury WHERE id=1")
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"[Memory] treasury yükleme hatası: {e}")
            return None

    # ── Ajan Ağırlıkları (Öğrenme) ────────────────────────────

    async def save_agent_weights(self, weights: dict[str, float],
                                  scores: dict[str, float] = None) -> None:
        """Güncellenmiş ajan ağırlıklarını DB'ye yaz."""
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                for agent, weight in weights.items():
                    raw = scores.get(agent, 0) if scores else 0
                    await conn.execute("""
                        INSERT INTO agent_weights (agent_name, weight, raw_score, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (agent_name) DO UPDATE SET
                            weight     = EXCLUDED.weight,
                            raw_score  = EXCLUDED.raw_score,
                            updated_at = NOW()
                    """, agent, weight, raw)
            logger.info(f"[Memory] Ajan ağırlıkları kaydedildi: {weights}")
        except Exception as e:
            logger.error(f"[Memory] ağırlık kaydetme hatası: {e}")

    async def load_agent_weights(self) -> dict[str, float]:
        """Öğrenilmiş ajan ağırlıklarını yükle. DB boşsa varsayılanları döndür."""
        if not self.is_connected:
            return dict(DEFAULT_WEIGHTS)
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT agent_name, weight FROM agent_weights")
                if rows:
                    return {r["agent_name"]: r["weight"] for r in rows}
        except Exception as e:
            logger.error(f"[Memory] ağırlık yükleme hatası: {e}")
        return dict(DEFAULT_WEIGHTS)

    async def update_agent_outcome(self, agent_name: str,
                                    won: bool, pnl_pct: float) -> None:
        """Bir işlem kapandığında ajanın istatistiğini güncelle."""
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    UPDATE agent_weights
                    SET trade_count = trade_count + 1,
                        win_count   = win_count + $1,
                        updated_at  = NOW()
                    WHERE agent_name = $2
                """, 1 if won else 0, agent_name)
        except Exception as e:
            logger.error(f"[Memory] outcome güncelleme hatası: {e}")

    # ── Karar Günlüğü ─────────────────────────────────────────

    async def log_decision(self, decision) -> Optional[int]:
        """Parliament kararını kaydet. Dönen ID outcome güncellemede kullanılır."""
        if not self.is_connected:
            return None
        try:
            async with self._pool.acquire() as conn:
                votes_dict = {
                    v.agent_name: {"signal": v.signal, "confidence": v.confidence}
                    for v in decision.votes
                }
                row = await conn.fetchrow("""
                    INSERT INTO decision_log
                        (symbol, action, consensus_score, leverage,
                         leader_agent, votes, outcome)
                    VALUES ($1,$2,$3,$4,$5,$6,'pending')
                    RETURNING id
                """,
                    decision.symbol, decision.action,
                    decision.consensus_score, decision.leverage,
                    decision.leader_agent, json.dumps(votes_dict),
                )
                return row["id"] if row else None
        except Exception as e:
            logger.error(f"[Memory] karar loglama hatası: {e}")
            return None

    async def update_decision_outcome(self, decision_id: int,
                                       outcome: str, pnl_pct: float) -> None:
        """İşlem kapandığında kararın sonucunu güncelle."""
        if not self.is_connected or decision_id is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    UPDATE decision_log
                    SET outcome=($1), pnl_pct=($2)
                    WHERE id=($3)
                """, outcome, pnl_pct, decision_id)
        except Exception as e:
            logger.error(f"[Memory] outcome güncelleme hatası: {e}")

    # ── Haber Önbelleği ───────────────────────────────────────

    async def get_news_cache(self, key: str) -> Optional[dict]:
        """Önbellekten haber verisi al. Süresi geçmişse None döndür."""
        if not self.is_connected:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT data FROM news_cache
                    WHERE cache_key=$1 AND expires_at > NOW()
                """, key)
                return json.loads(row["data"]) if row else None
        except Exception as e:
            logger.error(f"[Memory] cache okuma hatası: {e}")
            return None

    async def set_news_cache(self, key: str, data: dict,
                              ttl_minutes: int = 15) -> None:
        """Haber verisini önbelleğe yaz."""
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                expires = datetime.utcnow() + timedelta(minutes=ttl_minutes)
                await conn.execute("""
                    INSERT INTO news_cache (cache_key, data, expires_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        data = EXCLUDED.data,
                        expires_at = EXCLUDED.expires_at
                """, key, json.dumps(data), expires)
        except Exception as e:
            logger.error(f"[Memory] cache yazma hatası: {e}")

    # ── Günlük Snapshot ───────────────────────────────────────

    async def save_daily_snapshot(self, total_capital: float, accum_value: float,
                                   realized_pnl: float, win_rate: float,
                                   agent_weights: dict) -> None:
        if not self.is_connected:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO daily_snapshots
                        (snapshot_date, total_capital, accum_value,
                         realized_pnl, win_rate, agent_weights)
                    VALUES (CURRENT_DATE, $1, $2, $3, $4, $5)
                    ON CONFLICT (snapshot_date) DO UPDATE SET
                        total_capital = EXCLUDED.total_capital,
                        accum_value   = EXCLUDED.accum_value,
                        realized_pnl  = EXCLUDED.realized_pnl,
                        win_rate      = EXCLUDED.win_rate,
                        agent_weights = EXCLUDED.agent_weights
                """,
                    total_capital, accum_value, realized_pnl,
                    win_rate, json.dumps(agent_weights),
                )
        except Exception as e:
            logger.error(f"[Memory] snapshot hatası: {e}")

    async def get_performance_history(self, days: int = 30) -> list[dict]:
        """Son N günün performans geçmişini çek."""
        if not self.is_connected:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM daily_snapshots
                    ORDER BY snapshot_date DESC
                    LIMIT $1
                """, days)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[Memory] performans geçmişi hatası: {e}")
            return []
