"""
main.py — Ana Döngü v3 (Hafıza + Öğrenme + Gerçek Haberler)

Başlangıç akışı:
  1. PostgreSQL'e bağlan
  2. DB'den pozisyonları, ağırlıkları yükle (kaldığı yerden devam)
  3. NewsFeed başlat (CryptoPanic + Fear & Greed)
  4. Binance Testnet'e bağlan
  5. Ana döngü: tarama → oylama → emir → öğrenme → kaydet

Modlar:
  paper   → simülasyon, DB yine de kullanılır
  testnet → Binance Testnet + DB
  live    → Gerçek Binance + DB
"""

import asyncio
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/bot_{datetime.utcnow().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger("main")


async def run(mode: str, config: dict):
    from utils.memory import BotMemory
    from data.news_feed import NewsFeed
    from orchestration.parliament import DemocraticParliament
    from strategy.position_engine import PositionEngine
    from data.market_scanner import MarketScanner
    from execution.binance_client import BinanceFuturesClient

    # ── 1. PostgreSQL Hafıza ───────────────────────────────────
    memory = BotMemory()
    db_ok  = await memory.connect()
    if not db_ok:
        logger.warning("[Main] DB yok → hafıza devre dışı (bot çalışmaya devam eder)")

    # ── 2. Gerçek Haber Akışı ─────────────────────────────────
    news_feed = NewsFeed(memory=memory)
    await news_feed.start()
    logger.info("[Main] NewsFeed başlatıldı (CryptoPanic + Fear & Greed)")

    # ── 3. Binance Bağlantısı ─────────────────────────────────
    client  = BinanceFuturesClient(config)
    real_bal = await client.get_account_balance()
    capital  = (
        real_bal if mode in ("testnet", "live") and real_bal > 0
        else config["initial_capital_usdt"]
    )

    # ── 4. Diğer Bileşenler ───────────────────────────────────
    parliament = DemocraticParliament(config, memory=memory, news_feed=news_feed)
    scanner    = MarketScanner(config)
    scanner.set_client(client)

    # ── 5. DB'den kaldığı yeri yükle ──────────────────────────
    treasury_row = await memory.load_treasury() if db_ok else None
    if treasury_row:
        accum_b = treasury_row["accum_budget"]
        opp_b   = treasury_row["opp_budget"]
        reserve = treasury_row["reserve"]
        capital = accum_b + opp_b + reserve
        logger.info(
            f"[Main] 💾 Kasa DB'den yüklendi | "
            f"Toplam: ${capital:.2f} | "
            f"Birikim: ${accum_b:.2f} | Fırsat: ${opp_b:.2f}"
        )
        engine = PositionEngine(capital)
        engine.accum_budget = accum_b
        engine.opp_budget   = opp_b
        engine.reserve      = reserve
    else:
        engine = PositionEngine(capital)

    # Ajan ağırlıklarını yükle
    await parliament.load_from_memory()

    # Birikim pozisyonlarını yükle
    accum_rows = await memory.load_accum_positions() if db_ok else []
    loaded_accum = _restore_accum_positions(engine, accum_rows)
    if loaded_accum:
        logger.info(f"[Main] 💾 {len(loaded_accum)} birikim pozisyonu DB'den geri yüklendi: {loaded_accum}")

    # Fırsat pozisyonlarını yükle
    opp_rows = await memory.load_opp_positions() if db_ok else []
    loaded_opp = _restore_opp_positions(engine, opp_rows)
    if loaded_opp:
        logger.info(f"[Main] 💾 {len(loaded_opp)} fırsat pozisyonu DB'den geri yüklendi: {loaded_opp}")

    logger.info(
        f"\n{'═'*58}\n"
        f"  🏛️  Demokratik Trading Bot v3 — {client.mode_label}\n"
        f"  Sermaye  : ${capital:.2f} USDT\n"
        f"  Hafıza   : {'✅ PostgreSQL' if db_ok else '⚠️  Devre Dışı'}\n"
        f"  Haberler : ✅ CryptoPanic + Fear & Greed\n"
        f"  Öğrenme  : ✅ EWA Adaptif Ağırlıklar\n"
        f"  Birikim  : {len(engine.accum_positions)} pozisyon yüklü\n"
        f"{'═'*58}"
    )

    scan_interval   = config.get("scan_interval_seconds", 60)
    tick_interval   = config.get("tick_interval_seconds", 10)
    scan_counter    = 0
    last_dashboard  = datetime.utcnow()
    last_snapshot   = datetime.utcnow()
    current_prices: dict = {}

    logger.info("✅ Tüm bileşenler hazır. Ana döngü başlıyor...\n")

    while True:
        try:
            # ── A. Güncel fiyatlar ─────────────────────────────
            watch = (
                list(engine.accum_positions.keys()) +
                [s for s, p in engine.opp_positions.items() if not p.closed]
            )
            if watch:
                current_prices = await scanner.get_prices(watch)

            # ── B. Fırsat TP/SL kontrolleri ───────────────────
            opp_exits = engine.tick_opportunity_exits(current_prices)
            for ev in opp_exits:
                if mode in ("testnet", "live"):
                    await _execute_opp_exit(client, ev)
                _log_exit(ev)

                # Çıkış oldu → parliament'a öğret
                # (Basitleştirilmiş: exit event'ten decision bulmak gerekir)
                # Tam implementasyonda: aktif_decisions[symbol] tutulur
                await memory.save_treasury(
                    engine.accum_budget, engine.opp_budget, engine.reserve
                )

            # ── C. Birikim kontrolleri ─────────────────────────
            accum_actions = engine.tick_accum_checks(current_prices)
            for act in accum_actions:
                if mode in ("testnet", "live"):
                    await _execute_accum_action(client, act, engine)
                elif mode == "paper":
                    price = current_prices.get(act["symbol"], 0)
                    logger.info(
                        f"[PAPER] {act['type'].upper()} {act['symbol']} @ {price:.4f}"
                    )
                # Birikim pozisyonunu DB'ye kaydet
                pos = engine.accum_positions.get(act["symbol"])
                if pos and db_ok:
                    await memory.save_accum_position(pos)

            # ── D. Yeni coin taraması ──────────────────────────
            if scan_counter % max(1, scan_interval // tick_interval) == 0:
                candidates = await scanner.get_candidates()
                logger.info(f"[Scanner] {len(candidates)} aday işleniyor...")

                active_decisions: dict = {}   # symbol → decision (öğrenme için)

                for symbol, market_data in candidates.items():

                    decision = await parliament.deliberate(symbol, market_data)
                    result   = engine.route_parliament_decision(decision, market_data)

                    if decision.action != "WAIT":
                        active_decisions[symbol] = decision

                    # — Birikim girişi —
                    if result["accum_action"] and result["accum_entry"]:
                        entry = result["accum_entry"]
                        pos   = engine.accum_positions.get(symbol)
                        logger.info(
                            f"[Main] 📦 {symbol} BİRİKİM {result['accum_action'].upper()} | "
                            f"{pos.tier.value if pos else '?'} | "
                            f"{pos.leverage if pos else '?'}x | "
                            f"${entry.usdt_spent:.2f} @ {entry.price:.4f}"
                        )
                        if mode in ("testnet", "live") and pos:
                            await client.open_long(
                                symbol, entry.quantity, pos.leverage,
                                entry.price * 0.80, []
                            )
                        if pos and db_ok:
                            await memory.save_accum_position(pos)

                    # — Fırsat pozisyonu —
                    if result["opp_action"] and result["opp_position"]:
                        opp = result["opp_position"]
                        logger.info(
                            f"[Main] 🎯 {symbol} FIRSAT {opp.direction} | "
                            f"{opp.leverage}x | ${opp.usdt_allocated:.2f} | "
                            f"Konsensüs:{decision.consensus_score:.1f} | "
                            f"Lider:{decision.leader_agent}"
                        )
                        if mode in ("testnet", "live"):
                            if opp.direction == "LONG":
                                await client.open_long(
                                    symbol, opp.quantity, opp.leverage,
                                    opp.stop_loss, opp.take_profits
                                )
                            else:
                                await client.open_short(
                                    symbol, opp.quantity, opp.leverage,
                                    opp.stop_loss, opp.take_profits
                                )
                        if db_ok:
                            await memory.save_opp_position(opp)

                    # — Kâr satışı —
                    if result["profit_action"]:
                        p = result["profit_action"]
                        logger.info(
                            f"[Main] 💰 {symbol} KÂR SATIŞI +{p['level_pct']:.0f}% | "
                            f"{p['sell_qty']:.4f} adet | ${p['realized_usdt']:.2f}"
                        )
                        if mode in ("testnet", "live"):
                            await client.close_partial(symbol, p["sell_qty"], "LONG")
                        pos = engine.accum_positions.get(symbol)
                        if pos and db_ok:
                            await memory.save_accum_position(pos)

                # Kasa durumunu DB'ye yaz
                if db_ok:
                    await memory.save_treasury(
                        engine.accum_budget, engine.opp_budget, engine.reserve
                    )

            # ── E. Dashboard (5 dakikada bir) ─────────────────
            now = datetime.utcnow()
            if (now - last_dashboard).seconds >= 300:
                engine.print_dashboard(current_prices)
                parliament.scoreboard.print_leaderboard()
                parliament.print_weights()
                last_dashboard = now

            # ── F. Günlük snapshot (24 saatte bir) ────────────
            if (now - last_snapshot) >= timedelta(hours=24):
                snap = engine.portfolio_snapshot(current_prices)
                await memory.save_daily_snapshot(
                    total_capital=snap["kasa"]["toplam"],
                    accum_value=snap["birikim_portfoy"]["toplam_deger_usdt"],
                    realized_pnl=snap["birikim_portfoy"]["toplam_realize_kar"],
                    win_rate=0.0,   # Scoreboard'dan hesaplanabilir
                    agent_weights=parliament.weight_engine.get_weights(),
                )
                logger.info("[Main] 📸 Günlük snapshot kaydedildi")
                last_snapshot = now

        except KeyboardInterrupt:
            logger.info("\n⏹  Bot durduruldu.")
            engine.print_dashboard(current_prices)
            if db_ok:
                await memory.save_treasury(
                    engine.accum_budget, engine.opp_budget, engine.reserve
                )
            await news_feed.close()
            await memory.close()
            break
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}", exc_info=True)

        scan_counter += 1
        await asyncio.sleep(tick_interval)


# ── DB'den pozisyon geri yükleme ──────────────────────────────

def _restore_accum_positions(engine, rows: list[dict]) -> list[str]:
    """DB satırlarından AccumPosition nesnelerini yeniden oluştur."""
    import json
    from datetime import datetime
    from strategy.position_engine import AccumPosition, AccumEntry, ProfitEvent, AccumTier

    restored = []
    for row in rows:
        try:
            tier = AccumTier.MAJOR if row["tier"] == "majör" else AccumTier.PROJECT
            entries = [
                AccumEntry(
                    price=e["price"], quantity=e["quantity"],
                    usdt_spent=e["usdt_spent"], entry_num=e["entry_num"],
                    trigger=e.get("trigger", "initial"),
                    timestamp=datetime.fromisoformat(e["timestamp"]),
                )
                for e in (json.loads(row["entries"]) if isinstance(row["entries"], str) else row["entries"])
            ]
            profit_events = [
                ProfitEvent(
                    level_pct=pe["level_pct"], sold_qty=pe["sold_qty"],
                    sold_price=pe["sold_price"], realized_usdt=pe["realized_usdt"],
                    timestamp=datetime.fromisoformat(pe["timestamp"]),
                )
                for pe in (json.loads(row["profit_events"]) if isinstance(row["profit_events"], str) else row["profit_events"])
            ]
            dip_t   = json.loads(row["dip_triggered"]) if isinstance(row["dip_triggered"], str) else row["dip_triggered"]
            prof_t  = json.loads(row["profit_triggered"]) if isinstance(row["profit_triggered"], str) else row["profit_triggered"]

            pos = AccumPosition(
                symbol=row["symbol"], tier=tier,
                leverage=row["leverage"], total_budget_usdt=row["total_budget"],
                entries=entries, profit_events=profit_events,
                dip_levels_triggered=set(dip_t),
                profit_levels_triggered=set(prof_t),
                next_scheduled_buy=row["next_sched_buy"],
            )
            engine.accum_positions[row["symbol"]] = pos
            restored.append(row["symbol"])
        except Exception as e:
            logger.error(f"[Main] {row.get('symbol','?')} yükleme hatası: {e}")
    return restored


def _restore_opp_positions(engine, rows: list[dict]) -> list[str]:
    """DB satırlarından OpportunityPosition nesnelerini yeniden oluştur."""
    import json
    from strategy.position_engine import OpportunityPosition

    restored = []
    for row in rows:
        try:
            tps = json.loads(row["take_profits"]) if isinstance(row["take_profits"], str) else row["take_profits"]
            hits = json.loads(row["tp_hits"]) if isinstance(row["tp_hits"], str) else row["tp_hits"]
            pos = OpportunityPosition(
                symbol=row["symbol"], direction=row["direction"],
                leverage=row["leverage"], entry_price=row["entry_price"],
                quantity=row["quantity"], usdt_allocated=row["usdt_allocated"],
                stop_loss=row["stop_loss"], take_profits=tps,
                tp_hits=hits, realized_pnl=row["realized_pnl"],
                closed=row["closed"], opened_at=row["opened_at"],
            )
            engine.opp_positions[row["symbol"]] = pos
            restored.append(row["symbol"])
        except Exception as e:
            logger.error(f"[Main] Opp pozisyon yükleme hatası: {e}")
    return restored


# ── Yardımcı yürütme fonksiyonları ────────────────────────────

async def _execute_opp_exit(client, event: dict):
    try:
        if event["action"] == "stop_loss":
            await client.close_partial(event["symbol"], 999999, "BOTH")
        elif event["action"] == "take_profit":
            await client.close_partial(event["symbol"], event["close_qty"], "LONG")
    except Exception as e:
        logger.error(f"[Execution] {event['symbol']} çıkış hatası: {e}")


async def _execute_accum_action(client, action: dict, engine):
    try:
        if action["type"] in ("dip_buy", "scheduled_buy"):
            entry = action["entry"]
            pos   = engine.accum_positions.get(action["symbol"])
            if pos:
                await client.open_long(
                    action["symbol"], entry.quantity,
                    pos.leverage, entry.price * 0.80, []
                )
        elif action["type"] == "profit_sell":
            await client.close_partial(action["symbol"], action["sell_qty"], "LONG")
    except Exception as e:
        logger.error(f"[Execution] Birikim aksiyon hatası: {e}")


def _log_exit(ev: dict):
    if ev["action"] == "stop_loss":
        logger.warning(f"[Exit] ❌ {ev['symbol']} SL | PnL:${ev['pnl']:.2f}")
    elif ev["action"] == "take_profit":
        logger.info(
            f"[Exit] ✅ {ev['symbol']} TP{ev['tp_num']}/3 | "
            f"PnL:${ev['pnl']:.2f} | Toplam:${ev['total_pnl']:.2f}"
            + (" ✔ Kapatıldı" if ev.get("closed") else "")
        )


# ── CLI ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Demokratik Trading Bot v3")
    parser.add_argument("--mode", choices=["paper","testnet","live"],
                        default=os.environ.get("BOT_MODE","paper"))
    parser.add_argument("--capital", type=float,
                        default=float(os.environ.get("INITIAL_CAPITAL","1000")))
    parser.add_argument("--scan-interval", type=int,
                        default=int(os.environ.get("SCAN_INTERVAL","60")))
    parser.add_argument("--tick-interval", type=int,
                        default=int(os.environ.get("TICK_INTERVAL","10")))
    args = parser.parse_args()

    config = {
        "mode":                    args.mode,
        "initial_capital_usdt":    args.capital,
        "scan_interval_seconds":   args.scan_interval,
        "tick_interval_seconds":   args.tick_interval,
        "min_volume_24h_usd":      5_000_000,
        "min_project_score":       50,
        "accum_min_project_score": 65,
        "opp_min_consensus":       55,
        "whale_min_transfer_usd":  500_000,
        "daily_loss_limit_pct":    5.0,
        "weekly_loss_limit_pct":   10.0,
        "election_interval":       12,
    }

    os.makedirs("logs", exist_ok=True)

    icons = {"paper":"📄","testnet":"🧪","live":"⚡"}
    print(f"""
╔══════════════════════════════════════════════════════╗
║   🏛️  Demokratik Trading Bot v3                     ║
╠══════════════════════════════════════════════════════╣
║  Mod      : {icons.get(args.mode,'')} {args.mode:<38}║
║  Sermaye  : ${args.capital:<39.2f}║
╠══════════════════════════════════════════════════════╣
║  💾 Hafıza    : PostgreSQL (7/24 kalıcı)            ║
║  📰 Haberler  : CryptoPanic + Fear & Greed           ║
║  🧠 Öğrenme   : EWA Adaptif Ajan Ağırlıkları        ║
║  📦 Birikim   : %60 sermaye                         ║
║  🎯 Fırsat    : %25 sermaye                         ║
║  💵 Rezerv    : %15 sermaye                         ║
╚══════════════════════════════════════════════════════╝
    """)

    asyncio.run(run(args.mode, config))


if __name__ == "__main__":
    main()
