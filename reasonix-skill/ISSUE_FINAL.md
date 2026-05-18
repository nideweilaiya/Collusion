# Issue 定稿：为 Reasonix 引入外部 MCP 多 Agent 方案设计工具

**目标仓库**: [esengine/DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)
**标签**: `feature-request`, `mcp`

---

## 标题

[Feature Request] 引入 Collusion：通过 MCP 协议为 Reasonix 增加多 Agent 并行方案设计能力

## 摘要

Collusion 是一个寄生在 Reasonix 外部的 MCP Server，通过 MCP Sampling 委托实现 3 个独立 AI Agent（安全/架构/UX）并行提案、交叉审查、可行性收束、投票输出 Top 3 方案。不改 Reasonix 内核代码，不破坏缓存命中率（保持 >90%），Reasonix 用户零配置（自动读取已有 API Key）。

## 解决什么问题

单 Agent 做技术方案设计时有三个固有问题：

1. **意图丢失** — AI 给出了看起来很完美的方案，落地时发现违背了核心约束
2. **过度设计** — 缺乏工程视角制衡，方案倾向于引入不必要的微服务/中间件
3. **关键环节缺失** — 用户描述遗漏了安全、合规、迁移等环节，AI 从不主动追问

Collusion 通过**多视角并行提案 + 交叉审查 + 可行性强制收束**解决这三个问题。它不是在生成代码，而是在写代码之前把方案想清楚、想周全。

## 如何集成

以 **MCP Server + 技能描述符** 形式寄生在 Reasonix 之外：

```
用户输入 "用 Collusion 设计文件分享服务"
  → Reasonix 调用 Collusion MCP 工具 brainstorm_orchestrate
  → Collusion 分解任务，启动 3 个子 Agent 进程
  → 子 Agent 需要 LLM 时，通过 sampling/createMessage 委托 Reasonix 执行
  → Reasonix 用自己的 API Key + 缓存完成调用
  → 结果返回 Collusion，继续交叉审查、收束、投票
  → Top 3 方案展示在 Reasonix 终端
```

关键设计：**所有 LLM 调用走 MCP Sampling 委托**。Collusion 本身不持有 API Key，不直接调 DeepSeek。Reasonix 在受托执行时自动应用自身的缓存机制——固定的 system prompt、append-only 消息历史、不可变的工具定义——缓存命中率完整保留。

## 对 Reasonix 缓存的影响

**零影响，命中率保持 >90%。**

原因：
- 子 Agent 只接收任务摘要（200-500 tokens），不加载 Reasonix 的全量对话上下文
- LLM 调用由 Reasonix 自己执行，走 Reasonix 自身的缓存体系
- 不引入新的 system prompt 前缀，不破坏 Reasonix 不可变前缀的稳定性

这与其他 MCP 工具根本不同——它们通常会引入额外上下文导致缓存失效。

## 已实现的能力

| 模式 | MCP 工具 | 说明 |
|------|---------|------|
| 方案设计 | `brainstorm_orchestrate` | 7 阶段全流程：解构→共识→提案→审查→收束→整合→投票 |
| 代码审查 | `collusion_review` | 安全/性能/可维护性三视角并行审查 |
| 任务拆解 | `collusion_plan` | 产品经理+架构师+工程专家协作拆解 |
| 问题诊断 | `collusion_diagnose` | 故障树分析，交叉验证 |
| 技术选型 | `collusion_choose` | 成本/性能/安全/维护四维加权评估 |
| 项目侦察 | `collusion_scout` | 多视角项目代码审查 |
| 黑板模式 | `collusion_blackboard_start` | 3 Agent 后台静默并行，询问时通知用户 |
| 方案增强 | `collusion_enhance` | 已有方案多视角审查增强 |

共 18 个 MCP 工具，67 个单元/回归/冒烟测试全部通过。

## Benchmark 数据

5 个领域、25 个维度的真实项目盲评对比（Collusion vs 单次 LLM 调用）：

| 维度 | Collusion 胜 | 对照组胜 | 平局 |
|------|:----------:|:------:|:---:|
| 完整性 | 5 | 0 | 0 |
| 创新性 | 5 | 0 | 0 |
| 业务对齐 | 4 | 0 | 1 |
| 可行性 | 2 | 1 | 2 |
| 正确性 | 0 | 0 | 5 |
| **总计** | **16** | **1** | **8** |

成本：¥0.04（1 Agent Flash）~ ¥0.16（3 Agent Flash）~ ¥0.30（混合模式）/任务。

## 对 Reasonix 用户的体验

安装后零配置：
```bash
pip install collusion-mcp
```

Reasonix MCP 配置一行：
```json
{ "mcp": ["collusion=collusion-mcp --stdio"] }
```

使用时带关键词即可触发（避免误激活）：
```
用 Collusion 设计一个支持过期时间和密码的文件分享服务
```

## 附带建议：Reasonix --headless 模式

如果要让多 Agent 真正并行（每个 Agent 都是独立 Reasonix 进程），Reasonix 需要增加一个轻量的 headless 模式：

```bash
# 理想形态：外部程序 spawn Reasonix 子进程作为 LLM 后端
reasonix code --headless --prompt "你是安全专家，审查以下方案" --output review.md
```

预计改动量 ~75 行（3 个新参数 + 1 个分支函数），不改缓存、不改 LLM 适配器、不改 TUI。详见附件 `REASONIX_HEADLESS_PROPOSAL.md`。

即使没有 headless 模式，Collusion 也已经可以正常工作——MCP Sampling 委托路线是现在就能跑的。

## 项目地址

- GitHub: https://github.com/nideweilaiya/Collusion
- 协议: MIT
- 语言: Python 3.10+
- 依赖: mcp, openai (DeepSeek 兼容), starlette, uvicorn, jinja2, mistune, pyyaml

## 征求意见

1. 这个方向（外部 MCP Server + MCP Sampling 委托）是否符合 Reasonix 对社区 MCP 工具的期望？
2. Issue 提到的 headless 模式建议是否值得单独开 Feature Request？
3. 如果可以，我们接下来录制演示视频 + 整理 PR。

---

## 发布前准备

- [ ] 确认 Reasonix Issue 模板格式要求（标签：`feature-request`, `mcp`）
- [ ] 录制 Reasonix 中端到端使用视频（1-2 分钟）
- [ ] 实测 brainstorm_orchestrate + blackboard 全部路径
- [ ] 准备好回复社区提问的技术细节
