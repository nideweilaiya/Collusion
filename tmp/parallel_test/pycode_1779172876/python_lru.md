好的，这里给出一个简洁、线程安全的 LRU 缓存实现。

它只用 `collections.OrderedDict` + `threading.Lock`，核心思路是：  
- 用 `OrderedDict` 维护插入/访问顺序，最近访问的 key 通过 `move_to_end` 移到末尾。  
- 达到容量上限时，用 `popitem(last=False)` 移除链表头部的“最久未使用”项。  
- 所有 `get`、`put` 操作都通过同一个锁 `_lock` 保护，保证多线程下数据一致。

```python
import threading
from collections import OrderedDict


class LRUCache:
    """线程安全的 LRU 缓存，容量固定，最近最少使用淘汰。"""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()  # 保护所有对 _cache 的复合操作

    def get(self, key):
        """获取 key 对应的 value，若不存在返回 None。
        访问后该 key 会被标记为最近使用。
        """
        with self._lock:
            if key in self._cache:
                # 移到 OrderedDict 末尾表示“最近使用”
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key, value):
        """写入 key-value，若 key 已存在则更新值并标记为最近使用；
        若达到容量上限则淘汰最久未使用的项。
        """
        with self._lock:
            if key in self._cache:
                # 更新值，并移到末尾
                self._cache[key] = value
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.capacity:
                    # popitem(last=False) 弹出并删除第一个元素（最久未使用）
                    self._cache.popitem(last=False)
                self._cache[key] = value

    def delete(self, key):
        """显式删除一个 key，若不存在则静默忽略。"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """清空所有缓存项。"""
        with self._lock:
            self._cache.clear()

    def __len__(self):
        """当前缓存项数量（线程安全）。"""
        with self._lock:
            return len(self._cache)

    def __contains__(self, key):
        """支持 in 判断（线程安全）。"""
        with self._lock:
            return key in self._cache

    def __repr__(self):
        with self._lock:
            return f"{type(self).__name__}(capacity={self.capacity}, items={list(self._cache.items())})"
```

---

### 为什么是线程安全的？

- `OrderedDict` 本身的单个操作（如 `move_to_end`、`popitem`）虽然由 GIL 保护，但 `if key in cache` + `move_to_end` 是**复合操作**，多个线程交错执行就可能读到不一致状态。  
- 通过在 `get`、`put` 等公开方法中统一加 `self._lock`，把整个“检查+修改”逻辑变成原子的，从而避免**竞态条件**（race condition）。

---

### 使用示例

```python
cache = LRUCache(2)

cache.put("a", 1)
cache.put("b", 2)
print(cache.get("a"))   # 1 —— a 变为最近使用
cache.put("c", 3)       # 容量满，淘汰 "b"
print(cache.get("b"))   # None —— 被淘汰
print(cache.get("c"))   # 3
```

多线程演示（安全）：

```python
import time, random, threading

cache = LRUCache(1000)

def worker(n):
    for i in range(500):
        cache.put(f"k_{i}_{n}", i)
        cache.get(f"k_{i}_{n}")
        time.sleep(random.random() * 0.001)

threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(f"final size: {len(cache)}")  # 不超过 1000，不出错
```

---

### 性能权衡

- 单把锁的实现简单可靠，适合中等并发场景。如果读多写少且对延迟敏感，可以考虑读写锁（`threading.Lock` 无法区分读写，但 `threading.RLock` 允许递归）。  
- 更高并发场景可改用分段锁或 `cachetools` 等第三方库，但这对大多数应用已经够用。

— turns:1 cache:96.4% cost:$0.000680 save-vs-claude:98.3%
