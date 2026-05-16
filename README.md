# Collusion (共谋)

> 一个在方案设计阶段，通过多对象协作防止过度设计和意图丢失的 MCP 引擎。
> 在真实项目盲评中，以 16:1 胜出对照组（5 领域 × 5 维度）。

[English](#english) | [快速开始](#快速开始) | [路线图](ROADMAP.md) | [贡献指南](CONTRIBUTING.md)

---

## 为什么需要 Collusion？

你是否遇到过：

- AI 给出的方案看起来很完美，但落地时发现违背了核心约束？
- 需求描述遗漏了关键环节（如安全、合规），AI 从不主动追问？
- 只有一个方案，没得选，不知道是否有更好的技术路径？

**Collusion 正是为这些问题而生。**

它不是另一个"先设计再编码"的工具。Plan 模式、Spec 模式、Superpowers 都能做到"先设计"。Collusion 做的是它们做不到的事：**在方案设计阶段，让多个 AI Agent 从不同专业视角并行提案、互相审查、强制收束。**

---

## 核心机制

| 阶段 | 机制 | 说明 |
|:---|:---|:---|
| 🔍 环节共识 | 多 Agent 审查 | 自动识别缺失环节并补全（如安全、合规、迁移） |
| 📝 并行提案 | 3 个对象代言人 | 业务价值、技术架构、安全合规三视角独立生成方案 |
| 🔄 交叉审查 | 轮流修改 | 每个 Agent 审查他人方案，一处修改一处标注 |
| 🛑 可行性收束 | 强制减速带 | 工程对象代言人对过度设计做减法 |
| 📊 Owner 整合 | 两遍打磨 | Flash 模型初稿 + Strong 模型终审润色 |
| 🗳️ 投票评分 | 5 维打分 | 正确性、完整性、可行性、创新性、业务对齐 |
| 🏆 输出 Top 3 | 差异化方案 | 附带评分理由和复杂度标注 |

---

## 真实项目盲评对比

**测试任务**：为一个开源博客平台设计技术方案（要求一个 Docker 命令部署，不依赖付费云服务）。

**对照组**：单次 LLM 调用生成方案（5 个不同领域各 1 个任务，共 25 个维度对比）。

| 维度 | Collusion 胜 | 对照组胜 | 平局 |
|:---|:---|:---|:---|
| 完整性 | 5 | 0 | 0 |
| 创新性 | 5 | 0 | 0 |
| 业务对齐 | 4 | 0 | 1 |
| 可行性 | 2 | 1 | 2 |
| 正确性 | 0 | 0 | 5 |
| **总计** | **16** | **1** | **8** |

具体到博客平台任务，Collusion 的设计方案与 Superpowers 的对比：

| 对比项 | Collusion | Superpowers |
|:---|:---|:---|
| 部署方式 | Go+SQLite 单二进制 | Next.js + 5 容器 |
| 外部依赖 | 0（内置 SQLite+Bleve） | PostgreSQL + Redis + Meilisearch |
| 架构哲学 | 单体+模块化，极简主义 | 微服务，企业级复杂度 |
| 首屏性能 | 纯静态 HTML，无 JS 阻塞 | Next.js hydration 有白屏 |
| 自托管门槛 | 一个 Docker 命令 | 需编排多个服务 |
| 方案完整度 | 11 个技术模块，含 SQL 定义 | 架构概念清晰，落地细节较少 |
| 编辑器体验 | 基础 Markdown 编辑 | CodeMirror 6 详细设计 |

> 📝 **重要说明**：本次测试中，Collusion 以全自动模式运行。Superpowers 的设计哲学是人工多轮协作，在有人类紧密配合时其方案质量可能更高。此对比旨在展示两种范式的差异，而非全面性能竞赛。

**评委核心评语**：
> "方案以 Docker 单命令极简 SQLite 部署，架构务实性能达标；对比方案依赖过多，服务繁杂，部署门槛高。"

---

## 场景能力

### 需求补齐

当用户需求遗漏关键环节时，Collusion 会主动识别并补全。

**案例**：用户输入 "我需要一个前端框架选型方案，团队主要用 React。只关心组件库和技术栈。"

引擎在环节共识阶段自动补全了：

| 原始环节 | 引擎补全 |
|:---|:---|
| 组件库选型对比 | → 前端安全策略（XSS/CSP/依赖漏洞扫描） |
| 技术栈搭配建议 | → 后端 API 与数据层设计 |
| 工程化配置（打包/构建） | → 开发者体验与入门引导 |
| | → 部署与 CI/CD 方案 |

**另一个案例**：用户输入 "我想做一个开发者用的工具平台"，引擎自动补全了：

- 开发者体验与入门引导
- 数据迁移与导入导出
- 威胁建模与安全风险评估

### 约束守卫

当方案偏离核心约束时，可行性收束阶段会强制纠偏。

**案例**：博客平台任务的约束是"不依赖付费云服务，一个 Docker 命令部署"。某方案提出了 PostgreSQL + Redis + 多个微服务的复杂架构，在可行性收束阶段被工程对象代言人判定为"过度设计"，复杂度评分被强制压回阈值以下，最终该方案排名第三。

---

## 快速开始

### 前置条件

- Python 3.10+
- DeepSeek API Key（[免费注册](https://platform.deepseek.com)）

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-username/Collusion.git
cd Collusion

# 安装依赖
pip install -r requirements.txt

# 配置 API Key（二选一）
# 方式一：环境变量（推荐）
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxx"

# 方式二：复制示例配置并填入 Key
cp config.example.json config.json
# 编辑 config.json，填入 api_key
```

### MCP 客户端接入

**Claude Code** — 在 `.mcp.json` 中添加：

```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "python",
      "args": ["src/mcp_server.py", "--stdio"],
      "cwd": "/path/to/Collusion"
    }
  }
}
```

**Trae Solo / 其他 MCP 客户端** — 启动 SSE 模式：

```bash
python src/mcp_server.py --sse --port 8020
```

然后在 MCP 配置中添加 `http://localhost:8020/sse`。

### 使用示例

```
# 在 Claude Code 中直接调用
请用 brainstorm_orchestrate 工具设计一个高并发短链接服务

# 调整 Agent 数量
brainstorm_orchestrate(task="设计一个RESTful API", agents=1)  # 快速模式
brainstorm_orchestrate(task="设计一个开源博客平台", agents=3)  # 完整模式

# 查询进度
brainstorm_status(task_id="task_xxxxxxxxxxxx")

# 获取结果
brainstorm_result(task_id="task_xxxxxxxxxxxx")
```

### 成本参考

| Agent 数量 | 单任务 Token | 单任务成本 |
|:---|:---|:---|
| 1 | ~15,000 | ~¥0.03 |
| 2 | ~35,000 | ~¥0.07 |
| 3 | ~50,000-65,000 | ~¥0.08-0.15 |

*基于 DeepSeek API 定价，实际成本随任务复杂度浮动。v0.5.0 目标降至 ¥0.05-0.10。*

---

## 当前局限

- **仅支持 DeepSeek API**：底层使用 DeepSeek 适配器，暂不支持其他 LLM 提供商（DeepSeek 兼容 OpenAI 协议，欢迎社区贡献其他适配器）
- **仅支持从零生成方案**：暂不支持基于已有方案的增量增强（已列入中期路线图）
- **Agent 角色固定**：当前内置 3 个角色（业务价值、技术架构、安全合规），自定义角色正在开发
- **仅支持 stdio 传输**：HTTP/SSE 传输模式正在开发
- **输出格式为 Markdown**：HTML 可视化报告正在开发（含雷达图、架构图、风险标注卡片）

## 路线图

详见 [ROADMAP.md](ROADMAP.md)。

近期计划（v0.4.0 - v0.5.0）：
- HTML 可视化报告（雷达图 + Mermaid 架构图 + 风险卡片）
- 可执行 JSON 蓝图（直接对接 writing-plans）
- 用户反馈回路（增量修改 + 多视角审查）
- 成本控制（¥0.05-0.10/任务）

中期计划（v0.6.0 - v0.8.0）：
- 增量增强模式（基于已有方案进行多视角审查和优化）
- 动态 Agent 调度
- Agent 角色扩展
- HTTP + SSE 远程部署

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)

## 贡献

欢迎 Issue、PR、Discussion！详见 [CONTRIBUTING.md](CONTRIBUTING.md)

---

## English

### What is Collusion?

A multi-agent MCP engine for technical design orchestration. Give it a task, and three AI agents — each representing a different perspective (Business Value, Technical Architecture, Security & Compliance) — independently generate proposals, cross-review each other's work, enforce feasibility checks, and produce a ranked Top 3.

Collusion **beat single-shot LLM generation 16:1** (8 ties) in blind evaluations across 5 domains, 25 total dimensions.

### Key differentiators

- **Gap detection**: Automatically identifies and fills missing components (security, deployment, migration)
- **Multi-perspective review**: Three agents critique each other's work, not just generate
- **Feasibility brake**: Engineering agent enforces real-world constraints and simplification
- **Ranked Top 3**: Multi-dimensional scoring with reasoning, not just one answer

### Quick Start

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your-api-key"
# Then configure your MCP client with src/mcp_server.py --stdio
```

> **Note**: Collusion currently only supports DeepSeek API. DeepSeek uses an OpenAI-compatible protocol, so other compatible providers may work with a custom adapter. Community contributions for additional LLM backends are welcome.

### Roadmap

See [ROADMAP.md](ROADMAP.md) for the full public roadmap covering v0.4.0 through v1.0.0+.
