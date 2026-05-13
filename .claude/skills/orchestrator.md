# Orchestrator — 多模型智能调度规则

当本 Skill 激活时，Claude Code 根据任务类型自动选择最优模型执行。

## 调度规则（由 claude-code-router config-router.json 的 Router 段强制执行）

| 任务类型 | 路由目标 | 适用场景 |
|---------|---------|---------|
| `default` | DeepSeek V4 Flash | 日常编码、问答、文档生成 |
| `background` | Ollama qwen3:8b (本地) | 文件读写、格式转换、简单脚本 |
| `think` | DeepSeek V4 Pro | 架构设计、复杂重构、算法实现 |
| `longContext` | DeepSeek V4 Pro | 超长上下文（>60K tokens） |
| `webSearch` | DeepSeek V4 Flash | 联网搜索 |

## 手动切换模型

在 Claude Code 会话中使用 `/model` 命令实时切换：

```
/model deepseek,deepseek-v4-flash    # 切到日常编码模型
/model deepseek,deepseek-v4-pro      # 切到深度推理模型
/model ollama,qwen3:8b              # 切到本地免费模型
```

## 工作流示例

### 场景 A：设计新模块（老板模式）
1. `/model deepseek,deepseek-v4-pro` — 激活深度推理
2. 输出架构文档、数据流图、接口定义
3. 保存为 `docs/design-<name>.html`

### 场景 B：填空式编码（打工人模式）
1. `/model deepseek,deepseek-v4-flash` — 切换到快速编码
2. 根据架构文档逐文件实现
3. 编写单元测试

### 场景 C：批量文件操作（免费苦力模式）
1. `/model ollama,qwen3:8b` — 切换到本地模型
2. 执行格式化、重命名、批量替换等操作
3. 零成本完成

## 配置引用

- 路由配置：`~/.claude-code-router/config-router.json`
- 启动命令：`bash claude_deepseek_settings.sh`（已更新）
- Provider 管理：`ccr model`（交互式 CLI）
- 配置编辑：`ccr config --edit`
