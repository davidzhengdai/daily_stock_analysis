# Scanner 与沙里淘金

本文说明两个跨市场选股功能：Scanner 全市场扫股，以及沙里淘金 GoldDigger。

## 功能定位

Scanner 用于从配置的市场股票池中筛选中期投资机会。当前支持：

- 美股：NYSE + NASDAQ 股票池
- A股：通过本地数据源获取 A 股股票池
- 五层漏斗：基础元数据、技术面、基本面、行业分散、AI 预选与深度分析
- A股政策热点权重：对中国政策、国家热点、产业主题相关候选加权
- 跨市场保留：同时扫描美股与 A股时，Scanner 会在技术筛选、基本面筛选和最终 Top Picks 阶段为每个仍有有效候选的市场保留推荐空间，避免单一市场挤出另一市场
- AI 预选：深度分析前会先用轻量 LLM 从行业/市场分散后的候选池中挑选进入 Tier 5 的股票；LLM 不可用时自动回退到规则排序
- Top Picks 展示入选理由和关键筛选因子，便于理解股票为何通过漏斗

沙里淘金用于寻找被市场忽视的低位价值股。当前支持：

- 美股小盘股：默认 50M–1000M 美元市值
- A股低位候选
- 超跌、估值折价、分析师覆盖、主题匹配、AI 预选与深度分析
- A股政策热点权重：提高中国政策与国家热点相关候选的排序权重

## 命令行用法

运行 Scanner：

```bash
python main.py --scanner --discovery-markets us,cn
```

只扫描 A股，并提高中国政策热点权重：

```bash
python main.py --scanner --discovery-markets cn --china-policy-weight 0.4
```

运行沙里淘金：

```bash
python main.py --gold-digger --discovery-markets us,cn
```

只运行 A股沙里淘金：

```bash
python main.py --gold-digger --discovery-markets cn --china-policy-weight 0.4
```

常用参数：

| 参数 | 适用功能 | 说明 |
| --- | --- | --- |
| `--discovery-markets us,cn` | 两者 | 扫描市场，可填 `us`、`cn` 或 `us,cn` |
| `--discovery-top-n 10` | 两者 | 最终输出推荐数量 |
| `--china-policy-weight 0.25` | 两者 | A股政策与国家热点权重，范围 0-1 |
| `--scanner-min-market-cap-m 500` | Scanner | 最低市值过滤，单位百万美元 |
| `--scanner-min-avg-volume 500000` | Scanner | 最低日均成交量过滤 |
| `--scanner-max-tier5-stocks 30` | Scanner | 进入深度 AI 分析的候选数量 |
| `--scanner-max-cn-stocks 800` | Scanner | A股股票池上限 |
| `--gold-max-tier5-per-market 15` | 沙里淘金 | 每个市场进入深度 AI 分析的候选数量 |
| `--gold-us-min-market-cap-m 50` | 沙里淘金 | 美股最低市值，单位百万美元 |
| `--gold-us-max-market-cap-m 1000` | 沙里淘金 | 美股最高市值，单位百万美元 |
| `--gold-min-price-decline-6m-pct 20` | 沙里淘金 | 6 个月最小跌幅百分比 |
| `--gold-min-pe-discount-pct 10` | 沙里淘金 | 相对行业 PE 最小折价百分比 |
| `--gold-theme-count 8` | 沙里淘金 | 宏观/政策主题提取数量 |

CLI 会等待后台任务完成，并在日志中输出 Top Picks 摘要。

## Web 用法

启动 Web 服务：

```bash
python main.py --serve-only
```

打开 WebUI 后：

- Scanner 页面可选择美股、A股或两者，并配置推荐数量、A股股票池上限、政策热点权重。
- 沙里淘金页面可选择市场、推荐数量、每市场 AI 候选数量、政策热点权重。

## API 用法

Scanner：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scanner/scan \
  -H 'Content-Type: application/json' \
  -d '{"markets":["us","cn"],"top_n":10,"max_cn_stocks":800,"china_policy_weight":0.25}'
```

沙里淘金：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/gold-digger/dig \
  -H 'Content-Type: application/json' \
  -d '{"markets":["us","cn"],"top_n":10,"max_tier5_per_market":15,"china_policy_weight":0.25}'
```

接口会立即返回任务 ID。通过对应 status 接口轮询进度：

- Scanner：`GET /api/v1/scanner/status/{scan_id}`
- 沙里淘金：`GET /api/v1/gold-digger/status/{run_id}`

## 环境配置

Scanner 支持以下默认配置：

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
SCANNER_AI_PRESELECT_ENABLED=true
GOLD_DIGGER_AI_PRESELECT_ENABLED=true
```

Web 和 CLI 显式传入的参数会覆盖这些默认值。

## 注意事项

- Scanner 的入选理由来自筛选漏斗指标与 AI 分析摘要组合，用于解释排序依据，不等同于买入建议。
- 同时选择美股与 A股时，Scanner 会优先保持跨市场覆盖；如果某个市场没有足够有效候选，则空余名额按综合排序回填给其他市场。
- A股股票池会过滤指数、基金和 ETF；批量行情筛选通过统一数据源管理器自动 fallback，并优先使用适合批量任务的 Baostock，避免慢速行情接口失败时拖慢整批筛选。
- 中国政策热点权重只影响 A股候选排序，不会改变美股排序。
- 权重越高，越偏向国家政策、产业主题和热点方向；权重为 `0` 时关闭该加权。
- AI 预选使用 `SCANNER_MODEL` / `GOLD_DIGGER_MODEL`，只负责挑选进入深度分析的候选，不替代最终完整研报分析。
- Scanner 与沙里淘金都可能触发大量行情、搜索和 LLM 请求，建议先用较小的候选数量验证配置。
