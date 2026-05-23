#!/bin/bash
task_file="D:/BrainstormOrchestrator/tmp/parallel_test/pycode_1779173342\react_hook_task.txt"
sys="你是React性能优化专家。精通useMemo/useCallback。输出重构后代码。禁止调用MCP工具。"
model="deepseek-v4-flash"
npx reasonix run "$(cat "$task_file")" -m "$model" --system "$sys" --no-config
