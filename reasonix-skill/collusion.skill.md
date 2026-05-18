---
name: collusion
description: Multi-perspective design orchestration engine. 3 AI agents (UX/Architecture/Security) generate, cross-review, and rank technical proposals in parallel.
type: mcp-skill
version: 0.5.1
mcp_server: collusion-mcp
---

# Collusion — Multi-Agent Design Review

A multi-perspective technical design engine. Give it a task, and three AI agents — each representing a different perspective (Business Value, Technical Architecture, Security & Compliance) — independently generate proposals, cross-review each other's work, enforce feasibility checks, and produce a ranked Top 3.

Beat single-shot LLM generation **6:1** in blind evaluations across 5 domains, 25 total dimensions.

## Trigger Keywords

- `Collusion`
- `/collusion`
- `用Collusion` / `用共谋`

Without these keywords, Reasonix handles the request normally (no false activation).

## Modes

| User Intent | Tool | Description |
|---|---|---|
| "design / scheme / 设计 / 方案" | `brainstorm_orchestrate` | Full 7-phase orchestration → Top 3 proposals |
| "review / 审查 / 检查代码" | `collusion_review` | Security/Performance/Maintainability code review |
| "plan / 拆解 / 规划 / 任务" | `collusion_plan` | Task decomposition with dependencies |
| "diagnose / 诊断 / 排查" | `collusion_diagnose` | Fault tree analysis from 3 perspectives |
| "choose / 选型 / 对比" | `collusion_choose` | Multi-dimensional tech selection (cost/perf/security/maintenance) |
| "scout / 侦察 / 看看项目" | `collusion_scout` | Project reconnaissance report |
| "blackboard / 黑板 / 护航" | `collusion_blackboard_start` | Background agent collaboration via shared blackboard |
| "enhance / 增强 / 优化方案" | `collusion_enhance` | Multi-perspective enhancement of existing plans |

## Execution Flow

```
User: "用 Collusion 设计一个文件分享服务"
  → Mode detected: scheme
  → brainstorm_orchestrate(task="...", preset="auto")
  → Returns task_id
  → "3 agents drafting proposals (~3-5 min)"
  → Poll brainstorm_status until complete
  → brainstorm_result → Show Top 3 with scores
```

## Cache Preservation

Collusion uses **MCP Sampling delegation** (`src/llm/mcp_sampling.py`) for all LLM calls. Instead of calling the DeepSeek API directly, Collusion requests Reasonix to execute LLM calls via `sampling/createMessage`. This means:

- **All API calls go through Reasonix** — using Reasonix's own API key and connection
- **No extra context is loaded** — each sub-agent receives only a task summary, not the full conversation history
- **Cache hit rate remains >90%** — Reasonix's session cache is fully preserved
- **Zero-config** — auto-detects Reasonix's API key from `~/.reasonix/config.json`

This is Collusion's key advantage over other MCP tools: it does NOT degrade the host's cache performance.

## Cost Reference

| Configuration | Cost | Time |
|---|---|---|
| 1 Agent (Flash) | ¥0.04 | ~2 min |
| 3 Agent (all Flash) | ¥0.16 | ~4.5 min |
| 3 Agent (hybrid: Arch R1 + UX/Sec Flash) | ¥0.20-0.30 | ~5-8 min |

*Based on DeepSeek API pricing. Costs borne by the host (Reasonix) when using MCP Sampling mode.*

## Available MCP Tools

**Core:**
`brainstorm_orchestrate`, `brainstorm_status`, `brainstorm_result`

**Review & Analysis:**
`collusion_enhance`, `collusion_review`, `collusion_plan`, `collusion_diagnose`, `collusion_choose`, `collusion_scout`

**Blackboard Mode:**
`collusion_blackboard_start`, `collusion_blackboard_status`, `collusion_blackboard_answer`, `collusion_blackboard_merge`

**Advanced:**
`collusion_refine`, `brainstorm_elicit`, `brainstorm_search_assets`, `collusion_branch`, `collusion_merge`

## Setup

1. Install: `pip install collusion-mcp`
2. Start MCP server: `collusion-mcp --sse --port 8020`
3. Add to Reasonix MCP config: `http://localhost:8020/sse`

No separate API key needed — Reasonix users get zero-config key detection.
