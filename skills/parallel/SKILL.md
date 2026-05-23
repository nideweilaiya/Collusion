---
name: parallel
description: 多Agent并行执行引擎。parallel_schedule MCP工具，3 Worker并行设计方案或执行编程任务，替代subagent，固定system prompt ~90%缓存。
---

# Parallel — 多Agent并行执行引擎

调用 `parallel_schedule` MCP 工具并行启动多个 Worker。每个 Worker 是独立 Reasonix 会话，固定 system prompt。

## 触发

用户输入 `/parallel <任务>` 或"并行设计/并行修复"时触发。

## 执行方式

### 方案设计

```
/parallel 设计短链接服务
→ parallel_schedule(task="设计短链接服务", mode="design")
→ 立即返回 task_id
→ 等待 60s → parallel_status(task_id, seq=1)
→ 若 running → 等 15s → parallel_status(task_id, seq=2)
→ 重复直到 completed → 展示 architect/security/performance 三份方案
```

### 编程执行

```
/parallel 并行修复 bug
→ parallel_schedule(
    tasks=["修复 storage.py", "修复 formatter.py"],
    mode="code",
    workdir="D:/项目路径")
→ 每个 Worker 独立改文件 → 改完验证
→ parallel_status 轮询直到 completed
```

## 轮询规则

1. 每次 parallel_status 传不同 seq (1, 2, 3...递增)
2. 间隔 ≥10 秒
3. 最多 8 次，超时告知用户
4. completed 时停止

## 缓存

| 轮次 | 命中率 |
|------|--------|
| 首轮 (冷) | 0% |
| 第2轮 | ~85% |
| 第3轮+ | ~95% |
