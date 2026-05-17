# Collusion for Reasonix

> Reasonix 专属集成指引。零额外配置，自动复用 Reasonix API Key。

## 前置条件

Reasonix 已配置 MCP Server（在 `~/.reasonix/config.json` 中）：

```json
{
  "mcp": [
    "brainstorm=http://localhost:8020/sse"
  ]
}
```

如果尚未配置，在 Reasonix 设置 → MCP 中添加 SSE 端点。

## 启动 MCP Server

**方式一：自动启动（推荐）**

将 `reasonix-start.bat` 放入 Windows 启动项，或每次打开 Reasonix 前运行：

```bash
D:\BrainstormOrchestrator\platforms\reasonix-start.bat
```

**方式二：手动启动**

```bash
collusion-mcp --sse --port 8020
```

保持终端窗口不关闭。

## 使用方式

### 方案设计（完整 7 阶段编排）

```
/brainstorm_orchestrate 设计一个文件分享服务，支持过期时间和访问密码
```

约 4-5 分钟后，调用 `brainstorm_result` 获取 Top 3 方案。

### 黑板+顾问模式（3 Agent 后台静默）

```
/collusion_blackboard_start 设计一个高并发短链接服务
```

3 个子 Agent 在后台静默运行（架构师用 R1 深度推理，安全/UX 用 Flash 控制成本）：

- `collusion_blackboard_status` — 查询进度和子 Agent 询问
- `collusion_blackboard_answer` — 回答子 Agent 的疑问
- `collusion_blackboard_merge` — 合并最终方案

### 其他模式

| 工具 | 用途 |
|------|------|
| `collusion_enhance` | 增强已有方案 |
| `collusion_review` | 代码审查 |
| `collusion_plan` | 任务拆解 |
| `collusion_diagnose` | 问题诊断 |
| `collusion_choose` | 技术选型 |
| `collusion_scout` | 项目侦察 |

## 成本

| 配置 | 成本 | 耗时 |
|------|------|------|
| 1 Agent | ¥0.04 | ~2 min |
| 3 Agent 全 Flash | ¥0.16 | ~4.5 min |
| 黑板+顾问（混合） | ¥0.20-0.30 | ~5-8 min |

所有 LLM 调用通过 MCP Sampling 委托 Reasonix 执行，完整保留 99% 缓存命中率。
