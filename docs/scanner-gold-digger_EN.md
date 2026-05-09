# Scanner and GoldDigger

This document covers the two cross-market stock discovery features: Scanner and GoldDigger.

## What They Do

Scanner finds medium-term investment opportunities from configured market universes:

- US: NYSE + NASDAQ universe
- China: A-share universe from local data providers
- Five-tier funnel: metadata, technicals, fundamentals, sector diversity, deep AI analysis
- China policy weighting: boosts A-share candidates tied to national policy and hot topics
- Top Picks show why-selected explanations and key screening factors

GoldDigger looks for overlooked beaten-down value stocks:

- US small caps: default USD 50M-1000M market cap
- China A-share low-position candidates
- Price drawdown, valuation discount, analyst neglect, theme matching, deep AI analysis
- China policy weighting for A-share candidate ranking

## CLI Usage

Run Scanner:

```bash
python main.py --scanner --discovery-markets us,cn
```

Run Scanner for China only with stronger policy weighting:

```bash
python main.py --scanner --discovery-markets cn --china-policy-weight 0.4
```

Run GoldDigger:

```bash
python main.py --gold-digger --discovery-markets us,cn
```

Run GoldDigger for China only:

```bash
python main.py --gold-digger --discovery-markets cn --china-policy-weight 0.4
```

Common options:

| Option | Feature | Description |
| --- | --- | --- |
| `--discovery-markets us,cn` | Both | Markets to scan: `us`, `cn`, or `us,cn` |
| `--discovery-top-n 10` | Both | Number of final picks |
| `--china-policy-weight 0.25` | Both | A-share policy and national-hot-topic weight, 0-1 |
| `--scanner-min-market-cap-m 500` | Scanner | Minimum market cap in USD millions |
| `--scanner-min-avg-volume 500000` | Scanner | Minimum average daily volume |
| `--scanner-max-tier5-stocks 30` | Scanner | Candidates sent to deep AI analysis |
| `--scanner-max-cn-stocks 800` | Scanner | A-share universe cap |
| `--gold-max-tier5-per-market 15` | GoldDigger | Deep AI candidates per market |
| `--gold-us-min-market-cap-m 50` | GoldDigger | US minimum market cap in USD millions |
| `--gold-us-max-market-cap-m 1000` | GoldDigger | US maximum market cap in USD millions |
| `--gold-min-price-decline-6m-pct 20` | GoldDigger | Minimum 6-month drawdown percent |
| `--gold-min-pe-discount-pct 10` | GoldDigger | Minimum PE discount versus sector |
| `--gold-theme-count 8` | GoldDigger | Number of macro/policy themes to detect |

The CLI waits for the background task to finish and prints a Top Picks summary to logs.

## Web Usage

Start the Web service:

```bash
python main.py --serve-only
```

Then open the WebUI:

- Scanner page: choose US, China, or both; configure top picks, A-share universe cap, and China policy weight.
- GoldDigger page: choose markets, top picks, per-market AI candidate count, and China policy weight.

## API Usage

Scanner:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scanner/scan \
  -H 'Content-Type: application/json' \
  -d '{"markets":["us","cn"],"top_n":10,"max_cn_stocks":800,"china_policy_weight":0.25}'
```

GoldDigger:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/gold-digger/dig \
  -H 'Content-Type: application/json' \
  -d '{"markets":["us","cn"],"top_n":10,"max_tier5_per_market":15,"china_policy_weight":0.25}'
```

Poll progress with:

- Scanner: `GET /api/v1/scanner/status/{scan_id}`
- GoldDigger: `GET /api/v1/gold-digger/status/{run_id}`

## Environment Defaults

Scanner supports these default settings:

```bash
SCANNER_ENABLED=true
SCANNER_MARKETS=us,cn
SCANNER_MIN_MARKET_CAP_M=500
SCANNER_MIN_AVG_VOLUME=500000
SCANNER_TOP_N=10
SCANNER_MAX_TIER5_STOCKS=30
SCANNER_MAX_CN_STOCKS=800
SCANNER_CHINA_POLICY_WEIGHT=0.25
SCANNER_UNIVERSE_CACHE_HOURS=24
```

Explicit Web and CLI request values override these defaults.

## Notes

- Scanner why-selected explanations combine funnel metrics and AI analysis summaries. They explain ranking rationale and are not standalone buy recommendations.
- China scanning depends on local Tushare, Baostock, and Akshare availability.
- China policy weighting only affects A-share candidate ranking.
- Higher weight favors national policy, industrial themes, and hot-topic relevance; `0` disables the boost.
- Both features can issue many quote, search, and LLM requests. Start with smaller candidate counts when validating a setup.
