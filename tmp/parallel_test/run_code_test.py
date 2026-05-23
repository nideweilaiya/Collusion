"""
Parallel Skill 编程任务缓存测试 v2
修复编码问题 + 不用 markdown fence 以避免模型忽略代码块
"""
import subprocess, time, os

TEST_ID = f"pycode_{int(time.time())}"
OUT_DIR = f"D:/BrainstormOrchestrator/tmp/parallel_test/{TEST_ID}"
os.makedirs(OUT_DIR, exist_ok=True)

# 不用 markdown ``` 代码块，直接用自然语言分隔
TASKS = {
    "python_lru": (
        "用Python实现一个线程安全的LRU缓存类，要求：\n"
        "1. 支持指定最大容量(capacity)\n"
        "2. 支持 get(key) 和 put(key, value) 操作\n"
        "3. 支持过期时间(TTL)，过期条目自动清理\n"
        "4. 线程安全(使用threading.Lock)\n"
        "5. 包含类型注解和docstring\n"
        "6. 包含使用示例\n\n"
        "禁止使用任何工具。禁止调用MCP。直接输出完整Python代码。"
    ),
    "go_race": (
        "请分析这段Go代码的并发问题并给出修复后的完整代码。\n\n"
        "待修复代码:\n"
        "package main\n\n"
        "import (\n"
        '    "fmt"\n'
        '    "sync"\n'
        ")\n\n"
        "type Counter struct {\n"
        "    value int\n"
        "}\n\n"
        "func (c *Counter) Increment(wg *sync.WaitGroup) {\n"
        "    defer wg.Done()\n"
        "    for i := 0; i < 1000; i++ {\n"
        "        c.value++\n"
        "    }\n"
        "}\n\n"
        "func main() {\n"
        "    c := &Counter{}\n"
        "    var wg sync.WaitGroup\n"
        "    for i := 0; i < 100; i++ {\n"
        "        wg.Add(1)\n"
        "        go c.Increment(&wg)\n"
        "    }\n"
        "    wg.Wait()\n"
        '    fmt.Println(c.value)\n'
        "}\n\n"
        "问题：指出race condition位置和原理，输出修复后的完整代码。\n"
        "禁止使用任何工具。禁止调用MCP。"
    ),
    "react_hook": (
        "请重构这个React自定义hook以优化性能。\n\n"
        "待重构代码:\n"
        "import { useState, useEffect } from 'react';\n\n"
        "interface UserData {\n"
        "  id: number;\n"
        "  name: string;\n"
        "  email: string;\n"
        "  role: string;\n"
        "  lastLogin: Date;\n"
        "  permissions: string[];\n"
        "}\n\n"
        "function useUserFilter(users: UserData[], searchTerm: string) {\n"
        "  const [filteredUsers, setFilteredUsers] = useState<UserData[]>([]);\n\n"
        "  useEffect(() => {\n"
        "    const filtered = users\n"
        "      .filter(u => u.name.toLowerCase().includes(searchTerm.toLowerCase()))\n"
        "      .sort((a, b) => a.name.localeCompare(b.name));\n"
        "    setFilteredUsers(filtered);\n"
        "  }, [users, searchTerm]);\n\n"
        "  return {\n"
        "    filteredUsers,\n"
        "    adminCount: filteredUsers.filter(u => u.role === 'admin').length,\n"
        "    recentLogins: filteredUsers.filter(u => {\n"
        "      const daysSinceLogin = (Date.now() - u.lastLogin.getTime()) / (1000 * 60 * 60 * 24);\n"
        "      return daysSinceLogin < 7;\n"
        "    })\n"
        "  };\n"
        "}\n\n"
        "要求：使用useMemo避免不必要的重计算，保持功能不变。输出完整重构后的代码。\n"
        "禁止使用任何工具。禁止调用MCP。"
    ),
}

SYSTEMS = {
    "python_lru": "你是Python高级工程师。擅长并发编程、数据结构。输出可执行代码。禁止调用MCP工具。",
    "go_race": "你是Go并发编程专家。精通race detector和sync包。先分析再输出修复代码。禁止调用MCP工具。",
    "react_hook": "你是React性能优化专家。精通useMemo/useCallback。输出重构后代码。禁止调用MCP工具。",
}

def run_agent(name, task, system, model, odir):
    out_file = os.path.join(odir, f"{name}.md")
    err_file = os.path.join(odir, f"{name}_err.log")
    task_file = os.path.join(odir, f"{name}_task.txt")

    with open(task_file, "w", encoding="utf-8") as tf:
        tf.write(task)

    wrapper = os.path.join(odir, f"{name}_run.sh")
    with open(wrapper, "w", encoding="utf-8") as wf:
        wf.write('#!/bin/bash\n')
        wf.write(f'task_file="{task_file}"\n')
        wf.write(f'sys="{system}"\n')
        wf.write(f'model="{model}"\n')
        wf.write('npx reasonix run "$(cat "$task_file")" -m "$model" --system "$sys" --no-config\n')

    with open(out_file, "w", encoding="utf-8") as out, open(err_file, "w", encoding="utf-8") as err:
        return subprocess.Popen(["bash", wrapper], stdout=out, stderr=err)

# ===== 冷启动 =====
print("=" * 60)
print("  编程任务缓存测试 v2")
print("=" * 60)
print(f"ID: {TEST_ID} | 模型: deepseek-v4-flash | MCP: 已清空")
print(f"开始: {time.strftime('%H:%M:%S')}")

start = time.time()
procs = {}
for name in ["python_lru", "go_race", "react_hook"]:
    procs[name] = run_agent(name, TASKS[name], SYSTEMS[name], "deepseek-v4-flash", OUT_DIR)
    print(f"启动 {name} (PID={procs[name].pid})")

for name, p in procs.items():
    p.wait()

wall = int(time.time() - start)
print(f"\n冷启动墙钟: {wall}s\n")

for name in ["python_lru", "go_race", "react_hook"]:
    out_file = os.path.join(OUT_DIR, f"{name}.md")
    with open(out_file, encoding="utf-8") as f:
        content = f.read()
    kb = len(content) / 1024
    lines = content.count("\n")
    print(f"--- {name}: {kb:.0f}KB, {lines}行 ---")
    print(f"    尾: {content.strip().split(chr(10))[-1][:120]}")
    print(f"    头: {content[:80].strip()}")
    print()

# ===== Warm (相同 model + system, 同 model) =====
print("=" * 60)
print("  Warm 轮 (deepseek-v4-flash, 相同 system prompt)")
print("=" * 60)

TID2 = f"pycode_warm_{int(time.time())}"
OUT2 = f"D:/BrainstormOrchestrator/tmp/parallel_test/{TID2}"
os.makedirs(OUT2, exist_ok=True)

start2 = time.time()
procs2 = {}
for name in ["python_lru", "go_race", "react_hook"]:
    procs2[name] = run_agent(name, TASKS[name], SYSTEMS[name], "deepseek-v4-flash", OUT2)
    print(f"启动 {name} (PID={procs2[name].pid})")

for name, p in procs2.items():
    p.wait()

wall2 = int(time.time() - start2)
print(f"\nWarm墙钟: {wall2}s\n")

for name in ["python_lru", "go_race", "react_hook"]:
    out_file2 = os.path.join(OUT2, f"{name}.md")
    with open(out_file2, encoding="utf-8") as f:
        content = f.read()
    kb = len(content) / 1024
    lines = content.count("\n")
    print(f"--- {name}: {kb:.0f}KB, {lines}行 ---")
    print(f"    尾: {content.strip().split(chr(10))[-1][:120]}")
    print(f"    头: {content[:80].strip()}")
    print()

print(f"冷 {wall}s / Warm {wall2}s | 冷输出: {OUT_DIR}")
