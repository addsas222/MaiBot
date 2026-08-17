from typing import Any, TypeVar

from .config_base import ConfigBase
from .model_configs import APIProvider, ModelInfo, ModelTaskConfig, OpenAICompatibleAuthType, TaskConfig

T = TypeVar("T", bound=ConfigBase)

DEFAULT_PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "your-api-key",
        "auth_type": OpenAICompatibleAuthType.BEARER.value,
        "max_retry": 3,
        "timeout": 100,
        "retry_interval": 8,
    },
    {
        "name": "Cohub",
        "base_url": "http://127.0.0.1:8787/v1",
        "api_key": "cohub-local",
        "client_type": "openai",
        "auth_type": OpenAICompatibleAuthType.NONE.value,
        "model_list_endpoint": "/models",
        "max_retry": 0,
        "timeout": 600,
        "retry_interval": 10,
    },
]

DEFAULT_TASK_CONFIG_TEMPLATES: dict[str, dict[str, Any]] = {
    "utils": {
        "model_list": ["deepseek-v4-flash", "cohub-deepseek-v4-flash", "cohub-qwen3.7-plus"],
        "max_tokens": 4096,
        "temperature": 0.5,
        "slow_threshold": 15.0,
        "selection_strategy": "random",
        "hard_timeout": 120.0,
        "cooldown_seconds": 300,
        "cooldown_max_seconds": 3600,
    },
    "memory": {
        "model_list": [],
        "max_tokens": 8192,
        "temperature": 0.5,
        "slow_threshold": 30.0,
        "selection_strategy": "random",
        "hard_timeout": 240.0,
        "cooldown_seconds": 300,
        "cooldown_max_seconds": 3600,
    },
    "mid_memory": {
        "model_list": [],
        "max_tokens": 8000,
        "temperature": 0.7,
        "slow_threshold": 12.0,
        "selection_strategy": "random",
        "hard_timeout": 180.0,
        "cooldown_seconds": 300,
        "cooldown_max_seconds": 3600,
    },
    "replyer": {
        "model_list": ["deepseek-v4-pro-think", "deepseek-v4-pro-nonthink", "cohub-deepseek-v4-flash", "cohub-glm-5.2"],
        "max_tokens": 4096,
        "temperature": 1,
        "slow_threshold": 120.0,
        "selection_strategy": "random",
        "hard_timeout": 240.0,
        "cooldown_seconds": 300,
        "cooldown_max_seconds": 3600,
    },
    "planner": {
        "model_list": ["deepseek-v4-flash", "cohub-deepseek-v4-flash", "cohub-qwen3.7-plus"],
        "max_tokens": 8000,
        "temperature": 0.7,
        "slow_threshold": 12.0,
        "selection_strategy": "random",
        "hard_timeout": 180.0,
        "cooldown_seconds": 300,
        "cooldown_max_seconds": 3600,
    },
    "learner": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
    "expression_use": {"model_list": [], "max_tokens": 1024, "temperature": 0.3, "hard_timeout": 120.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
    "emoji": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
    "vlm": {"model_list": [], "max_tokens": 4096, "hard_timeout": 240.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
    "voice": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
    "embedding": {"model_list": [], "max_tokens": 4096, "hard_timeout": 60.0, "cooldown_seconds": 300, "cooldown_max_seconds": 3600},
}

DEFAULT_MODEL_TEMPLATES: list[dict[str, Any]] = [
    {
        "model_identifier": "deepseek-v4-pro",
        "name": "deepseek-v4-pro-think",
        "api_provider": "DeepSeek",
        "price_in": 12.0,
        "price_out": 24.0,
        "visual": False,
        "extra_params": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    },
    {
        "model_identifier": "deepseek-v4-pro",
        "name": "deepseek-v4-pro-nonthink",
        "api_provider": "DeepSeek",
        "price_in": 12.0,
        "price_out": 24.0,
        "visual": False,
        "extra_params": {"thinking": {"type": "disabled"}},
    },
    {
        "model_identifier": "deepseek-v4-flash",
        "name": "deepseek-v4-flash",
        "api_provider": "DeepSeek",
        "price_in": 1.0,
        "price_out": 2.0,
        "visual": False,
        "extra_params": {"thinking": {"type": "disabled"}},
    },
]

COHUB_MODEL_TEMPLATES: list[dict[str, Any]] = [
    {
        "model_identifier": "deepseek-v4-flash",
        "name": "cohub-deepseek-v4-flash",
        "api_provider": "Cohub",
        "price_in": 1.0,
        "price_out": 2.0,
        "visual": False,
    },
    {
        "model_identifier": "deepseek-v4-pro",
        "name": "cohub-deepseek-v4-pro",
        "api_provider": "Cohub",
        "price_in": 3.0,
        "price_out": 6.0,
        "visual": False,
    },
    {
        "model_identifier": "glm-5.2",
        "name": "cohub-glm-5.2",
        "api_provider": "Cohub",
        "price_in": 10.0,
        "price_out": 32.0,
        "visual": False,
    },
    {
        "model_identifier": "qwen3.7-plus",
        "name": "cohub-qwen3.7-plus",
        "api_provider": "Cohub",
        "price_in": 2.0,
        "price_out": 14.0,
        "visual": False,
    },
    {
        "model_identifier": "claude-sonnet-5",
        "name": "cohub-claude-sonnet-5",
        "api_provider": "Cohub",
        "price_in": 14.0,
        "price_out": 72.0,
        "visual": False,
    },
    {
        "model_identifier": "gpt-5.6-sol",
        "name": "cohub-gpt-5.6-sol",
        "api_provider": "Cohub",
        "price_in": 36.0,
        "price_out": 216.0,
        "visual": False,
    },
]


def build_default_model_templates() -> list[dict[str, Any]]:
    """筛选任务分配中实际用到的模型模板。"""

    used_model_names = {
        model_name
        for task_template in DEFAULT_TASK_CONFIG_TEMPLATES.values()
        for model_name in task_template["model_list"]
    }
    return [
        model_template
        for model_template in DEFAULT_MODEL_TEMPLATES + COHUB_MODEL_TEMPLATES
        if model_template["name"] in used_model_names
    ]


def create_default_model_config(config_class: type[T]) -> T:
    """根据预置模板创建可通过校验的默认模型配置。"""

    task_config_fields = {}
    for field_name, field_info in ModelTaskConfig.model_fields.items():
        if field_info.annotation is not TaskConfig:
            continue

        task_template = DEFAULT_TASK_CONFIG_TEMPLATES.get(field_name, {})
        task_config_fields[field_name] = TaskConfig(**task_template)

    return config_class(
        models=[ModelInfo(**model_template) for model_template in build_default_model_templates()],
        model_task_config=ModelTaskConfig(**task_config_fields),
        api_providers=[APIProvider(**provider_template) for provider_template in DEFAULT_PROVIDER_TEMPLATES],
    )
