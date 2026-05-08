# 交易策略目录 / Trading Strategies

本目录存放 **自然语言交易策略文件**（YAML 格式）。系统启动时自动加载此目录下所有 `.yaml` 文件。

对用户和文档，我们继续把这些能力称为”策略”；在代码、配置和 API 字段里，它们统一命名为 `skill`，你可以把它理解为”可复用的策略能力包”。

---

## 内置策略一览（共 15 个）

### 📈 趋势类（Trend）
| 策略 | 文件 | 优先级 | 说明 |
|------|------|--------|------|
| 默认多头趋势 | `bull_trend.yaml` | 10 | 默认策略，含趋势阶段分类、板块共振、RS评级 ⭐ |
| 均线金叉 | `ma_golden_cross.yaml` | 20 | 金叉质量九维评分 + 假信号三滤网 |
| 放量突破 | `volume_breakout.yaml` | 30 | 突破质量十维评分 + 吸筹分析 + 回踩确认 |
| 缩量回踩 | `shrink_pullback.yaml` | 40 | 五级支撑体系 + 斐波那契 + 回踩健康度评分 |
| 动量轮动 | `momentum_rotation.yaml` | 75 | 板块动量排名 + 资金流向 + 轮动信号 ⭐ 新增 |
| 龙头策略 | `dragon_head.yaml` | 80 | 六维领涨评分卡 + 持续性评估 + 资金验证 |

### 📊 形态类（Pattern）
| 策略 | 文件 | 优先级 | 说明 |
|------|------|--------|------|
| 一阳夹三阴 | `one_yang_three_yin.yaml` | 100 | 灵活形态匹配 + 量能U型弧线 + 多变体 |
| 缺口策略 | `gap_strategy.yaml` | 105 | 突破/中继/衰竭缺口 + 回补概率 ⭐ 新增 |

### 🔄 反转类（Reversal）
| 策略 | 文件 | 优先级 | 说明 |
|------|------|--------|------|
| 底部放量 | `bottom_volume.yaml` | 60 | 四步验证 + 死猫反弹过滤 + 质量评分 |
| 量价背离 | `volume_price_divergence.yaml` | 65 | VPA量价分析 + 主力吸筹/出货识别 ⭐ 新增 |
| 均值回归 | `mean_reversion.yaml` | 85 | 布林带+RSI+Z分数多指标超卖检测 ⭐ 新增 |

### 🧩 框架类（Framework）
| 策略 | 文件 | 优先级 | 说明 |
|------|------|--------|------|
| 箱体震荡 | `box_oscillation.yaml` | 50 | 算法化箱体 + 成交量剖面 + 时间衰减 |
| 缠论 | `chan_theory.yaml` | 70 | 简化实用版：背驰识别+分型定位+中枢操作 |
| 波浪理论 | `wave_theory.yaml` | 80 | 三大铁律 + 概率化情景 + 波量分析 |
| 情绪周期 | `emotion_cycle.yaml` | 95 | 自适应阈值 + 六维情绪评分 + 逆情绪仓位 |

⭐ = 默认激活策略 | ⭐ 新增 = v2 新增策略

---

## v2 升级亮点（2026-05）

所有策略均已从 v1 升级至 v2 专家增强版，核心改进：

1. **量化评分卡体系**：每个策略都有专属的多维评分卡（6-10 维），替代模糊的经验判断
2. **自适应阈值**：关键参数不再使用固定值，而是基于个股自身历史数据分布
3. **多级确认体系**：从「单信号触发」升级为「多信号逐步确认」，大幅降低假信号
4. **仓位矩阵**：每个策略都内置了基于信号强度的分档仓位建议
5. **假信号过滤**：每种策略都有专用的假信号识别规则
6. **新增 4 策略**：动量轮动、均值回归、缺口策略、量价背离，填补原有空白

---

## 如何编写自定义策略（Strategy Skill）

只需创建一个 `.yaml` 文件，用中文（或任意语言）描述你的交易策略即可，**无需编写任何代码**。

### 最简模板

```yaml
name: my_strategy          # 唯一标识（英文，下划线连接）
display_name: 我的策略      # 显示名称（中文）
description: 简短描述策略用途

instructions: |
  你的策略描述...
  用自然语言写出判断标准、入场条件、出场条件等。
  可以引用工具名称（如 get_daily_history、analyze_trend）来指导 AI 使用哪些数据。
```

### 完整模板

```yaml
name: my_strategy
display_name: 我的策略
description: 简短描述策略适用的市场场景

# 策略分类：trend（趋势）、pattern（形态）、reversal（反转）、framework（框架）
category: trend

# 关联的核心交易理念编号（1-7），可选
core_rules: [1, 2]

# 策略需要使用的工具列表，可选
# 可用工具：get_daily_history, analyze_trend, get_realtime_quote,
#           get_sector_rankings, search_stock_news
required_tools:
  - get_daily_history
  - analyze_trend

# 可选别名（用于 /ask 等自然语言技能选择）
aliases: [我的战法, 我的模型]

# 以下元数据用于驱动默认行为（可选）
# default_active: 是否属于默认激活技能集
# default_router: 是否属于路由 fallback 技能集
# default_priority: 默认展示/排序优先级，数值越小越靠前
# market_regimes: 该技能优先适配的市场状态标签
default_active: true
default_router: false
default_priority: 100
market_regimes: [trending_up]

# 策略详细说明（自然语言，支持 Markdown 格式）
instructions: |
  **我的策略名称**

  判断标准：

  1. **条件一**：
     - 使用 `analyze_trend` 检查均线排列。
     - 描述你期望看到的趋势特征...

  2. **条件二**：
     - 描述量能要求...

  评分调整：
  - 满足条件时建议的 sentiment_score 调整
  - 在 `buy_reason` 中注明策略名称
```

### 核心交易理念参考

| 编号 | 理念 |
|------|------|
| 1 | 严进策略：乖离率 < 5% 才考虑入场 |
| 2 | 趋势交易：MA5 > MA10 > MA20 多头排列 |
| 3 | 效率优先：量能确认趋势有效性 |
| 4 | 买点偏好：优先回踩均线支撑 |
| 5 | 风险排查：利空新闻一票否决 |
| 6 | 量价配合：成交量验证价格运动 |
| 7 | 强势趋势股放宽：龙头股可适当放宽标准 |

## 自定义策略目录

除了本目录（内置策略），你还可以通过环境变量指定额外的自定义策略目录：

```env
AGENT_SKILL_DIR=./my_skills
```

系统会同时加载内置策略和自定义策略。如果名称冲突，自定义策略覆盖内置策略。

环境变量名仍然是 `AGENT_SKILL_DIR`，这是内部统一命名后的配置入口；在产品语义上，它依然表示”自定义策略目录”。
