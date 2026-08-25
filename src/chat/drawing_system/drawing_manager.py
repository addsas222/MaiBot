"""绘图管理器：按配置分发到对应绘图后端，并把生成结果落盘。"""

from datetime import datetime
from pathlib import Path
import uuid

from src.common.logger import get_logger
from src.common.utils.image_path import serialize_stored_image_path
from src.config.config import global_config

from .image_backends import comfyui_generate, openai_compat_generate, sd_webui_generate

logger = get_logger("drawing_manager")

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.absolute().resolve()
DATA_DIR = PROJECT_ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"


class DrawingManager:
    """绘图生成管理器。"""

    async def generate_and_save(self, prompt: str, negative_prompt: str = "") -> tuple[str, bytes]:
        """按全局配置的后端生成图片并写入 data/images 落盘子目录。

        Args:
            prompt: 正向提示词。
            negative_prompt: 负向提示词，仅 comfyui / sd_webui 后端支持。

        Returns:
            tuple[str, bytes]: 相对项目根目录的存储路径与图片二进制。
        """
        image_cfg = global_config.image_generation
        provider = str(image_cfg.provider or "").strip()
        timeout_seconds = float(image_cfg.timeout_seconds)

        if provider == "openai_compat":
            image_bytes = await openai_compat_generate(
                prompt,
                cfg=image_cfg.openai_compat,
                size=image_cfg.openai_compat.size,
                timeout=timeout_seconds,
            )
        elif provider == "comfyui":
            image_bytes = await comfyui_generate(
                prompt,
                cfg=image_cfg.comfyui,
                negative_prompt=negative_prompt,
                timeout=timeout_seconds,
            )
        elif provider == "sd_webui":
            image_bytes = await sd_webui_generate(
                prompt,
                cfg=image_cfg.sd_webui,
                negative_prompt=negative_prompt,
                timeout=timeout_seconds,
            )
        else:
            raise ValueError(f"未知的绘图 provider: {provider}")

        sub_dir = str(image_cfg.output_sub_dir or "").strip()
        target_dir = IMAGE_DIR / sub_dir if sub_dir else IMAGE_DIR
        # 写盘前校验子目录仍在 data/images 边界内，拒绝 '..'/绝对路径穿越；
        # 若先写盘后校验，边界检查形同虚设
        try:
            target_dir.resolve().relative_to(IMAGE_DIR.resolve())
        except ValueError as exc:
            raise ValueError(f"绘图落盘子目录越界，已拒绝写入: {sub_dir!r}") from exc
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.png"
        file_path = target_dir / file_name
        file_path.write_bytes(image_bytes)
        stored_path = serialize_stored_image_path(file_path)

        logger.info(f"绘图完成: provider={provider} 存储路径={stored_path} 大小={len(image_bytes)} 字节")
        return stored_path, image_bytes


drawing_manager = DrawingManager()
