#!/bin/bash
TASK=$(cat "D:/BrainstormOrchestrator/tmp/parallel_test/pycode_1779173149\react_hook_task.txt")
npx reasonix run "$TASK" -m deepseek-v4-flash --system "你是React性能优化专家。精通useMemo/useCallback/React.memo。输出重构后的完整代码。禁止使用任何MCP工具。pycode_1779173149" --no-config
