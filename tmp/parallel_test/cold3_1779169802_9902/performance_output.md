I searched the workspace using `Glob` and the `explore` subagent for `TeamWiki` / `teamwiki`. **No existing codebase or project was found.** The following is a greenfield design based on general best practices for a team wiki / knowledge base system.

---

## TeamWiki 缓存与搜索性能方案


### 一、整体架构原则

知识库业务**读远多于写**（一个编辑可能被阅读几百次），所以核心思路是：
1. **缓存优先** — 极速加载已渲染的页面。
2. **搜索分层** — 防止慢查询拖垮数据库。
3. **缓存失效可靠** — 写操作立刻广播清除，保证最终一致性。

---

### 二、缓存方案

#### 1. 分层缓存布局 (Multi‑Tier Caching)

| 层级 | 存储 | 对象与TTL | 目标延迟 |
|---|---|---|---|
| L0 浏览器 | CDN / Service Worker | 静态资源 (JS/CSS)，长 TTL (1y) | < 5ms |
| L1 进程内 | `lru-cache` (Node/Python) | 热门页面 HTML、当前用户权限 (短 TTL，maxSize 控制) | < 1ms |
| L2 分布式 | **Redis** | 页面本体、目录树、搜索高频结果 | < 5ms |
| L3 数据库 | PostgreSQL 物化视图 | 搜索热表、聚合统计 | < 50ms |

#### 2. 关键缓存键与失效
```text
wiki:page:{id}:rendered   → 渲染后的 HTML（写时逐出）
wiki:page:{id}:source     → 原始 markdown / json（写时逐出）
wiki:space:{id}:tree      → 嵌套目录树（页面新增/移动时逐出）
wiki:user:{id}:perms      → 用户有权访问的空间/页面列表（权限变更时逐出）
wiki:search:top           → 热门搜索词结果（每小时过期）
```

- **失效机制**：利用 **Redis Pub/Sub** 或 **PostgreSQL LISTEN/NOTIFY**。当 `page.updated` 事件触发 → 发布 `invalidate:page:{id}` 消息，所有订阅节点同时清除本地和 Redis 中的对应缓存。

#### 3. 缓存防崩措施
- **缓存击穿**：高频热点页面，使用 Redis `SETNX` 实现互斥锁，只放行一个请求重建缓存（其他请求回退到 DB 或等待）。
- **缓存雪崩**：给缓存 TTL 加上随机偏移量（即 `base TTL + random(0, 600)`），避免同时过期。

---

### 三、搜索性能方案

#### 1. 引擎选型
- **首选：Elasticsearch（推荐）** — 成熟、支持复杂权限过滤、实时索引。
- **极简替代：Meilisearch** — 开箱即用、自带中文分词、运维成本低，适合团队 < 50 人的场景。
- **兜底方案：PostgreSQL FTS (`tsvector` + GIN 索引)** — 不引入外部组件，但仅建议在页面数 < 5 万时使用。

#### 2. 索引 Schema 与权限处理（关键设计）

**`pages` 索引文档结构：**
```json
{
  "id": 1,
  "title": "K8s 部署指南",
  "content": "...",
  "tags": ["运维", "Kubernetes"],
  "space_id": 5,
  "creator": "zhangsan",
  "created_at": "2024-01-01",
  "updated_at": "2024-06-01",
  "view_count": 1200,
  // ⭐ 权限外挂字段
  "allowed_team_ids": [1, 2, 3],
  "allowed_user_ids": [42, 99]
}
```

**查询时的权限过滤：**
```json
{
  "query": {
    "bool": {
      "must": [{
        "multi_match": {
          "query": "K8s",
          "fields": ["title^3", "content", "tags^2"]
        }
      }],
      "filter": [{
        "bool": {
          "should": [
            { "terms": { "allowed_team_ids": [1] } },
            { "term":  { "allowed_user_ids": 42 } }
          ]
        }
      }],
      "function_score": {
        "functions": [
          { "gauss": { "updated_at": { "origin": "now", "scale": "30d" } }},
          { "field_value_factor": { "field": "view_count", "modifier": "log1p" }}
        ]
      }
    }
  }
}
```

**为什么这样设计？**
- 将**权限判断下压到搜索引擎**，利用倒排索引一次过滤。
- 避免「查全部 → 查权限 → 应用层过滤」这种 O(N) 模式，稳定在 O(logN)。
- 用户会话中只需缓存 `allowed_team_ids` 列表（Redis / LRU），非常轻量。

#### 3. 数据同步管道
```
PostgreSQL → WAL → Debezium → Kafka → Indexer Service → Elasticsearch
```
- 使用 CDC 流式同步，延迟 < 1 秒。
- 附加**定时全量重建**（每日凌晨），修复可能的数据不一致。

#### 4. 搜索功能支撑
| 功能 | 实现方式 |
|---|---|
| 搜索建议 / Auto‑Complete | Elasticsearch `completion suggester` 或 edge‑n‑gram 过滤器 |
| 拼写纠错 | `match` 查询下开启 `fuzziness: AUTO` |
| 精准匹配高权重 | `match_phrase` 对 `title` 加权 |
| 深度分页 | 使用 `search_after` 游标分页 |
| 聚合筛选 | 对 `space_id`, `tags` 做 `terms` aggregation |

---

### 四、兜底与监控

- **熔断降级**：当 Elasticsearch P99 延迟 > 500ms 时，自动回退到 PostgreSQL FTS 查询（牺牲排序精度，保证可用性）。
- **监控大盘**：
  - Redis 缓存命中率（目标 > 90%），未命中来源跟踪。
  - ES 查询延迟直方图（P50 / P99）。
  - CDC 同步延迟（当前最新索引时间与数据库时间的差值）。
- **快速恢复**：管理员一键 `POST /admin/cache/purge` 或 `POST /admin/search/reindex`。

— turns:1 cache:96.4% cost:$0.002100 save-vs-claude:98.2%
