#!/bin/bash
# Parallel Skill 冷启动测试
set -e

TEST_ID="cs_$(date +%s)"
OUT_DIR="/d/BrainstormOrchestrator/tmp/parallel_test/$TEST_ID"
mkdir -p "$OUT_DIR"

TASK="设计一个团队协作Wiki知识库系统（TeamWiki）的后端技术方案。
要求：
1. 支持Markdown实时协作编辑
2. 全文搜索 + 语义搜索
3. 版本历史/回滚
4. 权限管理（组织->空间->页面三级）
5. 100人团队，10000+页面

请输出完整技术方案，包括架构图（ASCII art）、技术栈推荐、API设计、数据库schema。"

ARCHITECT_SYSTEM="你是后端架构师。专注API设计、数据库Schema、技术栈选型、系统可扩展性。
输出格式：1.整体架构(ASCII art) 2.技术栈推荐 3.核心API设计 4.数据库Schema 5.扩展性考量。中文输出。"

SECURITY_SYSTEM="你是安全专家。专注认证授权、注入防护、数据加密、审计日志。
输出：1.认证方案(JWT/Session/OAuth2) 2.三级权限模型 3.Web安全防护(XSS/CSRF/SQL注入) 4.数据加密策略 5.审计日志设计。中文输出。"

PERFORMANCE_SYSTEM="你是性能分析师。专注缓存策略、数据库优化、并发处理、搜索性能。
输出：1.缓存分层策略(CDN->应用->DB) 2.数据库优化 3.全文搜索方案(ES/PostgreSQL FTS/Meilisearch) 4.实时协作并发控制(CRDT/OT) 5.容量规划。中文输出。"

echo "============================================"
echo "Parallel Skill 冷启动测试"
echo "============================================"
echo "测试ID: $TEST_ID"
echo "输出目录: $OUT_DIR"
echo "模型: deepseek-chat (--no-config, 跳过MCP)"
echo "开始时间: $(date '+%H:%M:%S')"
echo ""

START_TIME=$(date +%s)

echo "启动 3 个 Agent (并行)..."
echo ""

npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$ARCHITECT_SYSTEM" \
  --transcript "$OUT_DIR/architect.jsonl" \
  --no-config \
  > "$OUT_DIR/architect_output.md" 2>"$OUT_DIR/architect_stderr.log" &
PID_ARCH=$!

npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$SECURITY_SYSTEM" \
  --transcript "$OUT_DIR/security.jsonl" \
  --no-config \
  > "$OUT_DIR/security_output.md" 2>"$OUT_DIR/security_stderr.log" &
PID_SEC=$!

npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$PERFORMANCE_SYSTEM" \
  --transcript "$OUT_DIR/performance.jsonl" \
  --no-config \
  > "$OUT_DIR/performance_output.md" 2>"$OUT_DIR/performance_stderr.log" &
PID_PERF=$!

echo "架构师 PID=$PID_ARCH | 安全专家 PID=$PID_SEC | 性能分析师 PID=$PID_PERF"

wait $PID_ARCH $PID_SEC $PID_PERF

END_TIME=$(date +%s)
WALL_TIME=$((END_TIME - START_TIME))

echo ""
echo "============================================"
echo "全部完成 — 墙钟时间: ${WALL_TIME}s ($((WALL_TIME / 60))m $((WALL_TIME % 60))s)"
echo "============================================"
echo ""

TOTAL_KB=0
TOTAL_LINES=0
for ROLE in architect security performance; do
  TXT="$OUT_DIR/${ROLE}.jsonl"
  OUTPUT="$OUT_DIR/${ROLE}_output.md"
  KB=0; LINES=0
  [ -f "$OUTPUT" ] && KB=$(du -k "$OUTPUT" | cut -f1) && LINES=$(wc -l < "$OUTPUT")
  TOTAL_KB=$((TOTAL_KB + KB))
  TOTAL_LINES=$((TOTAL_LINES + LINES))
  echo "= $ROLE: ${KB}KB, ${LINES}行 ="
  if [ -f "$TXT" ]; then
    npx reasonix stats "$TXT" 2>&1 || true
  fi
  echo ""
done
echo "总计: ${TOTAL_KB}KB, ${TOTAL_LINES}行"

{
  echo "test_id=$TEST_ID"
  echo "model=deepseek-chat"
  echo "wall_time=${WALL_TIME}s"
  echo "total_output=${TOTAL_KB}KB"
  echo "total_lines=${TOTAL_LINES}"
} > "$OUT_DIR/summary.txt"
