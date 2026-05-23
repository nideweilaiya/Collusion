请把你要重构的自定义 Hook 代码贴出来，这样我才能给出**具体、可验证**的优化建议（比如减少不必要的重渲染、缓存计算结果、避免闭包陷阱等）。

如果你还没有粘贴，下面是一些通用的性能优化方向，你可以对照检查：

1. **避免不必要的状态更新**  
   - 用 `useMemo`/`useCallback` 缓存值或回调，只有当依赖变化时才重新计算。  
   - 如果 state 是引用类型（对象/数组），确保 setState 传入的是新引用而不是原地修改。

2. **减少渲染次数**  
   - 把经常变化的值拆到单独的 Context Provider 里，避免祖先节点重渲染连带消费同一 Context 的其他子节点。  
   - 使用 `useSyncExternalStore` 替代 `useState` + `useEffect` 订阅外部 store，防止 tearing。

3. **优化副作用**  
   - `useEffect` 中清楚列出所有依赖；如果确实不需要响应某些依赖变化，考虑用 `useRef` 持有可变值。  
   - 对于高频触发的事件（scroll、resize），用 `throttle`/`debounce` 或 `useEffect` 的 cleanup 及时移除监听。

4. **懒加载与拆分**  
   - 如果 Hook 内部逻辑很重（比如复杂计算或大量数据转换），用 `useDeferredValue` 或 `startTransition` 延迟非紧急更新。  
   - 把不相关的逻辑拆成多个轻量 Hook，方便按需复用和独立优化。

**拿到你的代码后，我会逐行分析、给出 SEARCH/REPLACE 形式的修改建议，并附上相关的源码位置引用。**

— turns:1 cache:96.6% cost:$0.000226 save-vs-claude:98.6%
