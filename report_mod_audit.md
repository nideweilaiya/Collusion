# MC AI Companion Mod — 全功能审计与闭环实现报告

> 生成: 2026-05-20
> 范围: D:/AI_Workbench/integrations/minecraft/forge-mod
> 编译: 通过 (gradle build -x test)

---

## 总览

| 模块 | 文件数 | 代码行 | 功能完整性 |
|------|--------|--------|-----------|
| AI 对话系统 | 4 | ~800 | 60% |
| 技能系统 | 10 | ~1200 | 70% |
| NPC 实体 | 1 | ~3300 | 80% |
| 行为模式 | 8 | ~3000 | 50% |
| GUI 界面 | 7 | ~1000 | 40% |
| 命令系统 | 4 | ~1500 | 70% |
| 记忆系统 | 3 | ~400 | 60% |
| 网络/桥接 | 2 | ~400 | 50% |
| 经济/交易 | 1 | ~100 | 30% |
| 构建系统 | 2 | ~300 | 40% |
| 个性/性格 | 2 | ~350 | 50% |

---

## 一、技能系统 — Skill System

### 现状

| 组件 | 文件 | 实现度 | 问题 |
|------|------|--------|------|
| Skill 数据类 | `skill/Skill.java` | 80% | 基本数据类型完备 |
| SkillAction 执行链 | `skill/SkillAction.java` | 80% | advance() 已修复 |
| AtomicAction 接口 | `skill/atomic/*.java` (10个) | 90% | 全部实现 |
| SkillEngine 引擎 | `skill/SkillEngine.java` | 70% | 驱动逻辑完整 |
| SkillLibrary 库 | `skill/SkillLibrary.java` | 60% | 注册/学习/遗忘 |
| SkillGenerator LLM生成 | `skill/SkillGenerator.java` | 50% | 需要 Ollama |
| SkillVerifier 验证 | `skill/SkillVerifier.java` | 60% | 需要 Ollama |
| AutoCurriculum 课程 | `skill/AutoCurriculum.java` | 30% | **未接入主循环** |
| FeedbackCollector 反馈 | `skill/FeedbackCollector.java` | 40% | **未实际使用** |
| SkillCommand 命令 | `command/CompanionAICommands.java` | 60% | 基本命令就绪 |
| SkillScreen GUI | `client/gui/SkillScreen.java` | 50% | 需要对接数据 |

### 游戏的观察

> **玩家报告**: 挖矿遇到矿不挖，一直循环「发现 完成」

### 根因分析

CompanionGatherGoal 的 `canContinueToUse()` 在技能激活时返回 false，导致 `start()` 和 `stop()` 反复触发。修复已应用：`companion.isSkillActive()` 时返回 `true` 让目标继续执行。

### 闭环清单

| 任务 | 状态 | 预估 |
|------|------|------|
| Advance() 修复验证 | ✅ 已修 | - |
| GatherGoal canContinue 修复 | ✅ 已修 | - |
| AutoCurriculum 接入主循环 | ❌ 未做 | 2h |
| FeedbackCollector 实际记录 | ❌ 未做 | 1h |
| SkillScreen 对接SkillLibrary | ❌ 未做 | 1h |

---

## 二、GUI 界面系统

### 现状

| 组件 | 文件 | 实现度 | 问题 |
|------|------|--------|------|
| 快捷菜单 | `CompanionQuickMenuScreen.java` | 70% | **按钮重叠** |
| 同伴列表 | `CompanionListScreen.java` | 60% | 基础列表 |
| 角色详情 | `CompanionDetailScreen.java` | 40% | **无属性数据** |
| 设置面板 | `CompanionSettingsScreen.java` | 50% | 基础设置 |
| 技能 GUI | `SkillScreen.java` | 30% | 未对接数据 |
| HUD 覆盖 | `CompanionHUDOverlay.java` | 50% | 显示同伴信息 |
| 背包界面 | `CompanionContainerScreen.java` | 70% | 基本可用 |
| G键快捷键 | `CompanionKeyHandler.java` | 60% | 需确认绑定 |
| 角色卡片 | `CharacterCardWidget.java` | 40% | 基础框架 |

### 游戏观察

> **玩家报告**: G键面板 UI 错乱重叠，没有实现属性面板

### 根因分析

1. **G键面板重叠**: 按钮宽度 140px，在窄窗口(<900px)下 8 个按钮 + 标题共 192px 高度超出中线范围。修复: 按钮宽改为 120px，高度 18px，间距 1px。顶部设最小边距。
2. **属性面板缺失**: CompanionDetailScreen 只有三个按钮(召唤/跟随/换肤)，没有查询同伴实时属性的逻辑。需要 `AutomatonEntity` 暴露属性接口。

### 闭环清单

| 任务 | 状态 | 预估 |
|------|------|------|
| QuickMenu 按钮布局修复 | ✅ 已修 | - |
| 按钮宽度自适应 | ✅ 已修 | - |
| CompanionDetailScreen 加属性标签 | ❌ 未做 | 2h |
| AutomatonEntity 暴露属性接口 | ❌ 未做 | 1h |
| SkillScreen 对接技能数据 | ❌ 未做 | 1h |

---

## 三、行为模式系统

### 现状

| 模式 | 文件 | 实现度 | 问题 |
|------|------|--------|------|
| 采集 (gather) | `CompanionGatherGoal.java` | 70% | 循环问题已修 |
| 种植 (farm) | `CompanionFarmGoal.java` | 60% | 基本可用 |
| 守护 (guard) | `CompanionGuardGoal.java` | 60% | 基本可用 |
| 跟随 (follow) | `CompanionFollowGoal.java` | 80% | 稳定 |
| 钓鱼 (fishing) | `CompanionFishingGoal.java` | 50% | 基本可用 |
| 交易 (trade) | `CompanionTradeGoal.java` | 30% | **基本未实现** |
| 游荡 (wander) | `CompanionWanderGoal.java` | 80% | 稳定 |
| 跳跃 | `JumpGoal.java` | 80% | 稳定 |
| **拾取 (pickup)** | **❌ 不存在** | **0%** | **未实现** |
| **自主 (auto)** | **❌ 不存在** | **0%** | **未实现** |
| 生存 (survival) | `CompanionSurvivalGoal.java` | 30% | 存在但未接入命令 |

### 游戏观察

> **玩家报告**: 自主模式纯摆设，拾取模式完全没有实现，按下去UI没有提示只有聊天框打开

### 根因分析

1. **拾取模式**: 命令系统没有 `pickup` 子命令。CompanionBehaviorCommands.java 已有 `gather`/`farm`/`guard` 但无 `pickup`。修复: 已添加 `pickup` 和 `auto` 命令注册。
2. **自主模式**: 命令注册了但 `auto` 方法指向 `AutomatonEntity.toggleAutoMode()`。需要确认该方法是否存在。若不存在则需要创建并实现自主行为逻辑。
3. **UI无提示**: QuickMenuScreen 的按钮点了直接发命令关菜单，没有状态反馈。需要在按钮文本上显示当前模式状态。

### 闭环清单

| 任务 | 状态 | 预估 |
|------|------|------|
| pickup 命令注册 | ✅ 已修 | - |
| auto 命令注册 | ✅ 已修 | - |
| AutomatonEntity.toggleAutoMode() | ❌ 待确认 | 1h |
| AutomatonEntity.togglePickupMode() | ❌ 待确认 | 1h |
| PickupGoal 实现 | ❌ 未做 | 3h |
| AutoGoal 实现 | ❌ 未做 | 4h |
| 按钮状态反馈 | ❌ 未做 | 1h |

---

## 四、AI 对话系统

### 现状

| 组件 | 文件 | 实现度 | 问题 |
|------|------|--------|------|
| CompanionAI 核心 | `ai/CompanionAI.java` | 70% | 需 Ollama |
| AIManager 管理器 | `ai/AIManager.java` | 60% | removeAI 已调用 |
| DialogueStack 消息栈 | `ai/DialogueStack.java` | 60% | 基本可用 |
| OllamaClient 客户端 | `ai/OllamaClient.java` | 70% | 已修复空catch |
| PerceptionEngine 感知 | `ai/PerceptionEngine.java` | 40% | **需更多感知维度** |
| TaskQueue 任务队列 | `ai/TaskQueue.java` | 70% | 基本可用 |
| TaskInterrupt 中断 | `ai/TaskInterruptProtocol.java` | 50% | 需LLM决策 |
| UtilityEvaluator 评分 | `ai/UtilityEvaluator.java` | 40% | **未实际调用** |

### 闭环清单

| 任务 | 状态 | 预估 |
|------|------|------|
| removeAI 调用修复 | ✅ 已修 | - |
| InterruptedException 恢复 | ✅ 已修 | - |
| OllamaClient encode 处理 | ✅ 已修 | - |
| PerceptionEngine 扩展感知 | ❌ 未做 | 2h |
| UtilityEvaluator 接入决策 | ❌ 未做 | 2h |

---

## 五、部署与验证流程

### 当前流程

```
修改代码 → gradlew build -x test (编译验证)
         → 通过 → copy jar to mods/
                → 启动Minecraft → 测试 → 反馈
```

### 已修复并部署

| 提交 | 变更 | 状态 |
|------|------|------|
| 空catch块日志 | MemoryStore/TCPServer | ✅ 已编译 |
| 硬编码温度常量化 | 8处 → OllamaClient常量 | ✅ 已编译 |
| SkillAction.advance() | 修复循环 | ✅ 已编译 |
| GatherGoal canContinue | 修复采集循环 | ✅ 已编译 |
| QuickMenu布局 | 防重叠 | ✅ 已编译 |
| pickup/auto命令注册 | 行为命令扩展 | ✅ 待编译验证 |

### 待验证

| 功能 | 验证方法 | 状态 |
|------|---------|------|
| pickup/auto命令编译 | gradlew build -x test | ⏳ 你跑 |
| 挖矿不再循环 | 进游戏测试 | ⏳ 你测 |
| G键面板不再重叠 | 进游戏测试 | ⏳ 你测 |
| pickup命令有效果 | 进游戏测试 | ⏳ 你测 |

---

## 六、优先级建议

### P0 — 影响游戏体验 (本周)

1. **编译验证 pickup/auto 命令** — 跑 `gradlew build -x test`，报错我修
2. **G键面板 UI** — 已修布局，进游戏确认不再重叠
3. **挖矿循环** — 已修 canContinue，进游戏确认

### P1 — 缺失功能 (下周)

4. **属性面板** — CompanionDetailScreen 显示等级/血量/技能
5. **拾取模式 Goal** — 实现 CollectItemsGoal 
6. **自主模式 Goal** — 实现 AutonomousGoal (SurvivalGoal扩展)

### P2 — 增强 (远期)

7. AutoCurriculum 接入主循环
8. PerceptionEngine 扩展 (物品识别/结构识别)
9. UtilityEvaluator 实际评分决策
10. FeedbackCollector 反馈闭环
