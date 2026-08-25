"""绘图组件：调用配置的绘图后端生成图片并落盘到 data/images。"""

from .drawing_manager import DrawingManager, drawing_manager

__all__ = ["DrawingManager", "drawing_manager"]
