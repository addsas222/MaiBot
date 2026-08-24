"""OpenCode zen 网关客户端身份注入测试。"""

from src.llm_models.openai_compat import build_openai_compatible_client_config
from src.config.model_configs import APIProvider


def _make_provider(base_url: str) -> APIProvider:
    return APIProvider(name="test", base_url=base_url, api_key="sk-test")


def test_zen_gateway_gets_cli_identity():
    config = build_openai_compatible_client_config(_make_provider("https://opencode.ai/zen/v1"))
    assert config.default_headers["User-Agent"].startswith("opencode/latest/")
    assert config.default_headers["x-opencode-client"] == "cli"


def test_zen_gateway_without_scheme_and_subdomain():
    for base_url in ("opencode.ai/zen/v1", "https://api.opencode.ai/zen/v1"):
        config = build_openai_compatible_client_config(_make_provider(base_url))
        assert "x-opencode-client" in config.default_headers, base_url


def test_other_providers_unaffected():
    config = build_openai_compatible_client_config(_make_provider("https://api.deepseek.com/v1"))
    assert "User-Agent" not in config.default_headers
    assert "x-opencode-client" not in config.default_headers


def test_user_configured_headers_preserved_on_other_hosts():
    provider = _make_provider("https://api.deepseek.com/v1")
    provider.default_headers = {"X-Custom": "yes"}
    config = build_openai_compatible_client_config(provider)
    assert config.default_headers["X-Custom"] == "yes"
