
[tool AI_Wiki_search_files] ERROR: Access denied - path outside allowed directories: D:\ not in D:\引擎\AI Wiki

[tool AI_Wiki_list_allowed_directories] Allowed directories:
D:\引擎\AI Wiki

[tool AI_Wiki_write_file] Successfully wrote to D:\引擎\AI Wiki\lru_cache.py
已创建 `lru_cache.py`。下面是实现说明：

---

## 设计思路

**核心结构**：`OrderedDict` + `threading.Lock`

- `OrderedDict` 保证 O(1) 的 `move_to_end` / `popitem` 操作，天然适合 LRU 语义
- `threading.Lock` 用 `with self._lock` 包裹所有公开方法，保证线程安全

### 接口一览

| 方法 | 说明 |
|---|---|
| `get(key)` → `Optional[VT]` | 获取值，命中后标记为最近使用；未命中返回 `None` |
| `get_or_compute(key, factory)` | **双检查锁模式** — 锁外计算，减少临界区；计算期间其他线程竞争时二次检查 |
| `put(key, value)` | 插入/更新，超容量时淘汰最久未使用的条目 |
| `delete(key)` → `bool` | 删除指定键 |
| `clear()` | 清空全部并重置统计 |
| `keys()` / `values()` / `items()` | 按 **最近→最久** 顺序返回快照 |
| `stats()` → `dict` | 返回命中率、容量、大小等统计信息 |
| `resize(new_capacity)` | 运行时调整容量，自动淘汰多余条目 |

### 关键设计取舍

1. **`get_or_compute` 的双检查锁模式**
   ```python
   with self._lock:          # ① 第一次检查（锁内）
       if key in self._cache:
           return self._cache[key]
   value = factory()         # ② 锁外计算
   with self._lock:          # ③ 第二次检查（锁内）
       if key in self._cache:  # 防止重复计算
           return self._cache[key]
       self._cache[key] = value
       self._evict_if_needed()
   ```
   既避免了对同一 key 的重复计算，又将计算过程移出临界区减少锁争用。

2. **统计埋点** — `_hits` / `_misses` 每次访问更新，`stats()` 给出命中率，方便调优容量。

3. **运行时 `resize`** — 支持动态缩减容量，自动淘汰最久未用的条目。

4. **避免用 `RLock`** — 此类对外接口互斥即可，内部不存在递归调用，用普通 `Lock` 更轻量。

### 使用示例

```python
from lru_cache import LRUCache

cache = LRUCache[str, bytes](capacity=1024)

# 基础读写
cache.put("config.json", data)
cached = cache.get("config.json")

# 惰性加载
result = cache.get_or_compute("expensive_query", lambda: db.query(...))

# 统计
if cache.stats()["hit_rate"] < 0.5:
    cache.resize(2048)  # 命中率低，扩容
```

— turns:4 cache:97.0% cost:$0.001123 save-vs-claude:99.2%
