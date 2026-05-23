#!/bin/bash
task_file="D:/BrainstormOrchestrator/tmp/parallel_test/pycode_1779173342\python_lru_task.txt"
sys="你是Python高级工程师。擅长并发编程、数据结构。输出可执行代码。禁止调用MCP工具。"
model="deepseek-v4-flash"
npx reasonix run "$(cat "$task_file")" -m "$model" --system "$sys" --no-config
