"""OpenAI 兼容客户端 Kimi K 系列模型适配测试。"""

from openai import APIStatusError

from src.llm_models.model_client.openai_client import (
    _is_fixed_temperature_error,
    _is_kimi_k_series_model,
    _convert_messages,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    FunctionCallItem,
    ContextToolCall,
    ReasoningItem,
    ReasoningRepresentation,
    UserMessageItem,
)


def _build_items_with_reasoning():
    """构造包含 reasoning 与正文的用户/助手消息序列。"""
    meta = ContextItemMeta.create(logical_turn_id="turn-1")
    return [
        UserMessageItem(meta=meta, parts=(ContextTextPart(text="你好"),)),
        ReasoningItem(
            meta=meta,
            text_parts=("思考中...",),
            representation=ReasoningRepresentation.RAW_TEXT,
        ),
        AssistantMessageItem(meta=meta, parts=(ContextTextPart(text="正式回复"),)),
    ]


def test_is_kimi_k_series_model():
    assert _is_kimi_k_series_model("kimi-k2.6")
    assert _is_kimi_k_series_model("kimi-k2-turbo-preview")
    assert _is_kimi_k_series_model("kimi-k3")
    assert not _is_kimi_k_series_model("gpt-4o")
    assert not _is_kimi_k_series_model("moonshot-v1-8k")


def test_is_fixed_temperature_error():
    def _build_error(status_code: int, message: str) -> APIStatusError:
        error = APIStatusError.__new__(APIStatusError)
        error.status_code = status_code
        error.message = message
        error.response = None
        return error

    assert _is_fixed_temperature_error(_build_error(400, "invalid temperature: only 1 is allowed for this model"))
    assert _is_fixed_temperature_error(_build_error(400, "invalid temperature: only 0.6 is allowed for this model"))
    assert not _is_fixed_temperature_error(_build_error(400, "invalid temperature: only 1"))
    assert not _is_fixed_temperature_error(_build_error(400, "invalid top_p: only 0.95 is allowed"))
    assert not _is_fixed_temperature_error(_build_error(500, "invalid temperature: only 1 is allowed for this model"))
    assert not _is_fixed_temperature_error(_build_error(400, "invalid api key"))


def test_convert_messages_default_drops_reasoning():
    messages = _convert_messages(_build_items_with_reasoning())
    assistant_payload = messages[-1]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["content"] == "正式回复"
    assert "reasoning_content" not in assistant_payload


def test_convert_messages_kimi_k2_preserves_reasoning():
    messages = _convert_messages(_build_items_with_reasoning(), preserve_reasoning_content=True)
    assistant_payload = messages[-1]
    assert assistant_payload["content"] == "正式回复"
    assert assistant_payload["reasoning_content"] == "思考中..."


def test_convert_messages_kimi_k2_preserves_reasoning_with_tool_calls():
    meta = ContextItemMeta.create(logical_turn_id="turn-1")
    items = [
        ReasoningItem(
            meta=meta,
            text_parts=("要调用工具",),
            representation=ReasoningRepresentation.RAW_TEXT,
        ),
        FunctionCallItem(
            meta=meta,
            tool_call=ContextToolCall.create(call_id="call_1", func_name="foo", args={"a": 1}),
        ),
    ]
    messages = _convert_messages(items, preserve_reasoning_content=True)
    assistant_payload = messages[-1]
    assert assistant_payload["tool_calls"][0]["function"]["name"] == "foo"
    assert assistant_payload["reasoning_content"] == "要调用工具"
