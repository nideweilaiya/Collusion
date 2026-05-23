"""检查点注册表 — Phase 3 实现"""


class CheckpointRegistry:
    """检查点注册与发现"""

    def __init__(self):
        self._checkpoints = {}

    def register(self, checkpoint_cls):
        self._checkpoints[checkpoint_cls.checkpoint_id] = checkpoint_cls

    def get(self, checkpoint_id: str):
        return self._checkpoints.get(checkpoint_id)

    @property
    def core_checkpoints(self):
        return [c for c in self._checkpoints
                if hasattr(c, 'category') and str(c.category) == 'core']

    @property
    def deep_checkpoints(self):
        return [c for c in self._checkpoints
                if hasattr(c, 'category') and str(c.category) == 'deep']

    @property
    def all_checkpoints(self):
        return list(self._checkpoints.values())
