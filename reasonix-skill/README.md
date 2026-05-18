# Collusion — Multi-Agent Design Engine for Reasonix

A multi-perspective technical design engine that integrates with Reasonix as an external MCP tool. Three AI agents independently generate, cross-review, and rank technical proposals — all while preserving Reasonix's cache through MCP Sampling delegation.

## Quick Start

```bash
# 1. Install
pip install collusion-mcp

# 2. Start MCP server (keep running)
collusion-mcp --sse --port 8020

# 3. Add to Reasonix MCP settings
# Settings → MCP → Add: http://localhost:8020/sse
```

No separate API key required. Collusion auto-detects Reasonix's configuration.

## Usage

In Reasonix, start your message with `Collusion` or `共谋`:

```
用 Collusion 设计一个支持过期时间和密码的文件分享服务
```

Available modes:
- **scheme** — Full technical design with Top 3 ranked proposals
- **review** — Code review (security/perf/maintainability)
- **plan** — Task decomposition
- **diagnose** — Fault diagnosis
- **choose** — Technology selection
- **scout** — Project reconnaissance
- **blackboard** — Background agent collaboration

## How It Works

Collusion runs as an MCP server alongside Reasonix. When you trigger it:

1. Collusion spawns 3 AI agents (UX, Architecture, Security) as subprocesses
2. Each agent independently generates a complete design proposal
3. Agents cross-review each other's work
4. A feasibility check eliminates over-engineering
5. Multi-dimensional voting produces a ranked Top 3

All LLM calls are delegated to Reasonix via `sampling/createMessage`, preserving Reasonix's 99% cache hit rate.

## Cost

| Config | Cost | Time |
|---|---|---|
| 1 Agent | ¥0.04 | ~2 min |
| 3 Agents | ¥0.16 | ~5 min |
| Blackboard (hybrid) | ¥0.20-0.30 | ~5-8 min |

Costs are borne by the host (Reasonix) via its own API connection.

## Troubleshooting

### API Key Not Configured

Collusion auto-detects Reasonix's API key. If it fails:
```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### MCP Server Failed to Start

```bash
# Check MCP package installed
python -c "import mcp; print(mcp.__version__)"

# Check port availability (Windows)
netstat -ano | findstr :8020

# Check port availability (macOS/Linux)
lsof -i :8020
```

### Blackboard Merge Returns Empty Rankings

Check status before merging:
```
collusion_blackboard_status(task_id="bb_xxxx")
```

Wait until all agents show `_done`. If an agent shows `_error`, check the error field. Then merge:
```
collusion_blackboard_merge(task_id="bb_xxxx")
```

## Benchmark

Collusion beat single-shot LLM generation **6:1** (8 ties) in blind evaluations across 5 domains, 25 total dimensions. The multi-perspective approach consistently produces more complete, feasible, and innovative designs.

## License

MIT — see [LICENSE](../LICENSE)
