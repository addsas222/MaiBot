import time
from types import SimpleNamespace

import pytest

from src.config.model_configs import APIProvider, ModelInfo, TaskConfig
from src.llm_models import utils_model
from src.llm_models.exceptions import ModelAttemptFailed, RespNotOkException
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_cooldown import ModelCooldownRegistry, ModelIsolationRegistry


def _build_provider() -> APIProvider:
    return APIProvider(
        name="test-provider",
        base_url="https://example.com/v1",
        auth_type="none",
        client_type="openai",
        default_headers={"Authorization": "secret"},
    )


def _build_model_a() -> ModelInfo:
    return ModelInfo(name="test-model-a", model_identifier="test-model-a-id", api_provider="test-provider")


def _build_model_b() -> ModelInfo:
    return ModelInfo(name="test-model-b", model_identifier="test-model-b-id", api_provider="test-provider")


class _FakeClient:
    async def get_response(self, request: ResponseRequest) -> None:
        raise RespNotOkException(status_code=429, message="too many requests")


class _FakeRegistry:
    def get_client_class_instance(self, api_provider: APIProvider) -> _FakeClient:
        return _FakeClient()


def _patch_utils_model_dependencies(monkeypatch, task_config: TaskConfig) -> None:
    fake_config_manager = SimpleNamespace(
        get_model_config=lambda: SimpleNamespace(
            models=[_build_model_a(), _build_model_b()],
            api_providers=[_build_provider()],
            model_task_config=SimpleNamespace(test_task=task_config),
        )
    )
    monkeypatch.setattr(utils_model, "config_manager", fake_config_manager)
    monkeypatch.setattr(utils_model, "ensure_configured_clients_loaded", lambda: None)
    monkeypatch.setattr(utils_model, "client_registry", _FakeRegistry())


@pytest.fixture
def orchestrator(monkeypatch) -> utils_model.LLMOrchestrator:
    task_config = TaskConfig(
        model_list=["test-model-a", "test-model-b"],
        selection_strategy="sequential",
        cooldown_seconds=300,
    )
    _patch_utils_model_dependencies(monkeypatch, task_config)
    return utils_model.LLMOrchestrator("test_task")


@pytest.fixture
def fresh_registry(monkeypatch) -> ModelCooldownRegistry:
    registry = ModelCooldownRegistry()
    monkeypatch.setattr(utils_model, "model_cooldown_registry", registry)
    return registry


@pytest.fixture
def fresh_isolation_registry(monkeypatch) -> ModelIsolationRegistry:
    registry = ModelIsolationRegistry()
    monkeypatch.setattr(utils_model, "model_isolation_registry", registry)
    return registry


class TestModelCooldownRegistry:
    def test_enter_and_check(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 300)
        assert registry.is_in_cooldown("model-x") is True
        assert registry.get_earliest_recovery_model(["model-x"]) == "model-x"

    def test_zero_cooldown_skips(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 0)
        registry.enter_cooldown("model-y", -5)
        assert registry.is_in_cooldown("model-x") is False
        assert registry.get_earliest_recovery_model(["model-x", "model-y"]) is None

    def test_auto_recovers_after_expiry(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 1)
        time.sleep(1.1)
        assert registry.is_in_cooldown("model-x") is False
        assert registry.get_earliest_recovery_model(["model-x"]) is None

    def test_earliest_recovery_model(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-a", 300)
        registry.enter_cooldown("model-b", 100)
        registry.enter_cooldown("model-c", 200)
        assert registry.get_earliest_recovery_model(["model-a", "model-b", "model-c"]) == "model-b"

    def test_earliest_recovery_returns_none_without_cooldown(self) -> None:
        registry = ModelCooldownRegistry()
        assert registry.get_earliest_recovery_model(["model-a", "model-b"]) is None


class TestModelCooldownStaircase:
    def _remaining(self, registry: ModelCooldownRegistry, model_name: str) -> float:
        return registry._cooldown_until[model_name] - time.time()

    def test_doubles_per_consecutive_429(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 300, 3600)
        first = self._remaining(registry, "model-x")
        registry.enter_cooldown("model-x", 300, 3600)
        second = self._remaining(registry, "model-x")
        registry.enter_cooldown("model-x", 300, 3600)
        third = self._remaining(registry, "model-x")
        assert abs(first - 300) < 1.5
        assert abs(second - 600) < 1.5
        assert abs(third - 1200) < 1.5

    def test_capped_at_max(self) -> None:
        registry = ModelCooldownRegistry()
        for _ in range(5):
            registry.enter_cooldown("model-x", 300, 3600)
        assert self._remaining(registry, "model-x") <= 3600.1

    def test_max_not_greater_than_base_keeps_fixed(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 300, 300)
        registry.enter_cooldown("model-x", 300, 300)
        assert self._remaining(registry, "model-x") <= 301

    def test_reset_strikes_restarts_from_base(self) -> None:
        registry = ModelCooldownRegistry()
        registry.enter_cooldown("model-x", 300, 3600)
        registry.enter_cooldown("model-x", 300, 3600)
        registry.reset_strikes("model-x")
        assert registry.is_in_cooldown("model-x") is False
        registry.enter_cooldown("model-x", 300, 3600)
        assert self._remaining(registry, "model-x") <= 301


class TestSelectModelCooldown:
    def test_skips_cooled_model(self, orchestrator, fresh_registry) -> None:
        fresh_registry.enter_cooldown("test-model-a", 300)
        model_info, _, _ = orchestrator._select_model()
        assert model_info.name == "test-model-b"

    def test_raises_for_requested_cooled_model(self, orchestrator, fresh_registry) -> None:
        fresh_registry.enter_cooldown("test-model-a", 300)
        with pytest.raises(RuntimeError, match="正在冷却中"):
            orchestrator._select_model(model_name="test-model-a")

    def test_degrades_when_all_models_cooled(self, orchestrator, fresh_registry) -> None:
        fresh_registry.enter_cooldown("test-model-a", 300)
        fresh_registry.enter_cooldown("test-model-b", 100)
        model_info, _, _ = orchestrator._select_model()
        assert model_info.name == "test-model-b"

    def test_keeps_error_when_pool_empty_without_cooldown(self, orchestrator, fresh_registry) -> None:
        with pytest.raises(RuntimeError, match="没有可用的模型可供选择"):
            orchestrator._select_model(exclude_models={"test-model-a", "test-model-b"})

    def test_recovers_after_cooldown_expiry(self, orchestrator, fresh_registry) -> None:
        fresh_registry.enter_cooldown("test-model-a", 1)
        time.sleep(1.1)
        model_info, _, _ = orchestrator._select_model()
        assert model_info.name == "test-model-a"


class TestAttemptRequest429Cooldown:
    @pytest.mark.asyncio
    async def test_429_exhaustion_registers_cooldown(self, monkeypatch) -> None:
        task_config = TaskConfig(
            model_list=["test-model-a"],
            selection_strategy="sequential",
            cooldown_seconds=300,
        )
        _patch_utils_model_dependencies(monkeypatch, task_config)
        monkeypatch.setattr(utils_model, "has_request_snapshot", lambda error: True)
        registry = ModelCooldownRegistry()
        monkeypatch.setattr(utils_model, "model_cooldown_registry", registry)

        orchestrator = utils_model.LLMOrchestrator("test_task")
        request = ResponseRequest(
            model_info=_build_model_a(),
            context_items=[],
            trace_context=None,
            max_tokens=256,
        )
        with pytest.raises(ModelAttemptFailed):
            await orchestrator._attempt_request_on_model(
                api_provider=_build_provider(),
                client=_FakeClient(),
                request=request,
                retry_limit=1,
            )
        assert registry.is_in_cooldown("test-model-a") is True


class TestModelIsolationRegistry:
    def test_isolate_and_check(self) -> None:
        registry = ModelIsolationRegistry()
        registry.isolate("model-x")
        assert registry.is_isolated("model-x") is True
        assert registry.is_isolated("model-y") is False

    def test_isolation_does_not_expire(self) -> None:
        # 隔离无到期时间，不会像冷却一样自动恢复，等待重启后重新探测
        registry = ModelIsolationRegistry()
        registry.isolate("model-x")
        time.sleep(1.1)
        assert registry.is_isolated("model-x") is True


class TestSelectModelIsolation:
    def test_skips_isolated_model(self, orchestrator, fresh_isolation_registry) -> None:
        fresh_isolation_registry.isolate("test-model-a")
        model_info, _, _ = orchestrator._select_model()
        assert model_info.name == "test-model-b"

    def test_raises_for_requested_isolated_model(self, orchestrator, fresh_isolation_registry) -> None:
        fresh_isolation_registry.isolate("test-model-a")
        with pytest.raises(RuntimeError, match="正在隔离中"):
            orchestrator._select_model(model_name="test-model-a")

    def test_isolated_model_not_degraded(self, orchestrator, fresh_registry, fresh_isolation_registry) -> None:
        # 隔离中的模型不参与降级：a 冷却 300s、b 冷却 100s 但被隔离，降级只能选择 a
        fresh_registry.enter_cooldown("test-model-a", 300)
        fresh_registry.enter_cooldown("test-model-b", 100)
        fresh_isolation_registry.isolate("test-model-b")
        model_info, _, _ = orchestrator._select_model()
        assert model_info.name == "test-model-a"

    def test_raises_when_all_models_isolated(self, orchestrator, fresh_isolation_registry) -> None:
        fresh_isolation_registry.isolate("test-model-a")
        fresh_isolation_registry.isolate("test-model-b")
        with pytest.raises(RuntimeError, match="没有可用的模型可供选择"):
            orchestrator._select_model()


class _FakeForbiddenClient:
    """模拟返回 403 的客户端，用于验证 403 触发模型隔离。"""

    def get_response(self, request) -> None:
        raise RespNotOkException(status_code=403, message="forbidden")


class TestAttemptRequest403Isolation:
    @pytest.mark.asyncio
    async def test_403_registers_isolation(self, monkeypatch) -> None:
        task_config = TaskConfig(
            model_list=["test-model-a"],
            selection_strategy="sequential",
            cooldown_seconds=300,
        )
        _patch_utils_model_dependencies(monkeypatch, task_config)
        monkeypatch.setattr(utils_model, "has_request_snapshot", lambda error: True)
        isolation_registry = ModelIsolationRegistry()
        monkeypatch.setattr(utils_model, "model_isolation_registry", isolation_registry)

        orchestrator = utils_model.LLMOrchestrator("test_task")
        request = ResponseRequest(
            model_info=_build_model_a(),
            context_items=[],
            trace_context=None,
            max_tokens=256,
        )
        with pytest.raises(ModelAttemptFailed):
            await orchestrator._attempt_request_on_model(
                api_provider=_build_provider(),
                client=_FakeForbiddenClient(),
                request=request,
                retry_limit=1,
            )
        assert isolation_registry.is_isolated("test-model-a") is True
