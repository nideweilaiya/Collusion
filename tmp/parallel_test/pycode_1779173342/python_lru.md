以下是完整的线程安全 LRU 缓存实现，基于 `OrderedDict` + `threading.Lock`，支持并发 `get`/`put`，自动淘汰最久未访问项。

```python
import threading
from collections import OrderedDict

class LRUCache:
    """线程安全的 LRU 缓存，容量固定（maxsize）。"""
    def __init__(self, maxsize: int = 128):
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self.maxsize = maxsize
        self.cache = OrderedDict()
        self.lock = threading.Lock()   # 保证所有操作原子性

    def get(self, key, default=None):
        """获取 key，命中则将 key 移至末尾（最近使用）。未命中返回 default。"""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)   # 标记为最近使用
                return self.cache[key]
            return default

    def put(self, key, value):
        """写入 key-value。若已存在则更新并移至末尾；若缓存满则淘汰最久未使用项。"""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)   # 淘汰最早插入的项

    def delete(self, key):
        """显式删除 key，无副作用。"""
        with self.lock:
            self.cache.pop(key, None)

    def clear(self):
        """清空缓存。"""
        with self.lock:
            self.cache.clear()

    def __len__(self):
        with self.lock:
            return len(self.cache)

    def __contains__(self, key):
        with self.lock:
            return key in self.cache
```

---

### 使用示例

```python
cache = LRUCache(maxsize=3)

cache.put("a", 1)
cache.put("b", 2)
cache.put("c", 3)

print(cache.get("a"))    # 1，此时顺序：b->c->a（a被移到末尾）
cache.put("d", 4)        # 超出容量，淘汰最久未使用的 b
print(cache.get("b"))    # None（已被淘汰）
print(len(cache))        # 3
```

---

### 线程安全说明

- 所有读写操作均通过 `self.lock` 保护，多线程下 `get`/`put`/`delete` 互斥执行，不会出现脏读、写丢失或内部数据结构损坏。
- `OrderedDict.move_to_end` 和 `popitem` 本身不是线程安全的，但在锁的保护下可放心使用。
- 由于 CPython 的 GIL 仅保护单条字节码，但多个操作复合（检查 + 移动 + 赋值）必须用显式锁保证原子性。上述实现满足该要求。

---

### 可扩展方向（未在基本需求中，可自行添加）

- **可选的 TTL 过期**：在 `put` 时记录时间戳，`get` 时检查是否过期，过期则删除并返回 `default`。
- **`__getitem__`/`__setitem__`**：实现 `cache[key]` 语法，内部调用 `get`/`put`。
- **统计命中率**：记录 `hits`/`misses` 计数器，暴露 `hit_rate` 属性。
- **`maxsize=0` 表示无限制**：可根据需要调整 `__init__` 逻辑。

— turns:1 cache:96.4% cost:$0.000460 save-vs-claude:98.4%
