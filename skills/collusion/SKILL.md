---
name: collusion
description: 多视角协作引擎 — 方案设计、代码审查、任务拆解、问题诊断、技术选型。通过多 Agent 并行工作+交叉审查防止过度设计和意图丢失。
type: skill
version: v0.4.0
author: Collusion Contributors
---

# Collusion (共谋) — 多视角协作引擎

> 寄生而非替代：增强任何 AI 编码工具的设计能力，不破坏宿主缓存。

## 模式一览

| 命令 | 功能 | 内部协作流程 |
|:---|:---|:---|
| `/collusion scheme <任务>` | 方案设计 | 3 Agent 并行提案 → 交叉审查 → 可行性收束 → 投票 Top 3 |
| `/collusion review <文件/代码>` | 代码审查 | 3 Agent 从安全/性能/风格三视角并行审查 → 汇总风险清单 |
| `/collusion plan <任务>` | 任务拆解 | 架构师+产品经理+工程专家协作拆解 → 可执行任务清单 |
| `/collusion diagnose <问题>` | 问题诊断 | 3 Agent 独立构建故障树 → 交叉验证 → 综合诊断报告 |
| `/collusion choose <选型问题>` | 技术选型 | 成本/性能/安全/维护四维评估 → 加权推荐 |

## 使用方法

### /collusion scheme — 方案设计

触发 MCP 工具 `brainstorm_orchestrate`，启动完整编排流程：

1. 调用 `brainstorm_orchestrate(task="<任务描述>", agents=3, format="html")` 启动编排
2. 调用 `brainstorm_status(task_id="...")` 查询进度（预计 2-4 分钟）
3. 调用 `brainstorm_result(task_id="...")` 获取 Top 3 方案及完整评分

**HTML 报告包含**：SVG 雷达图、方案对比表、风险标注卡片、可执行任务清单、在线反馈输入区。

**反馈修改**：用户对方案有修改建议时，调用 `collusion_refine(task_id="...", modifications=[{"step_name":"...", "suggestion":"..."}])`，三个 Agent 独立审查后给出采纳/隐患/创新反馈。

**Agent 数量选择**：
- `agents=1`：快速模式（~¥0.03，适合简单任务）
- `agents=2`：标准模式（~¥0.07，中等复杂）
- `agents=3`：完整模式（~¥0.08-0.15，高复杂度/高风险任务）

### /collusion review — 代码审查

使用宿主原生多 Agent 能力（不依赖 MCP），从三视角并行审查：

**执行流程**：
1. 请用户提供要审查的文件或代码片段
2. 同时启动 3 个 Agent（每个只读），分别从以下视角审查：
   - **Agent A（安全专家）**：检查注入漏洞、权限控制、敏感数据暴露、CSP/CSRF、依赖漏洞
   - **Agent B（性能架构师）**：检查 N+1 查询、缓存策略、内存泄漏、算法复杂度、IO 瓶颈
   - **Agent C（代码风格/可维护性）**：检查命名一致性、SOLID 原则、错误处理、圈复杂度、注释质量
3. 汇总三个 Agent 的审查结果，按严重程度排序输出风险清单

**输出格式**：每项风险包含 {位置, 严重度, 描述, 修复建议}

### /collusion plan — 任务拆解

使用宿主原生多 Agent 能力，协作拆解大型任务：

**执行流程**：
1. 接受用户输入的任务描述
2. 同时启动 3 个 Agent：
   - **Agent A（产品经理）**：关注用户故事、验收标准、优先级排序
   - **Agent B（架构师）**：关注技术依赖、模块边界、接口定义、数据流
   - **Agent C（工程专家）**：关注实现路径、风险预估、测试策略、工时估算
3. Owner Agent 整合三方输出，去重合并，产出统一任务清单

**输出格式**：任务清单含 {ID, 名称, 描述, 依赖关系, 预计耗时, 优先级, 对应文件路径}

### /collusion diagnose — 问题诊断

使用宿主原生多 Agent 能力，独立构建故障树后交叉验证：

**执行流程**：
1. 接受用户输入的异常现象/错误信息
2. 同时启动 3 个 Agent，每个独立构建故障树：
   - **Agent A**：从用户操作链入手的故障树
   - **Agent B**：从系统组件依赖链入手的故障树
   - **Agent C**：从数据流/状态变化入手的故障树
3. 三个 Agent 轮流审查对方的故障树，标注一致节点和分歧节点
4. Owner Agent 合并为一棵综合故障树，按概率排序给出排查路径

**输出格式**：综合故障树 + 排查路径优先级列表

### /collusion choose — 技术选型

使用宿主原生多 Agent 能力，四维加权评估：

**执行流程**：
1. 接受用户输入的选型问题（候选技术方案列表）
2. 同时启动 4 个 Agent，各负责一个维度：
   - **Agent A（成本）**：许可费用、云资源成本、学习成本、维护成本、迁移成本
   - **Agent B（性能）**：吞吐量、延迟、扩展性、冷启动、资源占用
   - **Agent C（安全）**：漏洞历史、社区响应速度、合规认证、依赖风险
   - **Agent D（维护）**：社区活跃度、文档质量、API 稳定性、锁定风险
3. 汇总评分，用户可根据项目偏好调整各维度的权重

**输出格式**：加权评分表 + 各维度详细理由 + 最终推荐

---

## MCP 工具参考

当 Collusion 以 MCP Server 模式运行时，提供以下工具：

| 工具名 | 说明 |
|:---|:---|
| `brainstorm_orchestrate` | 启动方案编排（task, agents=1-3, format=md/html/both） |
| `brainstorm_status` | 查询编排进度（task_id） |
| `brainstorm_result` | 获取 Top3 方案及完整评分（task_id） |
| `collusion_refine` | 提交修改建议，触发 Agent 审查（task_id, modifications） |

### 成本参考

| Agent 数量 | 单任务 Token | 单任务成本 |
|:---|:---|:---|
| 1 | ~15,000 | ~¥0.03 |
| 2 | ~35,000 | ~¥0.07 |
| 3 | ~50,000-65,000 | ~¥0.08-0.15 |

*缓存友好模式下输入成本降至 ~10%。*

---

## 安装配置

### 方式一：pip 安装（推荐）

```bash
pip install collusion-mcp
```

然后在宿主 MCP 配置中添加：

**Claude Code** (`.mcp.json`):
```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "collusion-mcp",
      "args": ["--stdio"]
    }
  }
}
```

**Reasonix / Trae Solo** (MCP 配置):
```json
{
  "mcpServers": {
    "brainstorm": {
      "command": "collusion-mcp",
      "args": ["--sse", "--port", "8020"]
    }
  }
}
```

### 方式二：源码安装

```bash
git clone https://github.com/anthropics/Collusion.git
cd Collusion
pip install -e .
```

### API Key 配置

```bash
# 方式一：环境变量（推荐）
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxx"

# 方式二：config.json
cp config.example.json config.json
# 编辑填入 api_key

# 方式三：Reasonix 用户零配置（自动读取）
```

---

## 核心原则

1. **寄生而非替代**：Collusion 不替代任何 AI 编码工具
2. **缓存无损**：在任何平台上都不破坏宿主缓存
3. **零额外配置**：安装时不需重复配置 API Key（Reasonix 用户零配置）
4. **流程决定质量**：多 Agent 的多样性来自协作流程
5. **成本透明**：每次调用返回 Token 消耗和费用
6. **过程可追溯**：保留修改历史、缺失补全记录、可行性收束决策
