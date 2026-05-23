#!/bin/bash
task_file="D:/BrainstormOrchestrator/tmp/parallel_test/pycode_warm_1779173376\go_race_task.txt"
sys="你是Go并发编程专家。精通race detector和sync包。先分析再输出修复代码。禁止调用MCP工具。"
model="deepseek-v4-flash"
npx reasonix run "$(cat "$task_file")" -m "$model" --system "$sys" --no-config
