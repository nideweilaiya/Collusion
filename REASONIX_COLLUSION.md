# Collusion for Reasonix

完全基于 Reasonix 框架的多智能体协作设计引擎。利用 Reasonix 的 `spawn_subagent` 并行机制和前缀缓存优势。

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                    Reasonix 主循环                              │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │         主 Agent (Orchestrator)                          │ │
│  └───────────────────────────────────────────────────────────┘ │
│                          │                                      │
│                          ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │      并行 spawn_subagent (parallelSafe: true)            │ │
│  │  ┌─────────────────────────────────────────────────────┐ │ │
│  │  │   Agent 1: UX/业务价值       (flash)              │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │  ┌─────────────────────────────────────────────────────┐ │ │
│  │  │   Agent 2: 技术架构         (pro, 需要时)        │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │  ┌─────────────────────────────────────────────────────┐ │ │
│  │  │   Agent 3: 安全合规         (flash)               │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────┘ │
│                          │                                      │
│                          ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │         文件系统黑板 (~/.reasonix/collusion/)            │ │
│  │   - task_{id}.json  (任务状态)                        │ │
│  │   - agent_{role}.md   (各 Agent 方案)                  │ │
│  │   - review_{role}.json (评审结果)                     │ │
│  │   - final.md         (最终方案)                       │ │
│  └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Reasonix 优势利用

1. **前缀缓存最大化**:
   - 不变的 system prompt + tool definitions
   - 只追加的对话日志
   - 子 Agent 复用前缀缓存

2. **原生并行**:
   - `spawn_subagent` 标记为 `parallelSafe: true`
   - 同时启动 3 个 Agent 并行提案

3. **成本控制**:
   - 子 Agent 默认使用 `deepseek-v4-flash`
   - 技术架构 Agent 在需要时使用 pro

---

## Skill: collusion-design

使用 Collusion 进行多智能体方案设计。

**Frontmatter:**
```yaml
name: collusion-design
description: 多智能体协作方案设计 - UX、架构、安全三个角度并行提案、交叉评审
runAs: main
model: deepseek-v4-flash
```

### 设计流程

1. **任务分解**: 理解用户的设计任务
2. **并行提案**: 同时 spawn 3 个 subagent，分别代表：
   - UX/业务价值
   - 技术架构
   - 安全合规
3. **交叉评审**: 每个 Agent 评审其他 Agent 的方案
4. **可行性收束**: 工程化检查和简化
5. **Owner 整合**: 每个 Agent 整合评审意见形成最终方案
6. **投票评分**: 综合评分并推荐 Top 1 方案

---

## Usage (in Reasonix)

```bash
cd your-project
reasonix code
# 然后运行:
/skill collusion-design
# 或者直接:
/skill new collusion-design
# 编辑后 /skill run collusion-design
```

---

## 黑板文件结构

```
~/.reasonix/collusion/
├── tasks/
│   └── {task_id}/
│       ├── task.json
│       ├── steps.json
│       ├── agents/
│       │   ├── ux/
│       │   │   ├── proposal.md
│       │   │   ├── reviews.json
│       │   │   └── final.md
│       │   ├── architecture/
│       │   │   ├── proposal.md
│       │   │   ├── reviews.json
│       │   │   └── final.md
│       │   └── security/
│       │       ├── proposal.md
│       │       ├── reviews.json
│       │       └── final.md
│       └── final/
│           ├── rankings.json
│           └── report.md
└── templates/
    └── system_prompt*.md
```
