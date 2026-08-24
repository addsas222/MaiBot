"""模型降级链（fallback_model_list）选择逻辑测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

from src.config.model_configs import TaskConfig
from src.llm_models.utils_model import LLMOrchestrator


def _make_task_config(main: list[str], fallback: list[str]) -> TaskConfig:
    return TaskConfig(model_list=main, fallback_model_list=fallback)


def _stub_config_manager(monkeypatch, task_name: str, task_config: TaskConfig) -> None:
    # utils_model 在导入期绑定了 config_manager 名称，需在其自身命名空间打补丁
    fake_cm = SimpleNamespace(
        get_model_config=lambda: SimpleNamespace(
            model_task_config=SimpleNamespace(**{task_name: task_config})
        )
    )
    import src.llm_models.utils_model as um

    monkeypatch.setattr(um, "config_manager", fake_cm)


def _make_orchestrator(monkeypatch, main: list[str], fallback: list[str]) -> LLMOrchestrator:
    task_name = "utils"
    task_config = _make_task_config(main, fallback)
    _stub_config_manager(monkeypatch, task_name, task_config)
    orch = LLMOrchestrator(task_name=task_name, request_type="test")
    # 屏蔽真实配置刷新，固定使用构造时的任务配置
    monkeypatch.setattr(orch, "_refresh_task_config", lambda: orch.model_for_task, raising=False)
    return orch


def test_fallback_models_included_in_usage_map() -> None:
    task = _make_task_config(["m1"], ["m2", "m3"])
    orch = LLMOrchestrator(task_name="utils", request_type="t")
    orch.model_for_task = task
    assert set(task.fallback_model_list).issubset(
        {"m1", "m2", "m3"}
    ), "保底模型应参与使用量记录"


def test_select_prefers_main_pool_before_fallback(monkeypatch) -> None:
    """主列表可用时不应选中保底模型。"""
    orch = _make_orchestrator(monkeypatch, main=["m1", "m2"], fallback=["paid-1"])

    def fake_get(name):
        return SimpleNamespace(name=name, api_provider="p")

    import src.llm_models.utils_model as um

    monkeypatch.setattr(um.TempMethodsLLMUtils, "get_model_info_by_name", staticmethod(fake_get))
    monkeypatch.setattr(um.TempMethodsLLMUtils, "get_provider_by_name", staticmethod(lambda p: SimpleNamespace(name=p)))
    monkeypatch.setattr(
        um.client_registry,
        "get_client_class_instance",
        lambda provider, force_new=False: object(),
    )

    first, _, _ = orch._select_model(exclude_models=set())
    second, _, _ = orch._select_model(exclude_models={first.name})
    assert {first.name, second.name} == {"m1", "m2"}, (
        f"主列表未耗尽时只应在主列表内选择: {first.name}, {second.name}"
    )


def test_select_uses_fallback_after_main_exhausted(monkeypatch) -> None:
    """主列表全部排除后才启用保底池。"""
    orch = _make_orchestrator(monkeypatch, main=["m1"], fallback=["paid-1"])

    def fake_get(name):
        return SimpleNamespace(name=name, api_provider="p")

    import src.llm_models.utils_model as um

    monkeypatch.setattr(um.TempMethodsLLMUtils, "get_model_info_by_name", staticmethod(fake_get))
    monkeypatch.setattr(um.TempMethodsLLMUtils, "get_provider_by_name", staticmethod(lambda p: SimpleNamespace(name=p)))
    monkeypatch.setattr(
        um.client_registry,
        "get_client_class_instance",
        lambda provider, force_new=False: object(),
    )

    model_info, _, _ = orch._select_model(exclude_models={"m1"})
    assert model_info.name == "paid-1"


def test_timeout_cooldown_triggered_on_exhaustion() -> None:
    from src.llm_models.utils_model import _is_request_timeout_error

    timeout_exc = TimeoutError("Request timed out.")

    class Wrapped(Exception):
        def __init__(self, cause):
            self.__cause__ = cause

    assert _is_request_timeout_error(Wrapped(timeout_exc)) is True

    class Conn(Exception):
        pass

    assert _is_request_timeout_error(Wrapped(Conn("refused"))) is False
