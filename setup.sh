#!/usr/bin/env bash
# Collusion 一键安装脚本 v1.0
# 自动检测宿主平台并配置 MCP 连接
set -euo pipefail

RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' CYAN='\033[0;36m' NC='\033[0m'

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Collusion (共谋) 一键安装 v0.4.0${NC}"
echo -e "${CYAN}  多视角协作技术方案编排引擎${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# ---- 1. 检查 Python ----
echo -e "${YELLOW}[1/5] 检查 Python 环境...${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            echo -e "  ${GREEN}✓ $cmd $ver${NC}"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo -e "${RED}错误: 需要 Python 3.10+，请先安装${NC}"
    exit 1
fi

# ---- 2. 安装 Collusion ----
echo -e "${YELLOW}[2/5] 安装 Collusion MCP 包...${NC}"
if [ -f "pyproject.toml" ]; then
    echo "  检测到源码目录，使用 pip install -e ."
    "$PYTHON" -m pip install -e . --quiet
else
    echo "  从 PyPI 安装 collusion-mcp"
    "$PYTHON" -m pip install collusion-mcp --quiet
fi
echo -e "  ${GREEN}✓ collusion-mcp 已安装${NC}"

# ---- 3. 检测 API Key ----
echo -e "${YELLOW}[3/5] 检测 API Key...${NC}"
KEY_OK=0
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    echo -e "  ${GREEN}✓ 检测到 DEEPSEEK_API_KEY 环境变量${NC}"
    KEY_OK=1
elif [ -f ~/.reasonix/config.json ]; then
    echo -e "  ${GREEN}✓ 检测到 Reasonix 配置（零配置模式）${NC}"
    KEY_OK=1
elif [ -f config.json ]; then
    key=$(python3 -c "import json; print(json.load(open('config.json')).get('api_key',''))" 2>/dev/null || echo "")
    if [ -n "$key" ] && [ "$key" != "sk-your-api-key-here" ]; then
        echo -e "  ${GREEN}✓ 检测到 config.json${NC}"
        KEY_OK=1
    fi
fi
if [ $KEY_OK -eq 0 ]; then
    echo -e "  ${YELLOW}⚠ 未检测到 API Key${NC}"
    echo -e "  请设置环境变量: ${CYAN}export DEEPSEEK_API_KEY=\"sk-...\"${NC}"
    echo -e "  或注册免费 Key: ${CYAN}https://platform.deepseek.com${NC}"
fi

# ---- 4. 检测宿主平台 ----
echo -e "${YELLOW}[4/5] 检测宿主平台...${NC}"
HOST=""
CONFIG_FILE=""
CONFIG_CONTENT=""

# 检测 Claude Code
if command -v claude &>/dev/null || [ -f ~/.claude/settings.json ]; then
    HOST="claude-code"
    CONFIG_FILE=".mcp.json"
    CONFIG_CONTENT='{
  "mcpServers": {
    "brainstorm": {
      "command": "collusion-mcp",
      "args": ["--stdio"]
    }
  }
}'
    echo -e "  ${GREEN}✓ 检测到 Claude Code${NC}"

# 检测 Reasonix
elif [ -f ~/.reasonix/config.json ]; then
    HOST="reasonix"
    CONFIG_FILE="reasonix.mcp.json"
    echo -e "  ${GREEN}✓ 检测到 Reasonix（零配置模式）${NC}"

# 检测 Cursor
elif command -v cursor &>/dev/null || [ -d ~/.cursor ]; then
    HOST="cursor"
    CONFIG_FILE=".cursor/mcp.json"
    CONFIG_CONTENT='{
  "mcpServers": {
    "brainstorm": {
      "command": "collusion-mcp",
      "args": ["--stdio"]
    }
  }
}'
    echo -e "  ${GREEN}✓ 检测到 Cursor${NC}"

# 检测 Trae Solo
elif [ -d .trae ] || [ -f .trae/mcp.json ]; then
    HOST="trae-solo"
    CONFIG_FILE=".trae/mcp.json"
    CONFIG_CONTENT='{
  "mcpServers": {
    "brainstorm": {
      "command": "collusion-mcp",
      "args": ["--sse", "--port", "8020"]
    }
  }
}'
    echo -e "  ${GREEN}✓ 检测到 Trae Solo${NC}"

else
    HOST="unknown"
    echo -e "  ${YELLOW}⚠ 未检测到已知宿主${NC}"
    echo -e "  支持: Claude Code / Cursor / Reasonix / Trae Solo"
    echo -e "  请手动参考 platform 目录下的配置模板"
fi

# ---- 5. 写入 MCP 配置 ----
if [ -n "$CONFIG_CONTENT" ] && [ -n "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}[5/5] 写入 MCP 配置 → $CONFIG_FILE${NC}"
    echo "$CONFIG_CONTENT" > "$CONFIG_FILE"
    echo -e "  ${GREEN}✓ 配置已写入${NC}"
elif [ "$HOST" = "reasonix" ]; then
    echo -e "${YELLOW}[5/5] Reasonix MCP 配置${NC}"
    echo -e "  请在 Reasonix MCP 设置中添加 SSE 端点："
    echo -e "  URL: ${CYAN}http://localhost:8020/sse${NC}"
    echo -e "  或手动启动: ${CYAN}collusion-mcp --sse --port 8020${NC}"
else
    echo -e "${YELLOW}[5/5] 手动配置${NC}"
    echo -e "  请将 platform/ 目录下的配置模板复制到对应位置"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Collusion 安装完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "使用方法（在 AI 编码助手中）："
echo "  /collusion scheme <任务描述>    — 多视角方案设计"
echo "  /collusion review <文件路径>    — 代码审查"
echo "  /collusion plan <任务描述>      — 任务拆解"
echo "  /collusion diagnose <异常现象>  — 问题诊断"
echo "  /collusion choose <选型问题>    — 技术选型"
echo ""
if [ "$HOST" = "unknown" ]; then
    echo "更多平台配置模板: platform/ 目录"
fi
