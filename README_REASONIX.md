# Collusion for Reasonix

完全基于 **Reasonix 框架** 的多智能体协作设计引擎！

## ✨ 核心优势

### 1. **完全利用 Reasonix 原生机制**
- 使用 Reasonix 的 `spawn_subagent` 工具（内置 `parallelSafe: true`）
- 完全复用 Reasonix 的前缀缓存架构（99%+ 缓存命中率）
- 零额外子进程/线程管理

### 2. **极致的缓存友好**
- Reasonix 的 immutable prefix + append-only log
- 子 Agent 共享相同的工具定义前缀
- 跨会话缓存复用

### 3. **并行执行**
- 3 个 `spawn_subagent` 工具调用由 Reasonix 自动并行调度
- 零手动线程/进程管理

## 🚀 快速开始

### 1. 安装 Reasonix

```bash
npm install -g reasonix
```

### 2. 复制 Skill 文件

```bash
# 在你的项目中
mkdir -p .reasonix/skills
cp skills/collusion-design.md .reasonix/skills/
```

### 3. 启动 Reasonix

```bash
cd your-project
reasonix code
```

### 4. 运行 Collusion Skill

```
/skill collusion-design
```

然后按照提示，描述你的设计任务！

## 📁 目录结构

```
/workspace/
├── skills/
│   └── collusion-design.md  # Reasonix Skill 剧本
├── src/
│   ├── reasonix_blackboard.py  # 黑板文件管理工具（可选辅助）
│   └── ...（原有文件，保留用于参考）
├── REASONIX_COLLUSION.md    # 架构设计文档
└── README_REASONIX.md       # 本文档
```

## 🧠 架构原理

```
┌───────────────────────────────────────────────────────┐
│              Reasonix 主循环                        │
│  ┌─────────────────────────────────────────────────┐ │
│  │  主 Agent (通过 /skill collusion-design)     │ │
│  └─────────────────────────────────────────────────┘ │
│                      │                              │
│  ┌───────────────────▼─────────────────────────────┐ │
│  │  Reasonix 并行工具调度器 (3 个 spawn_subagent) │ │
│  │  parallelSafe: true → 同时运行               │ │
│  └─────────────────────────────────────────────────┘ │
│  ┌──────────┐    ┌──────────────┐   ┌─────────────┐│
│  │   UX     │    │   架构       │   │   安全      ││
│  │  Agent   │    │   Agent      │   │   Agent     ││
│  │ (flash)  │    │ (flash/pro)  │   │  (flash)    ││
│  └──────────┘    └──────────────┘   └─────────────┘│
│                      │                              │
│  ┌───────────────────▼─────────────────────────────┐ │
│  │        文件系统黑板 (~/.reasonix/collusion/)   │ │
│  └─────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

## 🎯 使用流程

### 在 Reasonix 中的对话示例

```
User: /skill collusion-design

Reasonix (collusion): 请描述你要设计的系统！

User: 设计一个高并发的待办事项应用，支持 100 万用户...

Reasonix (collusion): 好的，现在并行启动 3 个 Agent 进行提案...

[ 后台: Reasonix 自动并行调用 3 个 spawn_subagent ]

Reasonix (collusion): 收到所有提案！现在进入交叉评审阶段...

[ 继续流程直到最终方案 ]

Reasonix (collusion): 完成！最终方案已保存到黑板。
```

## 💰 成本优化

利用 Reasonix 的成本控制机制：

| Agent | 默认模型 | 说明 |
|-------|----------|------|
| UX/业务 | `deepseek-v4-flash` | 成本敏感，不需要超强推理 |
| 技术架构 | `flash` / `pro` | 复杂任务可用 pro |
| 安全合规 | `flash` | 通常足够 |

## 📊 缓存命中率

Reasonix 原生架构保证：
- **90%+** 的前缀缓存命中
- **只追加** 的对话日志
- **零修改** 的系统 prompt + 工具定义

## 🔧 黑板管理工具（可选）

```python
from src.reasonix_blackboard import (
    init_task,
    save_proposal,
    get_all_proposals,
    save_final_report,
    list_tasks,
)

# 初始化任务
task = init_task("my-design-001", "设计一个聊天系统")

# 保存提案
save_proposal("my-design-001", "ux", "# UX 方案\n...")

# 读取所有提案
proposals = get_all_proposals("my-design-001")
```

## 📚 原有代码说明

保留的原有文件（`src/orchestrator.py`, `src/blackboard.py` 等）主要用于：
1. 参考和对比
2. 不使用 Reasonix 时的替代方案
3. 理解原始设计思路

但 **推荐使用 Reasonix 原生方案**！

## 🎉 总结

**旧方案（已废弃）**:
- 手动管理 subprocess.Popen
- 需要自己实现进程监控
- 无法利用 Reasonix 前缀缓存
- ❌ 不能在 Reasonix 宿主中良好运行

**新方案（当前）**:
- ✅ 完全基于 Reasonix `spawn_subagent`
- ✅ 自动并行调度
- ✅ 前缀缓存 99%+ 命中
- ✅ 零手动进程/线程管理
- ✅ 完美适配 Reasonix 宿主环境

---

**让 Reasonix 做它最擅长的事！**
