下面是一个基于 `OrderedDict` 实现的线程安全 LRU 缓存类。它使用 `threading.Lock` 保护所有共享状态，保证并发安全，同时利用 `OrderedDict` 的 `move_to_end` 和 `popitem` 达到 O(1) 的 `get` / `put` 开销。

```python
import threading
from collections import OrderedDict
from typing import Optional, Any

class ThreadSafeLRUCache:
    """线程安全的 LRU 缓存，容量固定，自动淘汰最久未使用的条目。"""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("容量必须为正整数")
        self.capacity = capacity
        self._cache: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        """获取键对应的值，若不存在返回 default。
        访问后将该键提升为最近使用。
        """
        with self._lock:
            if key not in self._cache:
                return default
            self._cache.move_to_end(key)   # 移到末尾 = 最近使用
            return self._cache[key]

    def put(self, key: Any, value: Any) -> None:
        """插入或更新键值对。若容量已满则淘汰最久未使用的条目。"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.capacity:
                # 移除最久未被使用的（第一个）
                self._cache.popitem(last=False)
            self._cache[key] = value

    def delete(self, key: Any) -> None:
        """删除指定键，若不存在则静默忽略。"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def clear(self) -> None:
        """清空所有缓存项。"""
        with self._lock:
            self._cache.clear()

    # ---------- 便利方法 / 魔法方法 ----------

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __getitem__(self, key: Any) -> Any:
        val = self.get(key)
        # 保持与内置 dict 一致：不存在时抛 KeyError
        if val is None and key not in self:
            raise KeyError(key)
        return val

    def __setitem__(self, key: Any, value: Any) -> None:
        self.put(key, value)

    def __delitem__(self, key: Any) -> None:
        self.delete(key)

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"{self.__class__.__name__}(capacity={self.capacity}, "
                f"items={len(self._cache)})"
            )
```

## 使用示例

```python
cache = ThreadSafeLRUCache(capacity=2)

cache.put("a", 1)
cache.put("b", 2)
cache.get("a")          # 1 → "a" 变为最近使用
cache.put("c", 3)       # 容量已满，淘汰最久未使用的 "b"
assert "b" not in cache
assert cache.get("a") == 1
assert cache.get("c") == 3

cache["d"] = 4          # 淘汰 "a"，因为 "c" 和 "d" 更新
assert "a" not in cache

# 线程安全测试
from concurrent.futures import ThreadPoolExecutor

def worker(key):
    cache.put(key, key)

with ThreadPoolExecutor(max_workers=10) as pool:
    pool.map(worker, range(100))

print(len(cache))       # 最多为 capacity = 2
```

## 关键设计说明

| 方面 | 选择 |
|------|------|
| **数据结构** | `OrderedDict`，`move_to_end` 和 `popitem(last=False)` 均为 O(1) |
| **线程安全** | 一个 `threading.Lock` 保护所有方法，`with self._lock` 保证互斥 |
| **get 默认值** | `get(key, default=None)` 避免 KeyError，同时提供 `__getitem__` 模拟字典行为（不存在时抛 KeyError） |
| **容量限制** | 插入时若已满则 `popitem(last=False)` 淘汰最老的条目 |
| **可扩展性** | 可以轻松添加 `setdefault`, `get_or_create`, `invalidate` 等方法，只需套上锁即可 |

## 为什么不用 `functools.lru_cache`？

标准库的 `@lru_cache` 在 CPython 下虽因 GIL 看似线程安全，但官方文档**未作保证**，且它只能装饰函数，无法作为独立容器使用。自己实现可以在各种 Python 实现（如 Jython、PyPy 无 GIL 模式）下都正确工作。

## 如果需要更底层的实现（双向链表 + 散列表）

面试中有时会要求手工实现链表。只需在前面代码中将 `self._cache` 替换为自定义的 `HashMap<Node>` + 头尾哨兵双向链表，同样用锁保护所有操作即可。核心逻辑不变：**加锁 → 查找 / 移除 / 插入 → 解锁**。

— turns:1 cache:96.4% cost:$0.000844 save-vs-claude:98.3%
