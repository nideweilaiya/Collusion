下面是 Python 实现的线程安全 LRU 缓存类，使用 `collections.OrderedDict` 管理有序缓存项，`threading.Lock` 保证并发安全。

```python
from collections import OrderedDict
import threading

class LRUCache:
    """Least Recently Used (LRU) Cache, thread-safe."""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        """Return value for key, or None if not present.
        Moves accessed key to end (most recent)."""
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def put(self, key, value):
        """Insert or update key-value pair.
        Evicts least recently used item if at capacity."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.capacity:
                # evict the first (least recently used) item
                self._cache.popitem(last=False)
            self._cache[key] = value

    def delete(self, key):
        """Remove key if it exists. Return True if removed, else False."""
        with self._lock:
            try:
                self._cache.pop(key)
                return True
            except KeyError:
                return False

    def clear(self):
        """Remove all items."""
        with self._lock:
            self._cache.clear()

    def __len__(self):
        with self._lock:
            return len(self._cache)

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def items(self):
        """Snapshot of all (key, value) pairs, from LRU to MRU."""
        with self._lock:
            return list(self._cache.items())

# ---------- 使用示例 ----------
if __name__ == "__main__":
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    print(cache.get("a"))   # 1 (a 变为最近使用)
    cache.put("c", 3)       # 容量 2 → 淘汰 b (LRU)
    print(cache.get("b"))   # None (已被淘汰)
    print(cache.get("c"))   # 3
    print(cache.get("a"))   # 1 (a 仍存在)
```

### 关键设计说明

- **`OrderedDict`**：键值对会保持插入顺序，`popitem(last=False)` 取出最早插入的键值对（即 LRU 项），`move_to_end(key)` 将访问过的键移到末尾（标记为最近使用）。
- **`threading.Lock`**：所有公开读写方法都通过 `with self._lock` 包裹，确保同一时刻只有一个线程能操作缓存。
- **线程安全粒度**：每个 `get` / `put` 调用都是原子操作，不会出现并发读写导致丢失更新或顺序错乱。
- **扩展性**：`delete`、`clear`、`items` 等方法同样受锁保护，保持内部状态一致。

此实现适合多线程环境下的读多写少场景，平均时间复杂度 O(1)。如果还需要支持 TTL 过期、统计信息等，可在 `__init__` 中增加 `time` 相关字段，并在 `get` / `put` 时做检查。

— turns:1 cache:96.4% cost:$0.000539 save-vs-claude:98.3%
