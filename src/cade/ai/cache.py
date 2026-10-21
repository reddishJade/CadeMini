"""缓存统计。

统一的缓存统计口径，确保跨请求一致性。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CacheUsage:
    """缓存使用统计。

    命中率公式：hit / (hit + miss)，而非 hit / prompt_tokens。
    """

    hit_tokens: int = 0
    miss_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """缓存总 token 数（命中 + 未命中）。"""
        return self.hit_tokens + self.miss_tokens

    @property
    def hit_rate(self) -> float:
        """缓存命中率。"""
        total = self.total_tokens
        return round(self.hit_tokens / total, 4) if total > 0 else 0.0
