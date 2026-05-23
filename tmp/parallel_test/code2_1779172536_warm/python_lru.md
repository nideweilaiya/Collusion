```python
import threading
from collections import OrderedDict
from typing import TypeVar, Generic, Optional

KT = TypeVar("KT")
VT = TypeVar("VT")


class LRUCache(Generic[KT, VT]):
    """线程安全的 LRU 缓存，容量固定，自动驱逐最久未使用的条目。"""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._cache: OrderedDict[KT, VT] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def get(self, key: KT) -> Optional[VT]:
        """返回 key 对应的值；如果不存在返回 None（同时不改变顺序）。"""
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)  # 标记为最近使用
            return self._cache[key]

    def put(self, key: KT, value: VT) -> None:
        """插入或更新 key。若缓存满且 key 不存在则驱逐最久未使用的条目。"""
        with self._lock:
            if key in self._cache:
                self._cache[key] = value
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._capacity:
                    self._cache.popitem(last=False)  # 移除最早插入的
                self._cache[key] = value

    def delete(self, key: KT) -> None:
        """显式删除 key。"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
```

**说明**  

- 基于 `OrderedDict` 实现 LRU 语义：`get` 时用 `move_to_end` 刷新顺序，`put` 时在容量满且 key 不存在时弹出最久未使用的条目（`popitem(last=False)`）。  
- 所有读写操作统一由 `self._lock` 保护，即使 Python 有 GIL，`OrderedDict` 的复合操作（存在判断 + 移动/弹出）也不是原子的，显式加锁是保证线程安全的正确做法。  
- 类型标注 `Generic[KT, VT]` 使缓存可被静态检查器正确推导。  

如果需要进一步扩展（如 TTL 过期、统计命中率、`__contains__` 支持等），可以在 `_lock` 保护下按相同模式添加。

— turns:1 cache:96.4% cost:$0.000388 save-vs-claude:98.4%
