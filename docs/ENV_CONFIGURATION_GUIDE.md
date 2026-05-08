# 🔧 .env 配置 Step-by-Step 指南

本指南将一步步指导您完成 `.env` 文件的配置，确保系统能够正常运行。

## 📋 配置概览

`.env` 文件包含以下主要配置类别：
1. **AI 模型配置**（必须配置至少一个）
2. **股票列表配置**（必须配置）
3. **通知渠道配置**（必须配置至少一个）
4. **可选功能配置**
5. **服务器配置**

---

## 🚀 快速开始

### Step 1: 创建 .env 文件

```bash
# 在项目根目录下执行
cp .env.example .env
vim .env  # 或使用您喜欢的编辑器
```

### Step 2: 配置 AI 模型（至少选择一个）

#### 选项 A: Anspire（推荐，一Key多用）
```bash
# Anspire API Key（同时支持大模型和搜索）
ANSPIRE_API_KEYS=your_anspire_api_key_here
```

**获取方式：**
1. 访问 [Anspire 开放平台](https://open.anspire.cn/?share_code=QFBC0FYC)
2. 注册并获取 API Key
3. 本项目用户有免费额度

#### 选项 B: AIHubMix（推荐，一Key多模型）
```bash
# AIHubMix API Key（支持全系模型）
AIHUBMIX_KEY=your_aihubmix_api_key_here
```

**获取方式：**
1. 访问 [AIHubMix](https://aihubmix.com/?aff=CfMq)
2. 注册并获取 API Key
3. 本项目用户可享 10% 优惠

#### 选项 C: Google Gemini
```bash
# Gemini API Key
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.1-pro-preview
```

**获取方式：**
1. 访问 [Google AI Studio](https://aistudio.google.com/)
2. 创建新的 API Key
3. 免费额度有限，适合测试

#### 选项 D: Anthropic Claude
```bash
# Anthropic API Key
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6
```

**获取方式：**
1. 访问 [Anthropic Console](https://console.anthropic.com/)
2. 创建 API Key
3. 按量付费，质量较高

#### 选项 E: OpenAI 兼容接口
```bash
# OpenAI API Key（或兼容接口如 DeepSeek、通义千问等）
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1  # 或其他兼容接口
OPENAI_MODEL=gpt-4o-mini
```

### Step 3: 配置股票列表（必须）

```bash
# 股票代码列表（逗号分隔）
STOCK_LIST=600519,000001,300750
```

**股票代码格式：**
- **A股**: 6 位数字，如 `600519`（贵州茅台）
- **港股**: `hk` + 5 位数字，如 `hk00700`（腾讯控股）
- **美股**: 股票代码，如 `AAPL`（苹果）

**示例配置：**
```bash
# 只分析 A 股
STOCK_LIST=600519,000001,300750

# 混合市场
STOCK_LIST=600519,hk00700,AAPL

# 港股专用
STOCK_LIST=hk00700,hk00941,hk00994
```

### Step 4: 配置通知渠道（至少选择一个）

#### 选项 A: 企业微信（推荐）
```bash
# 企业微信机器人 Webhook URL
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your_webhook_key
```

**配置步骤：**
1. 在企业微信中创建机器人
2. 获取 Webhook URL
3. 复制 URL 中 `key=` 后面的部分

#### 选项 B: 飞书
```bash
# 飞书机器人 Webhook URL
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook_key
```

**配置步骤：**
1. 在飞书中创建自定义机器人
2. 获取 Webhook URL
3. 如需签名验证，还需配置：
```bash
FEISHU_WEBHOOK_SECRET=your_secret_here
```

#### 选项 C: Telegram
```bash
# Telegram Bot 配置
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

**配置步骤：**
1. 与 @BotFather 对话创建 Bot
2. 获取 Bot Token
3. 与您的 Bot 对话获取 Chat ID

#### 选项 D: 邮件通知
```bash
# 邮件配置
EMAIL_SENDER=your_email@qq.com
EMAIL_PASSWORD=your_authorization_code
EMAIL_RECEIVERS=receiver1@example.com,receiver2@example.com
```

**配置步骤：**
1. 使用 QQ 邮箱或其他 SMTP 服务
2. 生成授权码（非登录密码）
3. 配置收件人列表

#### 选项 E: 其他通知方式
```bash
# PushPlus（国内推送）
PUSHPLUS_TOKEN=your_pushplus_token

# Server酱³（手机推送）
SERVERCHAN3_SENDKEY=your_sendkey

# 自定义 Webhook
CUSTOM_WEBHOOK_URLS=https://your-webhook-url.com/notify
```

### Step 5: 配置搜索功能（可选但推荐）

#### 选项 A: SerpAPI（推荐）
```bash
# SerpAPI 实时金融新闻搜索
SERPAPI_API_KEYS=your_serpapi_key_here
```

#### 选项 B: Tavily
```bash
# Tavily 搜索 API
TAVILY_API_KEYS=your_tavily_key_here
```

#### 选项 C: 其他搜索服务
```bash
# Bocha 搜索
BOCHA_API_KEYS=your_bocha_key_here

# Brave Search
BRAVE_API_KEYS=your_brave_key_here

# MiniMax 搜索
MINIMAX_API_KEYS=your_minimax_key_here
```

### Step 6: 配置定时任务（可选）

```bash
# 启用定时任务
SCHEDULE_ENABLED=true
SCHEDULE_TIME=18:00  # 每日执行时间（24小时制）

# 启用大盘复盘
MARKET_REVIEW_ENABLED=true
```

### Step 7: 配置服务器参数（可选）

```bash
# Web 界面配置
WEBUI_HOST=0.0.0.0  # 监听地址（0.0.0.0 表示所有接口）
API_PORT=8000       # 端口号

# 数据库路径
DATABASE_PATH=./data/stock_analysis.db

# 日志配置
LOG_DIR=./logs
LOG_LEVEL=INFO
```

---

## 📝 完整配置示例

### 最小配置（仅必需项）
```bash
# AI 模型（选择一个）
ANSPIRE_API_KEYS=your_anspire_key

# 股票列表
STOCK_LIST=600519,000001

# 通知渠道（选择一个）
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your_key
```

### 推荐配置（完整功能）
```bash
# === AI 模型配置 ===
ANSPIRE_API_KEYS=your_anspire_key
GEMINI_API_KEY=your_gemini_key  # 备用

# === 股票列表 ===
STOCK_LIST=600519,000001,300750,hk00700,AAPL

# === 通知渠道 ===
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your_key
EMAIL_SENDER=your_email@qq.com
EMAIL_PASSWORD=your_auth_code

# === 搜索配置 ===
SERPAPI_API_KEYS=your_serpapi_key

# === 定时任务 ===
SCHEDULE_ENABLED=true
SCHEDULE_TIME=18:00
MARKET_REVIEW_ENABLED=true

# === 服务器配置 ===
WEBUI_HOST=0.0.0.0
API_PORT=8000

# === 数据源配置 ===
TUSHARE_TOKEN=your_tushare_token  # 可选，用于更精准的数据
```

---

## 🔍 配置验证

### 验证步骤 1: 语法检查
```bash
# 检查 .env 文件语法
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('.env 文件语法正确')
"
```

### 验证步骤 2: 测试运行
```bash
# 干运行测试（不实际执行分析）
python main.py --dry-run
```

### 验证步骤 3: 检查配置完整性
```bash
# 启动应用查看配置状态
python main.py --debug
```

---

## ❓ 常见问题

### Q1: API Key 如何保密？
**A:** 确保 `.env` 文件不被提交到版本控制：
```bash
# .gitignore 应该包含
.env
.env.local
.env.*.local
```

### Q2: 如何配置多个 API Key？
**A:** 某些服务支持多 Key 配置：
```bash
# 多个 Gemini Key（逗号分隔）
GEMINI_API_KEYS=key1,key2,key3

# 多个搜索 Key
ANSPIRE_API_KEYS=key1,key2,key3
```

### Q3: 配置错误怎么办？
**A:** 查看日志文件定位问题：
```bash
# 查看应用日志
tail -f logs/stock_analysis_*.log

# 重新配置
vim .env
python main.py --dry-run
```

### Q4: 如何测试通知配置？
**A:** 手动发送测试消息：
```bash
# 测试通知（需要先配置股票列表）
python main.py --stocks 600519 --no-notify
```

---

## 🚨 重要提醒

1. **必须配置项**：AI 模型、股票列表、通知渠道
2. **API Key 安全**：不要泄露 API Key，不要提交到代码仓库
3. **配置验证**：使用 `--dry-run` 参数测试配置
4. **日志查看**：遇到问题时查看日志文件
5. **逐步配置**：建议先配置最小版本，再逐步添加功能

---

## 📞 获取帮助

如果配置过程中遇到问题：

1. 查看 [FAQ](FAQ.md) 文档
2. 检查日志文件 `logs/stock_analysis_*.log`
3. 使用 `python main.py --debug` 获取详细信息
4. 在项目仓库提交 Issue

---

**配置完成后，运行 `python main.py --dry-run` 验证配置正确性！**
