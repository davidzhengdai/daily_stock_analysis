# -*- coding: utf-8 -*-
"""
热点雷达 (NewsHeatRadar)

独立的短线新闻驱动扫描器：
  1. 并发查询 8 个短线新闻维度（24-48h 窗口）
  2. LLM 识别热门板块（HotSector）及 heat_score
  3. 对 top 5 板块各自 LLM 提名 3 只股票（HotPick）
  4. 按 market 过滤 → 去重 → 取 top_n
  5. 保存结果 → 通知 → 写入 DiscoveryList
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import get_config
from src.schemas.news_heat_radar import HeatConfig, HeatMeta, HeatReport, HotPick, HotSector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 短线新闻查询维度（24-48h 窗口，有别于 ThemeDetector 的 7天宏观）
# ---------------------------------------------------------------------------
_HEAT_RADAR_QUERIES = [
    "breaking stock market news sector catalyst today",
    "government policy regulation announcement stock sector impact this week",
    "earnings surprise guidance raised revenue beat sector stocks",
    "A股热门概念板块题材资金流向 近两日",
    "Federal Reserve economic data jobs report GDP market reaction sectors",
    "sector rotation money flow hot sector rally momentum today",
    "supply chain disruption trade announcement sector winners losers",
    "technology breakthrough product launch semiconductor AI chip stocks news",
]

_HEAT_RADAR_CN_QUERIES = [
    "A股热门概念板块题材资金流向 近两日",
    "A股 政策 利好 板块 催化剂 今天",
    "A股 业绩预增 订单突破 热门行业 近两日",
]

_HEAT_RADAR_US_QUERIES = [
    "breaking stock market news sector catalyst today",
    "earnings surprise guidance raised revenue beat sector stocks",
    "Federal Reserve economic data jobs report GDP market reaction sectors",
    "sector rotation money flow hot sector rally momentum today",
    "technology breakthrough product launch semiconductor AI chip stocks news",
]

# ---------------------------------------------------------------------------
# LLM Prompts
# ---------------------------------------------------------------------------
_HEAT_SECTOR_PROMPT = """\
You are a short-term market intelligence analyst (intraday to 5-day horizon).
Today is {date}. Analyze the following 24-48 hour news digest:

--- RECENT NEWS ---
{news_digest}
--- END NEWS ---

Identify the top {count} ACTIVELY MOVING market sectors where capital is flowing
RIGHT NOW based on the above news. Focus on short-term catalysts (days to a week),
not 6-month investment themes.

Measure "heat_score" (0-100) by: estimated news article count × market impact weight.
Ignore sectors with only one low-impact mention.

Return ONLY valid JSON array, no markdown fences, no explanation:
[
  {{
    "name": "Sector Name (3-6 words)",
    "heat_score": 85,
    "news_velocity": 12,
    "catalyst_summary": "One or two sentences describing the immediate catalyst.",
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "market_regions": ["us", "cn"],
    "sentiment": "bullish",
    "source_queries": ["relevant query used"]
  }}
]"""

_HEAT_STOCK_PROMPT = """\
Short-term trade signal generator (5 trading-day horizon).
Sector catalyst: {sector_name}
Catalyst summary: {catalyst_summary}
Keywords: {keywords}
Target markets: {markets}

Identify {n} specific publicly traded stocks (from {markets} markets) with DIRECT
exposure to this EXACT catalyst that are likely to move in the NEXT 5 TRADING DAYS.

Scoring criteria:
  1. Direct business exposure to catalyst (NOT peripheral/conglomerate)
  2. Recent price momentum or reversal setup
  3. Adequate liquidity (avoid micro-caps < 100M market cap)

For A-share stocks use numeric codes (e.g., 600519, 300750).
For US stocks use tickers (e.g., NVDA, TSLA).

Return ONLY valid JSON array, no markdown fences:
[
  {{
    "ticker": "NVDA",
    "name": "NVIDIA Corporation",
    "market": "US",
    "sector": "Semiconductors",
    "current_price": 0.0,
    "heat_score": 78,
    "llm_confidence": 72,
    "catalyst_thesis": "Direct GPU demand from AI data center buildout (max 100 chars).",
    "entry_window": "days 1-3",
    "key_risks": "Valuation stretch; macro pullback risk."
  }}
]"""


def _resolve_results_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).parent / "scanner_cache" / "heat_results"


_RESULTS_DIR = _resolve_results_dir()


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class NewsHeatRadar:
    """短线热点雷达：新闻密度 → 热门板块 → 具体股票。"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._lock = threading.Lock()
        self._progress: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_heat_scan(self, heat_config: Optional[HeatConfig] = None) -> str:
        """在后台线程中启动热点扫描，立即返回 run_id。"""
        cfg = heat_config or HeatConfig()
        run_id = uuid.uuid4().hex[:12]
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._progress[run_id] = {"progress": 0, "message": "启动中…", "status": "running"}
        thread = threading.Thread(
            target=self._run,
            args=(run_id, cfg),
            daemon=True,
            name=f"heat-radar-{run_id}",
        )
        thread.start()
        return run_id

    def get_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._progress.get(run_id, {}))

    def get_result(self, run_id: str) -> Optional[HeatReport]:
        path = _RESULTS_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._dict_to_report(data)
        except Exception as exc:
            logger.warning("[HeatRadar] 读取结果失败 %s: %s", run_id, exc)
            return None

    def get_latest_result(self) -> Optional[HeatReport]:
        files = sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("status") == "completed":
                    return self._dict_to_report(data)
            except Exception:
                continue
        return None

    def list_runs(self) -> List[HeatMeta]:
        metas: List[HeatMeta] = []
        for f in sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                sectors = data.get("hot_sectors", [])
                picks = data.get("hot_picks", [])
                metas.append(HeatMeta(
                    run_id=data["run_id"],
                    timestamp=data["timestamp"],
                    top_sector=sectors[0]["name"] if sectors else "—",
                    top_ticker=picks[0]["ticker"] if picks else "—",
                    sector_count=len(sectors),
                    pick_count=len(picks),
                    duration_s=data.get("duration_s", 0),
                    status=data.get("status", "unknown"),
                ))
            except Exception:
                continue
        return metas

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    def _update(self, run_id: str, progress: int, message: str, status: str = "running") -> None:
        with self._lock:
            self._progress[run_id] = {"progress": progress, "message": message, "status": status}

    def _run(self, run_id: str, cfg: HeatConfig) -> None:
        start = time.time()
        report: Optional[HeatReport] = None
        try:
            report = HeatReport(
                run_id=run_id,
                timestamp=_date.today().isoformat(),
                config=cfg.to_dict(),
                hot_sectors=[],
                hot_picks=[],
                duration_s=0.0,
                status="running",
            )
            self._do_run(run_id, cfg, report, start)
        except Exception as exc:
            logger.exception("[HeatRadar] run %s 失败: %s", run_id, exc)
            if report is None:
                report = HeatReport(
                    run_id=run_id,
                    timestamp=_date.today().isoformat(),
                    config=cfg.to_dict(),
                    hot_sectors=[],
                    hot_picks=[],
                    duration_s=0.0,
                    status="error",
                )
            report.status = "error"
            report.error = str(exc)
            report.duration_s = time.time() - start
            self._save_result(report)
            self._update(run_id, 100, f"失败: {exc}", "error")

    def _do_run(self, run_id: str, cfg: HeatConfig, report: HeatReport, start: float) -> None:
        today = _date.today().isoformat()

        # Step 1 — 初始化 LLM 和搜索服务
        self._update(run_id, 5, "初始化分析引擎…")
        from src.analyzer import GeminiAnalyzer
        from src.search_service import SearchService
        analyzer = GeminiAnalyzer()
        model = cfg.model or getattr(self.config, "heat_radar_model", "") or None

        try:
            search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=getattr(self.config, "anspire_api_keys", []),
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=getattr(self.config, "minimax_api_keys", []),
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=2,
                news_strategy_profile="short",
            )
        except Exception as exc:
            logger.warning("[HeatRadar] SearchService 初始化失败: %s", exc)
            search_service = None

        # Step 2 — 并发抓取 24-48h 短线新闻
        self._update(run_id, 15, "抓取最新市场新闻…")
        news_digest = self._build_news_digest(search_service, cfg.markets)

        if not news_digest:
            logger.warning("[HeatRadar] 无法获取实时新闻，使用 LLM 知识库兜底")
            news_digest = (
                f"(No live news feed available. Use your most current knowledge "
                f"of global financial markets as of {today}.)"
            )

        # Step 3 — LLM 识别热门板块
        self._update(run_id, 30, "识别热门板块…")
        hot_sectors = self._detect_hot_sectors(
            analyzer, news_digest, cfg.theme_count, today, model
        )
        report.hot_sectors = hot_sectors
        logger.info("[HeatRadar] 识别到 %d 个热门板块", len(hot_sectors))

        if not hot_sectors:
            report.status = "completed"
            report.duration_s = time.time() - start
            self._save_result(report)
            self._update(run_id, 100, "完成（未识别到热门板块）", "completed")
            return

        # Step 4 — 对 top 5 板块各自 LLM 提名股票
        target_sectors = hot_sectors[:5]
        markets_str = ", ".join(m.upper() for m in cfg.markets)
        all_picks: List[HotPick] = []
        total_sectors = len(target_sectors)

        for idx, sector in enumerate(target_sectors):
            pct = 35 + int((idx / total_sectors) * 45)
            self._update(run_id, pct, f"分析板块 [{sector.name}]…")
            picks = self._pick_stocks_for_sector(
                analyzer, sector, cfg.max_stocks_per_sector, cfg.markets, model
            )
            all_picks.extend(picks)

        # Step 5 — 过滤市场 → 去重 → 按 heat_score 排名
        self._update(run_id, 82, "排名筛选…")
        filtered = self._filter_and_rank(all_picks, cfg.markets, cfg.top_n)
        report.hot_picks = filtered

        # Step 6 — 保存结果
        report.status = "completed"
        report.duration_s = time.time() - start
        self._save_result(report)
        self._update(run_id, 95, f"完成 — 发现 {len(filtered)} 只热点股", "completed")
        logger.info("[HeatRadar] run %s 完成，耗时 %.1fs，%d 只热点股", run_id, report.duration_s, len(filtered))

        # Step 7 — 发送通知
        try:
            self._send_notification(report)
        except Exception as exc:
            logger.warning("[HeatRadar] 通知发送失败: %s", exc)

        # Step 8 — 写入淘金列表
        if report.hot_picks:
            try:
                from src.services.discovery_service import DiscoveryService
                n = DiscoveryService().add_from_heat_radar(run_id)
                logger.info("[HeatRadar] DiscoveryList +%d from %s", n, run_id)
            except Exception as exc:
                logger.warning("[HeatRadar] DiscoveryList 写入失败: %s", exc)

        self._update(run_id, 100, f"完成 — {len(filtered)} 只热点股", "completed")

    # ------------------------------------------------------------------
    # News fetching
    # ------------------------------------------------------------------

    def _build_news_digest(self, search_service, markets: Optional[List[str]] = None) -> str:
        if search_service is None or not getattr(search_service, "is_available", False):
            logger.info("[HeatRadar] SearchService 不可用，跳过新闻抓取")
            return ""

        snippets: List[str] = []
        seen_urls: set[str] = set()
        seen_lock = threading.Lock()
        queries = self._build_heat_queries(markets or ["us", "cn"])

        def _search_one(query: str) -> str:
            try:
                response = search_service.search(query, max_results=8, days=2)
                if response and response.results:
                    lines = [f"【{query} 搜索结果】（来源：{response.provider}）"]
                    kept = 0
                    for result in response.results:
                        url_key = (result.url or f"{result.source}:{result.title}").strip().lower()
                        with seen_lock:
                            if not url_key or url_key in seen_urls:
                                continue
                            seen_urls.add(url_key)
                        kept += 1
                        lines.append(f"\n{kept}. {result.to_text()}")
                        if kept >= 6:
                            break
                    return "\n".join(lines) if kept else ""
            except Exception as exc:
                logger.debug("[HeatRadar] 搜索失败 '%s': %s", query, exc)
            return ""

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_search_one, q): q for q in queries}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    snippets.append(result)

        if not snippets:
            return ""

        digest = "\n\n".join(snippets)
        if len(digest) > 12000:
            digest = digest[:12000]
        logger.info("[HeatRadar] 新闻摘要构建完成: %d 条查询有结果，共 %d 字符", len(snippets), len(digest))
        return digest

    @staticmethod
    def _build_heat_queries(markets: List[str]) -> List[str]:
        market_set = {m.strip().lower() for m in markets if m}
        queries: List[str] = []
        if "cn" in market_set:
            queries.extend(_HEAT_RADAR_CN_QUERIES)
        if "us" in market_set:
            queries.extend(_HEAT_RADAR_US_QUERIES)
        if not queries:
            queries = list(_HEAT_RADAR_QUERIES)

        deduped: List[str] = []
        seen: set[str] = set()
        for query in queries:
            key = query.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(query)
        return deduped

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def _detect_hot_sectors(
        self,
        analyzer,
        news_digest: str,
        count: int,
        date_str: str,
        model: Optional[str],
    ) -> List[HotSector]:
        prompt = _HEAT_SECTOR_PROMPT.format(
            date=date_str,
            news_digest=news_digest[:10000],
            count=count,
        )
        try:
            raw = analyzer.generate_text(prompt, max_tokens=3000, temperature=0.4, model=model)
            if not raw:
                return []
            return self._parse_sectors(raw)
        except Exception as exc:
            logger.warning("[HeatRadar] 板块识别 LLM 失败: %s", exc)
            return []

    def _pick_stocks_for_sector(
        self,
        analyzer,
        sector: HotSector,
        n: int,
        markets: List[str],
        model: Optional[str],
    ) -> List[HotPick]:
        markets_desc = " and ".join(m.upper() for m in markets)
        prompt = _HEAT_STOCK_PROMPT.format(
            sector_name=sector.name,
            catalyst_summary=sector.catalyst_summary,
            keywords=", ".join(sector.keywords[:6]),
            markets=markets_desc,
            n=n,
        )
        try:
            raw = analyzer.generate_text(prompt, max_tokens=2000, temperature=0.3, model=model)
            if not raw:
                return []
            return self._parse_picks(raw, sector)
        except Exception as exc:
            logger.warning("[HeatRadar] 股票提名 LLM 失败 [%s]: %s", sector.name, exc)
            return []

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_sectors(self, raw: str) -> List[HotSector]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                logger.warning("[HeatRadar] 无法解析板块 JSON: %.200s", text)
                return []
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError as exc:
                logger.warning("[HeatRadar] 板块 JSON 解析失败: %s", exc)
                return []

        sectors: List[HotSector] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                sectors.append(HotSector(
                    name=str(item.get("name", "Unknown")),
                    heat_score=float(item.get("heat_score", 50)),
                    news_velocity=int(item.get("news_velocity", 1)),
                    catalyst_summary=str(item.get("catalyst_summary", "")),
                    keywords=list(item.get("keywords", [])),
                    market_regions=list(item.get("market_regions", ["us", "cn"])),
                    sentiment=str(item.get("sentiment", "neutral")),
                    source_queries=list(item.get("source_queries", [])),
                ))
            except Exception as exc:
                logger.debug("[HeatRadar] 板块条目解析失败: %s", exc)
        return sorted(sectors, key=lambda s: s.heat_score, reverse=True)

    def _parse_picks(self, raw: str, sector: HotSector) -> List[HotPick]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if not m:
                logger.warning("[HeatRadar] 无法解析股票 JSON [%s]: %.200s", sector.name, text)
                return []
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []

        picks: List[HotPick] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                picks.append(HotPick(
                    rank=0,  # 最终排名在 _filter_and_rank 中赋值
                    ticker=str(item.get("ticker", "")),
                    name=str(item.get("name", "")),
                    market=str(item.get("market", "US")),
                    sector=str(item.get("sector", sector.name)),
                    current_price=float(item.get("current_price", 0.0)),
                    heat_score=float(item.get("heat_score", sector.heat_score * 0.8)),
                    llm_confidence=int(item.get("llm_confidence", 60)),
                    matched_sector=sector.name,
                    catalyst_thesis=str(item.get("catalyst_thesis", ""))[:120],
                    entry_window=str(item.get("entry_window", "days 1-5")),
                    key_risks=str(item.get("key_risks", "")),
                    trade_horizon="short",
                ))
            except Exception as exc:
                logger.debug("[HeatRadar] 股票条目解析失败: %s", exc)
        return picks

    def _filter_and_rank(
        self,
        picks: List[HotPick],
        markets: List[str],
        top_n: int,
    ) -> List[HotPick]:
        market_set = {m.upper() for m in markets}
        market_set.update({"A-share" if m == "cn" else m.upper() for m in markets})
        if "CN" in market_set:
            market_set.add("A-share")

        filtered: List[HotPick] = []
        for p in picks:
            if not p.ticker:
                continue
            pick_market = p.market.upper()
            if "A-SHARE" in market_set and pick_market in ("A-SHARE", "CN"):
                filtered.append(p)
            elif pick_market in market_set:
                filtered.append(p)

        seen: Dict[str, HotPick] = {}
        for p in sorted(filtered, key=lambda x: x.heat_score, reverse=True):
            if p.ticker not in seen:
                seen[p.ticker] = p

        ranked = list(seen.values())[:top_n]
        for i, p in enumerate(ranked, 1):
            p.rank = i
        return ranked

    # ------------------------------------------------------------------
    # Persistence & notification
    # ------------------------------------------------------------------

    def _save_result(self, report: HeatReport) -> None:
        path = _RESULTS_DIR / f"{report.run_id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[HeatRadar] 保存结果失败: %s", exc)

    def _send_notification(self, report: HeatReport) -> None:
        if not report.hot_picks:
            return
        lines = [
            f"热点雷达扫描完成 — {report.timestamp}",
            f"识别 {len(report.hot_sectors)} 个热门板块 | 提名 {len(report.hot_picks)} 只短线股票",
            "",
        ]
        for pick in report.hot_picks[:5]:
            market_label = "US" if pick.market.upper() in ("US",) else "A股"
            lines.append(
                f"#{pick.rank} {pick.ticker} ({market_label}) — 热度{pick.heat_score:.0f} "
                f"置信度{pick.llm_confidence}% [{pick.matched_sector}]"
            )
            if pick.catalyst_thesis:
                lines.append(f"  {pick.catalyst_thesis[:80]}")
        content = "\n".join(lines)
        try:
            from src.notification import get_notification_service
            ns = get_notification_service()
            ns.send(content=content, email_send_to_all=True)
        except Exception as exc:
            logger.warning("[HeatRadar] 通知发送失败: %s", exc)

    @staticmethod
    def _dict_to_report(data: Dict[str, Any]) -> HeatReport:
        sectors = [
            HotSector(
                name=s.get("name", ""),
                heat_score=float(s.get("heat_score", 0)),
                news_velocity=int(s.get("news_velocity", 0)),
                catalyst_summary=s.get("catalyst_summary", ""),
                keywords=s.get("keywords", []),
                market_regions=s.get("market_regions", []),
                sentiment=s.get("sentiment", "neutral"),
                source_queries=s.get("source_queries", []),
            )
            for s in data.get("hot_sectors", [])
        ]
        picks = [
            HotPick(
                rank=p.get("rank", 0),
                ticker=p.get("ticker", ""),
                name=p.get("name", ""),
                market=p.get("market", "US"),
                sector=p.get("sector", ""),
                current_price=float(p.get("current_price", 0)),
                heat_score=float(p.get("heat_score", 0)),
                llm_confidence=int(p.get("llm_confidence", 50)),
                matched_sector=p.get("matched_sector", ""),
                catalyst_thesis=p.get("catalyst_thesis", ""),
                entry_window=p.get("entry_window", ""),
                key_risks=p.get("key_risks", ""),
                trade_horizon=p.get("trade_horizon", "short"),
            )
            for p in data.get("hot_picks", [])
        ]
        return HeatReport(
            run_id=data["run_id"],
            timestamp=data["timestamp"],
            config=data.get("config", {}),
            hot_sectors=sectors,
            hot_picks=picks,
            duration_s=float(data.get("duration_s", 0)),
            status=data.get("status", "completed"),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Optional[NewsHeatRadar] = None
_instance_lock = threading.Lock()


def get_news_heat_radar() -> NewsHeatRadar:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = NewsHeatRadar()
    return _instance
