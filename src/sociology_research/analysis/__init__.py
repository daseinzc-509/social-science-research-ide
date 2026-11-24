"""论文分析领域子包：按版面去噪、证据账本、书目元数据、方法学评审与报告渲染分层。"""

from .reporting import build_deep_reading_report, render_deep_reading_markdown

__all__ = [
    "build_deep_reading_report",
    "render_deep_reading_markdown",
]
