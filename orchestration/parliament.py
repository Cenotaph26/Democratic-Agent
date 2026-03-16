"""
parliament.py — Demokratik Orkestrasyon Motoru (v3)

Yenilikler:
  - Adaptif ağırlıklar: her işlem sonrası EWA ile güncellenir
  - Hafıza entegrasyonu: kararlar DB'ye loglanır
  - news_feed: sentiment ajanına gerçek haberler inject edilir
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from agents.whale_tracker import WhaleTrackerAgent
from agents.technical_analyst import TechnicalAnalystAgent
from agents.sentiment_analyst import SentimentAnalystAgent
from agents.project_evaluator import ProjectEvaluatorAgent
from agents.risk_manager import RiskManagerAgent
from orchestration.election import ElectionSystem
from orchestration.scoreboard import Scoreboard
from orchestration.adaptive_weights import AdaptiveWeightEngine

logger = logging.getLogger(__name__)


@dataclass
class AgentVote:
    agent_name:  str
    signal:      float     # -100 ile +100
    confidence:  float     # 0.0 ile 1.0
    reasoning:   str
    veto:        bool = False


@dataclass
class ParliamentDecision:
    symbol:            str
    consensus_score:   float
    action:            str       # LONG | SHORT | WAIT
    leverage:          int
    position_size_pct: float
    entry_price:       float
    stop_loss:         float
    take_profit_levels: list[float]
    votes:             list[AgentVote]
    leader_agent:      str
    timestamp:         datetime = field(default_factory=datetime.utcnow)
    rationale:         str = ""
    db_id:             Optional[int] = None   # karar logu ID'si


class DemocraticParliament:
    """
    Beş uzman ajanın demokratik karar organı.
    Adaptif ağırlıklar + PostgreSQL hafıza entegrasyonu.
    """

    ENTRY_THRESHOLD = 40.0
    WAIT_THRESHOLD  = -20.0
    ELECTION_INTERVAL = 12

    def __init__(self, config: dict, memory=None, news_feed=None):
        self.config     = config
        self.memory     = memory      # BotMemory (opsiyonel)
        self.news_feed  = news_feed   # NewsFeed (opsiyonel)

        self.scoreboard = Scoreboard()
        self.election   = ElectionSystem(self.scoreboard)

        # Adaptif ağırlık motoru
        self.weight_engine = AdaptiveWeightEngine()

        # Ajanlar
        self.agents = {
            "whale_tracker":      WhaleTrackerAgent(config),
            "technical_analyst":  TechnicalAnalystAgent(config),
            "sentiment_analyst":  SentimentAnalystAgent(config, news_feed=news_feed),
            "project_evaluator":  ProjectEvaluatorAgent(config),
            "risk_manager":       RiskManagerAgent(config),
        }

        self.current_leader: Optional[str] = None
        self.trade_count: int = 0

    async def load_from_memory(self):
        """Başlangıçta DB'den öğrenilmiş ağırlıkları yükle."""
        if self.memory and self.memory.is_connected:
            weights = await self.memory.load_agent_weights()
            self.weight_engine.load_from_db([
                {"agent_name": k, "weight": v}
                for k, v in weights.items()
            ])
            logger.info("[Parliament] Öğrenilmiş ağırlıklar DB'den yüklendi")

    async def deliberate(self, symbol: str, market_data: dict) -> ParliamentDecision:
        """Tüm ajanları çağır, oyları topla, karar ver."""
        logger.debug(f"[Parliament] {symbol} için oylama...")

        votes = await self._collect_votes(symbol, market_data)

        # Veto kontrolü (risk ajanı)
        for vote in votes:
            if vote.veto:
                logger.warning(f"[Parliament] {vote.agent_name} VETO! {symbol} → İşlem yok")
                return self._wait_decision(symbol, votes, "VETO")

        # Adaptif ağırlıklı konsensüs
        weights  = self.weight_engine.get_weights(self.current_leader)
        consensus = self._weighted_consensus(votes, weights)
        action    = self._determine_action(consensus, votes)
        leverage  = self._calc_leverage(consensus, votes)
        pos_size  = self._calc_position_size(consensus)
        sl, tps   = self._calc_levels(market_data, action, leverage)

        decision = ParliamentDecision(
            symbol=symbol,
            consensus_score=round(consensus, 2),
            action=action,
            leverage=leverage,
            position_size_pct=pos_size,
            entry_price=market_data.get("price", 0),
            stop_loss=sl,
            take_profit_levels=tps,
            votes=votes,
            leader_agent=self.current_leader or "technical_analyst",
            rationale=self._build_rationale(votes, consensus, action, weights),
        )

        # DB'ye logla
        if self.memory and action != "WAIT":
            decision.db_id = await self.memory.log_decision(decision)

        self.scoreboard.record_decision(decision)
        self.trade_count += 1

        if self.trade_count % self.ELECTION_INTERVAL == 0:
            await self._hold_election()

        return decision

    async def record_outcome(self, decision: ParliamentDecision,
                              won: bool, pnl_pct: float):
        """
        İşlem kapandığında çağrılır.
        Ajan ağırlıklarını günceller, DB'ye yazar.
        """
        # EWA ile ağırlık güncelle
        updated_weights = self.weight_engine.update(
            votes=decision.votes, won=won, pnl_pct=pnl_pct
        )

        # DB'ye kaydet
        if self.memory:
            await self.memory.save_agent_weights(
                updated_weights,
                scores={n: self.weight_engine.stats[n].raw_score
                        for n in self.weight_engine.stats}
            )
            if decision.db_id:
                outcome = "win" if won else "loss"
                await self.memory.update_decision_outcome(
                    decision.db_id, outcome, pnl_pct
                )

        logger.info(
            f"[Parliament] Öğrenme tamamlandı | "
            f"{'✅ Kazandı' if won else '❌ Kaybetti'} {pnl_pct:+.2f}% | "
            f"{self.weight_engine.summary_line()}"
        )

    # ── İç metodlar ───────────────────────────────────────────

    async def _collect_votes(self, symbol: str, market_data: dict) -> list[AgentVote]:
        tasks = {
            name: agent.analyze(symbol, market_data)
            for name, agent in self.agents.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        votes = []
        for name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"[{name}] Ajan hatası: {result}")
                votes.append(AgentVote(name, 0.0, 0.0, "Hata"))
            else:
                votes.append(result)
        return votes

    def _weighted_consensus(self, votes: list[AgentVote],
                             weights: dict[str, float]) -> float:
        total_w, weighted_s = 0.0, 0.0
        for v in votes:
            w = weights.get(v.agent_name, 0.15) * v.confidence
            weighted_s += v.signal * w
            total_w    += w
        return weighted_s / total_w if total_w > 0 else 0.0

    def _determine_action(self, consensus: float, votes: list) -> str:
        if consensus >= self.ENTRY_THRESHOLD:
            long_votes = sum(1 for v in votes if v.signal > 0)
            return "LONG" if long_votes >= 3 else "SHORT"
        elif consensus <= self.WAIT_THRESHOLD:
            short_votes = sum(1 for v in votes if v.signal < -20)
            return "SHORT" if short_votes >= 3 else "WAIT"
        return "WAIT"

    def _calc_leverage(self, consensus: float, votes: list) -> int:
        risk_vote = next((v for v in votes if v.agent_name == "risk_manager"), None)
        base = 3
        if risk_vote:
            risk_factor = (risk_vote.signal + 100) / 200
            base = int(2 + risk_factor * 13)   # 2x - 15x
        if consensus >= 70:
            base = min(base + 3, 15)
        elif consensus < 50:
            base = max(base - 1, 2)
        return base

    def _calc_position_size(self, consensus: float) -> float:
        if consensus >= 70:
            return min(0.05, 0.02 * 2.5)
        elif consensus >= 50:
            return 0.03
        return 0.02

    def _calc_levels(self, market_data: dict, action: str, leverage: int):
        price = market_data.get("price", 0)
        atr   = market_data.get("atr", price * 0.015)
        if action == "LONG":
            sl  = price - atr * 2
            tps = [price * (1 + r) for r in (0.015, 0.03, 0.05)]
        else:
            sl  = price + atr * 2
            tps = [price * (1 - r) for r in (0.015, 0.03, 0.05)]
        return sl, tps

    def _build_rationale(self, votes, consensus, action, weights) -> str:
        lines = [f"Konsensüs: {consensus:.1f} → {action}"]
        for v in votes:
            w = weights.get(v.agent_name, 0)
            lines.append(
                f"  {v.agent_name:<22} "
                f"sinyal:{v.signal:+.0f} "
                f"güven:{v.confidence:.0%} "
                f"ağırlık:{w:.0%} — {v.reasoning[:50]}"
            )
        return "\n".join(lines)

    def _wait_decision(self, symbol, votes, reason) -> ParliamentDecision:
        return ParliamentDecision(
            symbol=symbol, consensus_score=0.0, action="WAIT",
            leverage=1, position_size_pct=0.0, entry_price=0.0,
            stop_loss=0.0, take_profit_levels=[], votes=votes,
            leader_agent=self.current_leader or "",
            rationale=f"İşlem yapılmadı: {reason}",
        )

    async def _hold_election(self):
        logger.info("[Parliament] 🗳️  Demokratik seçim başlıyor!")
        winner, loser = await self.election.run_election(self.agents)
        self.current_leader = winner
        self.weight_engine.set_leader(winner)
        logger.info(f"[Parliament] 🏆 Yeni lider: {winner}")
        if loser:
            logger.warning(f"[Parliament] ⚠️  Gözlem altı: {loser}")

    def print_weights(self):
        logger.info(f"[Parliament] Güncel ağırlıklar: {self.weight_engine.summary_line()}")
