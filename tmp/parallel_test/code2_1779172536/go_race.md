您没有附上具体的代码片段，请把需要分析的 Go 代码粘贴过来，我会：

1. 指出哪些变量/操作存在 data race  
2. 说明为什么这些访问是不同步的（缺少锁、channel 误用等）  
3. 给出一个**修复后的完整代码**（通常加 `sync.Mutex` / `sync.RWMutex` / channel 或 `atomic`）并解释改动点。

直接贴代码，我来分析。

— turns:1 cache:95.9% cost:$0.000092 save-vs-claude:98.9%
