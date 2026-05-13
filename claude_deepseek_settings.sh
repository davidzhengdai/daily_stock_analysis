#!/usr/bin/env bash
# ============================================================
# Claude Code 启动方式速查 — 多模型智能路由
# ============================================================
#
# 路由规则（~/.claude-code-router/config.json）：
#   default     → deepseek,deepseek-v4-flash   日常编码
#   think       → deepseek,deepseek-v4-pro     架构推理（老板）
#   background  → ollama,qwen3:8b              本地免费（苦力）
#   longContext → deepseek,deepseek-v4-pro     超长上下文
#   webSearch   → deepseek,deepseek-v4-flash   联网搜索
#
# 在 Claude Code 会话中动态切换:
#   /model deepseek,deepseek-v4-flash    # 日常编码
#   /model deepseek,deepseek-v4-pro      # 架构设计
#   /model ollama,qwen3:8b              # 本地免费

# ============================================================
# 方式 A: claude-code-router 团队模式（推荐）
# ============================================================

# 1. 加载 DeepSeek API Key（ccr 启动时需要，用于 $DEEPSEEK_API_KEY 变量替换）
export DEEPSEEK_API_KEY=$(python3 -c "
import json
with open('$HOME/.config/claude/claude_deepseek_settings.json') as f:
    cfg = json.load(f)
print(cfg.get('api',{}).get('authToken','') or cfg.get('env',{}).get('ANTHROPIC_AUTH_TOKEN',''))
")

# 2. 确保 ccr 以最新配置重启（避免残留进程丢失环境变量）
ccr stop 2>/dev/null
sleep 1
ccr start
sleep 2

# 3. 显示各 provider 状态
echo ""
echo "========== Provider 状态 =========="
ccr status

# 4. 启动 Claude Code（通过 ccr 代理路由）
ccr code

# ============================================================
# 方式 B: 直连 DeepSeek（备用）
# ============================================================
# claude --bare \
#   --settings ~/.config/claude/claude_deepseek_settings.json \
#   --debug-file /tmp/check.log \
#   --model sonnet \
#   "your prompt here"

# ============================================================
# 方式 C: ccr 命令参考
# ============================================================
# ccr start                        # 仅启动代理（后台）
# ccr stop                         # 停止代理
# ccr restart                      # 重启代理
# ccr status                       # 查看代理状态
# ccr code                         # 启动代理 + Claude Code
# ccr model                        # 交互式模型管理
# ccr config --edit                # 编辑路由配置
# eval "$(ccr activate)"           # 手动注入环境变量
