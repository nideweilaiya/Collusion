```python
import threading
from collections import OrderedDict

class LRUCache:
    """线程安全的 LRU 缓存（最近最少使用淘汰）"""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self._cache = OrderedDict()          # 保持插入顺序
        self._lock = threading.Lock()        # 保护所有读写操作

    def get(self, key):
        """获取键对应的值，不存在返回 -1。将访问过的键标记为最近使用。"""
        with self._lock:
            if key not in self._cache:
                return -1
            self._cache.move_to_end(key)     # 移到末尾 → 表示最近使用
            return self._cache[key]

    def put(self, key, value):
        """写入键值对。如果已存在则更新，并标记为最近使用；否则插入。
        超出容量时淘汰最久未使用的项。"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key) # 已存在 → 提前移动
            self._cache[key] = value
            if len(self._cache) > self.capacity:
                self._cache.popitem(last=False)  # 弹出最早插入的项

    def delete(self, key):
        """显式删除一个键，不存在则忽略。"""
        with self._lock:
            self._cache.pop(key, None)

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def __len__(self):
        with self._lock:
            return len(self._cache)

    def clear(self):
        """清空所有缓存项。"""
        with self._lock:
            self._cache.clear()

# ---- 使用示例 ----
if __name__ == "__main__":
    cache = LRUCache(2)
    cache.put(1, "a")
    cache.put(2, "b")
    print(cache.get(1))   # "a" (访问键1，使其成为最近使用)
    cache.put(3, "c")     # 淘汰键2（最近最少使用）
    print(cache.get(2))   # -1 (已被淘汰)
    print(cache.get(3))   # "c"
```

**要点说明**  
1. **线程安全**：用 `threading.Lock` 保护 `get`、`put`、`delete`、`clear`、`__contains__`、`__len__` 等所有公开方法。  
2. **LRU 语义**：基于 `OrderedDict` 的内部有序性。每次访问（`get` / `put`）时将对应键移到末尾，淘汰时从头部弹出，实现了 O(1) 读写。  
3. **容量检查**：写入后若超出容量则 `popitem(last=False)` 弹出最早插入的键（即最久未被使用的项）。  
4. **边界处理**：容量必须 >0；`get` 不存在的键返回 -1（类似标准 `lru_cache`的设计）。

— turns:1 cache:96.4% cost:$0.000395 save-vs-claude:98.4%
