from typing import Dict, List, Optional, Set

import threading
import time

from src.common.logger import get_logger

logger = get_logger("model_cooldown")


class ModelCooldownRegistry:
    """全局模型冷却登记表（线程安全）。

    记录每个模型因 429 限流等错误进入冷却的到期时间；冷却期内模型不会被选中，
    到期后自动恢复可用。冷却采用阶梯策略：模型连续因 429 进入冷却时，冷却时长
    从基础时长起逐级翻倍，最高不超过封顶时长；模型成功调用后清零连续次数。
    """

    def __init__(self) -> None:
        self._cooldown_until: Dict[str, float] = {}
        # 模型连续因 429 进入冷却的次数，用于阶梯冷却递增；成功调用后清零
        self._cooldown_strikes: Dict[str, int] = {}
        self._lock = threading.Lock()

    def enter_cooldown(
        self, model_name: str, cooldown_seconds: int, cooldown_max_seconds: int = 0
    ) -> None:
        """登记模型进入冷却。

        Args:
            model_name: 模型名称。
            cooldown_seconds: 基础冷却时长（秒）；小于等于 0 时不进入冷却。
            cooldown_max_seconds: 阶梯冷却封顶时长（秒）；小于等于基础时长时不启用阶梯，
                恒为基础时长。
        """
        if cooldown_seconds <= 0:
            return
        with self._lock:
            strikes = self._cooldown_strikes.get(model_name, 0) + 1
            self._cooldown_strikes[model_name] = strikes
            if cooldown_max_seconds > cooldown_seconds:
                # 阶梯冷却：连续次数越高冷却越长，最高不超过封顶时长
                duration = min(cooldown_seconds * (2 ** (strikes - 1)), cooldown_max_seconds)
            else:
                duration = cooldown_seconds
            cooldown_until = time.time() + duration
            self._cooldown_until[model_name] = cooldown_until
        logger.info(
            f"模型 '{model_name}' 进入冷却（原因 429，连续第 {strikes} 次，冷却 {duration} 秒，"
            f"冷却至 {time.strftime('%H:%M:%S', time.localtime(cooldown_until))}）"
        )

    def reset_strikes(self, model_name: str) -> None:
        """模型成功调用后清零连续冷却次数，并解除冷却登记。"""
        with self._lock:
            self._cooldown_until.pop(model_name, None)
            self._cooldown_strikes.pop(model_name, None)

    def is_in_cooldown(self, model_name: str) -> bool:
        """检查模型是否处于冷却期；已到期则自动清除登记并返回 False。"""
        with self._lock:
            cooldown_until = self._cooldown_until.get(model_name)
            if cooldown_until is None:
                return False
            if time.time() >= cooldown_until:
                del self._cooldown_until[model_name]
                logger.info(f"模型 '{model_name}' 冷却结束，恢复可用")
                return False
            return True

    def get_earliest_recovery_model(self, model_names: List[str]) -> Optional[str]:
        """返回给定模型中冷却结束最早的模型名称；若均未处于冷却期则返回 None。"""
        earliest_name: Optional[str] = None
        earliest_until = float("inf")
        with self._lock:
            for name in model_names:
                cooldown_until = self._cooldown_until.get(name)
                if cooldown_until is not None and cooldown_until < earliest_until:
                    earliest_until = cooldown_until
                    earliest_name = name
        return earliest_name


model_cooldown_registry = ModelCooldownRegistry()


class ModelIsolationRegistry:
    """模型隔离登记表（线程安全）。

    记录因 403 权限错误等硬错误被隔离的模型；被隔离的模型在本次运行中不再被选中，
    不随时间自动恢复，重启进程后隔离状态清空。
    """

    def __init__(self) -> None:
        self._isolated: Set[str] = set()
        self._lock = threading.Lock()

    def isolate(self, model_name: str) -> None:
        """将模型登记为隔离状态。"""
        with self._lock:
            self._isolated.add(model_name)
        logger.warning(f"模型 '{model_name}' 因 403 进入隔离，本次运行将不再使用，重启后重新探测")

    def is_isolated(self, model_name: str) -> bool:
        """检查模型是否处于隔离状态。"""
        with self._lock:
            return model_name in self._isolated


model_isolation_registry = ModelIsolationRegistry()
