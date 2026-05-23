---
name: collusion
description: v0.6 检查点引擎。轻量决策评估→按需深度审查。78 测试通过。信息单向流架构。
type: skill
version: v2.0.0
---

# Collusion (共谋) v2.0

> 检查点引擎 + 决策支持。核心：先检后设 → 情境压缩 → 检查点链 → 决策卡片。
> 轻量模式 ≤15k token / 4-5 次 LLM 调用。深度模式按需激活。

## 触发条件

在任何开发任务中，自动检测是否需要知识库支持。
不需要用户主动说"Collusion"。

## 宿主检测

**你在 Reasonix 中（有 MCP 工具）** → MCP Server 已全局注册，自动可用。

MCP 工具（按组分类）：

**probe（探查类）** — 零/极少 LLM 调用：
- `collusion_search_assets` — 搜索历史方案资产库
- `collusion_elicit` — 回答引导问题
- `collusion_scout` — 多视角项目侦察
- `collusion_status` — 查询任务进度

**check（检查类）** — v0.6 轻量主路径，4-5 次 LLM：
- `collusion_assess` — **主入口**：检索→压缩→核心检查点→决策卡片
- `collusion_check` — 运行单个检查点

**plan（策划类）** — 深度按需，8-25 次 LLM：
- `collusion_orchestrate` — 完整多方案编排+审查 (保留)
- `collusion_enhance` — 方案增强
- `collusion_review` — 代码审查
- `collusion_plan` — 任务拆解
- `collusion_choose` — 技术选型
- `collusion_refine` — 修改审查
- `collusion_diagnose` — 问题诊断
- `collusion_route` — 结构图谱路由

**workspace（工作区）**：
- `collusion_result` — 获取任务结果
- `collusion_render` — 渲染决策卡片/方案为 MD/HTML
- `collusion_branch` / `collusion_merge` — 分支与合并

旧工具名 (`brainstorm_*`) 保留向后兼容。

**你在 Claude Code / Cursor 中** → 跳转到下方「Skill 模式」使用原生 Agent 并行。

---

## v0.6 核心工作流

```
用户描述任务
    │
    ├─ collusion_assess(task)  ← 轻量默认路径
    │     ├─ KnowledgeRetriever: 检索历史资产
    │     ├─ SituationCompressor: 压缩为 ≤500 token 快照
    │     ├─ 3 核心检查点 (semantic_consistency / interface_conflict / pattern_match)
    │     └─ 输出: DecisionCard (约束+风险+建议下一步)
    │
    ├─ [若 deep_review_recommended=true]
    │   └─ collusion_orchestrate(task) ← 深度审查
    │
    └─ collusion_render(task_id) → MD/HTML 报告
```

## v0.6 架构原则

1. **信息单向流**: Retriever → Compressor → Snapshot → Checkpoints。检查点绝不绕过快照访问原始数据。
2. **检查点无状态**: BaseCheckpoint.run() 是纯函数，输出仅依赖输入快照和自身逻辑。
3. **Token 预算硬上限**: 轻量 ≤15k / 深度 ≤25k。TokenBudgetController 超预算自动截断。
4. **动态角色选择**: 深度检查点按需激活角色，核心检查点不绑定角色。

### 原则：先检后设

所有任务启动时，自动执行关联度预检。只有复杂任务才启动 3 Agent 编排。

### 自动流程

```
用户描述任务
    │
    ├─ collusion_check(任务描述) — 0.5ms
    │     ↓
    │   ┌─ 命中历史 (关联度 >0.5): 直接复用经验，不启动编排
    │   └─ 未命中或复杂任务: 提示启动编排
    │
    ├─ brainstorm_orchestrate(mode="design") — 按需
    │     ↓ 自动完成:
    │     ├─ Phase 6.5: 资产索引 ✓
    │     ├─ Phase 6.6: 因果记录 ✓
    │     └─ Phase 6.7: Agent Graph ✓
    │
    └─ collusion_stats — 随时查看积累状态
```

### 命令参考

| 场景 | 操作 | 耗时 |
|------|------|------|
| 改 bug、加字段 | 自动 collusion_check | 0.5ms |
| 新模块架构 | collusion_check + 可选 brainstorm_orchestrate | 2-4 min |
| 查看知识积累 | collusion_stats | 0.5ms |
| 手动归档 | collusion_asset_tag + collusion_causal_record | 2ms |
| **并行方案设计** | **parallel_schedule(task, mode="design")** | **~90s** |
| **并行编程执行** | **parallel_schedule(task, mode="code", workdir="D:/path")** | **~16s** |

### parallel_schedule — 多Agent并行引擎

替代 subagent 的日常并行模式。3 个独立 Worker 并行工作，固定 system prompt 确保 ~90% 缓存命中率。

```
/parallel 设计短链接服务
→ parallel_schedule(task="设计短链接服务", mode="design")
→ 3 Worker 并行: architect / security / performance
→ 汇总输出

/parallel 修复 auth.py 的登录验证bug
→ parallel_schedule(task="修复auth.py的登录验证bug", mode="code", workdir="D:/myproject")
→ 1 Worker 带文件系统MCP: 读文件→修改→验证
→ 返回修改结果
```

**缓存表现：**
- 首次冷启动: 0%（每个 Worker 首次 system prompt 计算）
- 第2轮: ~91%（固定 system prompt，DeepSeek 前缀缓存命中）
- 不同任务相同角色: ~91%（system prompt 不变，仅 task 变化）

**vs Subagent 优势：**
- 上下文隔离：Worker 不继承父会话，只带固定 system prompt
- 缓存高：system prompt 永不变化 → ~90% 缓存命中 vs Subagent 每次不同上下文
- 成本低：最小上下文 token → 总成本 $0.001-0.005/轮

---

## Skill 模式（Claude Code / Cursor）

以下所有模式利用宿主原生 Agent 工具并行执行。

### 模式一览

| 命令 | 功能 | Agent 数 | 典型耗时 |
|------|------|---------|---------|
| `/collusion scheme <任务>` | 方案设计 | 3 | 3-5 min |
| `/collusion enhance <方案>` | 方案增强 | 3 | 2-3 min |
| `/collusion review <代码>` | 代码审查 | 3 | 1-2 min |
| `/collusion plan <任务>` | 任务拆解 | 3 | 1-2 min |
| `/collusion diagnose <问题>` | 问题诊断 | 3 | 1-2 min |
| `/collusion choose <选项>` | 技术选型 | 4 | 1-2 min |
| `/collusion scout` | 项目侦察 | 3 | 1-2 min |

所有模式共用同一个编排框架：并行 Agent → 交叉审查 → 汇总输出。

---

## /collusion scheme — 方案设计

从零设计技术方案。

### 流程

**Phase 1: 解构** — 拆解为 4-8 个技术环节。检查是否遗漏安全/部署/迁移/开发者体验。

**Phase 2: 并行提案** — 一次工具调用，3 个 Agent 块同时启动：

- **Agent A (业务价值)**：关注用户能否用起来、操作流畅度、部署门槛、场景完整性
- **Agent B (技术架构)**：关注选型合理性、性能瓶颈、缓存策略、扩展性、数据流
- **Agent C (安全合规)**：关注数据安全、认证授权、威胁建模、合规要求

每个 Agent 输出覆盖所有环节的完整技术方案（含选型、决策理由、代码示例、文件路径标注如 `src/api/xxx.py`）。

**Phase 3: 审查与收束** — 对每份方案输出审查表：

| 方案 | 优点 | 关键风险 | 过度设计？ | 简化建议 |
|------|------|----------|-----------|----------|

**Phase 4: 投票** — 5 维评分（可行性 25% / 正确性 20% / 完整性 20% / 业务对齐 20% / 创新性 15%）

**Phase 5: 输出** — 以 Top 1 为基础，吸收其他方案优点，整合输出：

```
🏆 推荐方案: 方案X — X.X分 | 一句话评语

📊 排名: 🥇方案X X.X | 🥈方案Y X.X | 🥉方案Z X.X

📋 推荐方案完整设计:
   环节1: xxx — 具体设计
   环节2: xxx — 具体设计
   ...

⚠️ 风险与简化建议
💰 预估: 复杂度 | 代码量 | 工期
```

---

## /collusion enhance — 方案增强

对已有方案（半成品或草稿）进行多视角审查增强。

### 流程

用户提供一份已有方案（可以是 Markdown、文本描述或代码注释），3 Agent 并行审查：

- **Agent A (业务)**：是否遗漏用户场景？优先级是否合理？上手门槛？
- **Agent B (技术)**：选型是否合理？扩展性？性能瓶颈？
- **Agent C (安全)**：安全漏洞？合规风险？数据保护？

输出：

```
🔍 审查发现:
   ✅ 优势: ...
   ⚠️ 风险: ...
   💡 增强建议: ...

📝 增强后的方案:
   [在原文基础上标注修改点和理由]
```

---

## /collusion review — 代码审查

审查代码文件或片段。

### 流程

用户提供代码（粘贴或指定文件路径），3 Agent 并行审查：

- **Agent A (安全)**：注入漏洞、权限控制、敏感数据暴露、依赖漏洞
- **Agent B (性能)**：N+1 查询、缓存缺失、内存泄漏、算法复杂度
- **Agent C (可维护性)**：命名一致性、SOLID 原则、错误处理、圈复杂度

输出：

```
🔍 审查结果:

   🔴 高危 (x项):
   - [位置] 问题描述 → 修复建议

   🟡 中危 (x项):
   - [位置] 问题描述 → 修复建议

   🟢 建议 (x项):
   - [位置] 改进建议

📊 总体评分: X/10
```

---

## /collusion plan — 任务拆解

将大型任务拆解为可执行的任务清单。

### 流程

3 Agent 并行拆解：

- **Agent A (产品经理)**：关注用户故事、验收标准、优先级
- **Agent B (架构师)**：关注技术依赖、模块边界、接口定义
- **Agent C (工程专家)**：关注实现路径、风险预估、工时估算

整合去重后输出：

```
📋 任务清单:

   Task 1: xxx [MVP] [高优先级] [2-3h]
   Task 2: xxx [中优先级] [1-2h] → 依赖 Task1
   ...

📊 总计: x个任务 | 预估 x 天 | MVP 范围: Task 1-x
```

---

## /collusion diagnose — 问题诊断

对异常现象进行故障树分析。

### 流程

3 Agent 独立构建故障树，交叉验证后合并：

- **Agent A**：从用户操作链入手
- **Agent B**：从系统组件依赖入手
- **Agent C**：从数据流/状态变化入手

输出：

```
🌳 综合故障树:

   根因假设1 (概率: 高) → 验证方法: xxx → 修复: xxx
   根因假设2 (概率: 中) → 验证方法: xxx → 修复: xxx
   根因假设3 (概率: 低) → 验证方法: xxx

🔧 推荐排查路径: 1→2→3
```

---

## /collusion choose — 技术选型

对多个候选方案进行多维度评估。

### 流程

4 Agent 并行评估（如果候选方案明确已知，直接评分；否则先让各 Agent 从自己维度推荐候选）：

- **Agent A (成本)**：许可费用、云资源成本、学习成本、迁移成本
- **Agent B (性能)**：吞吐量、延迟、扩展性、冷启动、资源占用
- **Agent C (安全)**：漏洞历史、社区响应速度、合规认证
- **Agent D (维护)**：社区活跃度、文档质量、API 稳定性、锁定风险

输出：

```
📊 加权评分:

   | 方案 | 成本 | 性能 | 安全 | 维护 | 总分 |
   |------|------|------|------|------|------|
   | A    | 8.0  | 7.5  | 9.0  | 8.5  | 8.3  |
   | B    | 6.0  | 9.0  | 7.0  | 6.5  | 7.1  |

🏆 推荐: 方案A — 综合得分最高，长期维护成本低

💡 用户可通过调整权重适配项目偏好
```

---

---

## /collusion scout — 项目侦察

读取当前项目代码，输出多视角侦察报告，为后续方案设计提供上下文。

### 流程

**Step 1: 索引** — 列出项目文件结构，识别技术栈（package.json/requirements.txt/go.mod 等）。

**Step 2: 并行侦察** — 根据文件类型分配 3 Agent：

- **Agent A (业务/UX)**：读取页面/组件/API 定义，分析用户流程和交互模式
- **Agent B (架构)**：读取核心模块、数据层、配置文件，分析架构模式和依赖关系
- **Agent C (安全/质量)**：读取认证/授权/中间件，分析安全配置和潜在风险

每个 Agent 输出各自视角的发现摘要。

**Step 3: 汇总** — 输出统一侦察报告：

```
🔍 项目侦察报告:

   📁 技术栈: Python 3.11 + FastAPI + SQLite
   📊 规模: ~5,000 行 | 15 模块 | 3 路由组
   🏗️ 架构: 分层架构 (routes → services → models)

   ✅ 优势: 模块清晰、类型标注完整
   ⚠️ 风险: 缺少认证中间件、无数据库迁移
   💡 建议: 优先补充认证层、添加测试覆盖

   📋 关键文件:
      src/main.py — 应用入口
      src/auth.py — 认证逻辑（缺失！）
      src/models/user.py — 用户模型
```

---

## 参数

- `--quick`：1 Agent 快速模式，跳过交叉审查（适用于 scheme/review）
- `MVP 优先`：收束阶段加大简化力度

## 原则

1. **并行是灵魂**：所有模式的 Agent 必须同时启动
2. **做减法**：敢于砍过度设计，回归务实
3. **独立完整**：每个 Agent 产出完整方案/审查，不是一个段落
4. **不写代码**：产出设计文档和审查意见，不写实现
5. **零依赖**：纯 Claude Code 原生能力，不依赖外部服务
