"""网络烂梗识别模块单元测试。"""

import pytest

from src.learners.bad_meme_filter import (
    BAD_MEME_KEYWORDS,
    _parse_judge_response,
    contains_bad_meme,
    filter_bad_meme_expressions,
    filter_bad_meme_jargons,
    filter_bad_meme_jargons_with_llm,
    find_bad_memes,
    is_bad_meme,
    normalize_meme_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "家人们谁懂啊",
        "这操作真是依托答辩",
        "奥利给 兄弟们干了",
        "你怎么这么 泰裤辣",
        "疯狂星期四！V我50",
        "鸡你太美",
        "尊嘟假嘟",
    ],
)
def test_contains_bad_meme_hits(text: str) -> None:
    """烂梗词库应能命中典型网络烂梗。"""

    assert contains_bad_meme(text) is True
    assert find_bad_memes(text)


@pytest.mark.parametrize(
    "text",
    [
        "看到同事摸鱼我就想笑",
        "刚才开会讨论了论文答辩的安排",
        "别玩梗了，说正事",
        "今天的晚饭吃什么",
        "",
    ],
)
def test_contains_bad_meme_misses(text: str) -> None:
    """正常文本不应被误判为包含网络烂梗。"""

    assert contains_bad_meme(text) is False
    assert find_bad_memes(text) == []


def test_find_bad_memes_returns_deduplicated_matches() -> None:
    """同一文本命中多个烂梗时应全部返回且不重复。"""

    matches = find_bad_memes("家人们谁懂啊，这操作真是依托答辩")
    assert matches == ["依托答辩", "家人们谁懂啊"]
    assert find_bad_memes("家人们谁懂啊，家人们谁懂啊") == ["家人们谁懂啊"]


def test_normalize_meme_text_handles_punctuation_and_case() -> None:
    """规范化应去除空白、标点并统一小写，保证子串匹配。"""

    assert normalize_meme_text(" 芭比 Q,。！？~ ") == "芭比q"
    assert normalize_meme_text("V我50") == "v我50"


def test_lexicon_entries_are_non_empty() -> None:
    """词库条目不应为空字符串或纯标点。"""

    for keyword in BAD_MEME_KEYWORDS:
        assert normalize_meme_text(keyword), f"词库存在空条目: {keyword!r}"


@pytest.mark.parametrize(
    "text",
    [
        "傻逼",
        "nmsl",
        "sb",
        "龟男",
        "操你妈",
        "死全家",
        "断子绝孙",
        "支那",
    ],
)
def test_is_bad_meme_regex_hits(text: str) -> None:
    """正则规则应能命中低俗辱骂、性低俗、恶意诅咒、歧视攻击类烂梗。"""

    match = is_bad_meme(text)
    assert match is not None
    assert match.category in {"低俗辱骂", "性低俗", "恶意诅咒", "歧视攻击", "危险词库"}


@pytest.mark.parametrize(
    "text",
    [
        "牛批",
        "nb",
        "内卷",
        "社死",
        "显眼包",
        "蹭蹭",
        "门派",
        "钩子",
        "nga",
    ],
)
def test_is_bad_meme_misses(text: str) -> None:
    """正常黑话与中性词不应被误判为烂梗。"""

    assert is_bad_meme(text) is None


def test_filter_bad_meme_jargons_keeps_normal_entries() -> None:
    """规则层过滤黑话时应保留正常词条，移除烂梗词条。"""

    entries = [("yyds", "1"), ("泰裤辣", "2"), ("内卷", "3"), ("nmsl", "4")]
    kept, rejected = filter_bad_meme_jargons(entries, session_id="test")
    assert kept == [("内卷", "3")]
    assert [item[0] for item in rejected] == ["yyds", "泰裤辣", "nmsl"]


def test_filter_bad_meme_expressions_filters_situation_and_style() -> None:
    """规则层过滤表达方式时应同时检查 situation 与 style。"""

    expressions = [
        ("表示惊叹", "使用 泰裤辣", "1"),
        ("表示愤怒", "使用 别跟我讲道理", "2"),
        ("问候他人", "使用 你是不是傻逼", "3"),
    ]
    kept, rejected = filter_bad_meme_expressions(expressions, session_id="test")
    assert kept == [("表示愤怒", "使用 别跟我讲道理", "2")]
    assert [item[0] for item in rejected] == ["表示惊叹", "问候他人"]


def test_parse_judge_response_collects_bad_entries() -> None:
    """LLM 判定响应解析应只收集 is_bad_meme 为 true 的词条。"""

    response = """
    [
      {"content": "yyds", "is_bad_meme": false, "reason": "正常缩写"},
      {"content": "典中典", "is_bad_meme": true, "reason": "过气烂梗"},
      {"content": "蚌埠住了", "is_bad_meme": false, "reason": "正常流行语"}
    ]
    """
    assert _parse_judge_response(response) == {"典中典"}
    assert _parse_judge_response("```json\n[]\n```") == set()
    assert _parse_judge_response("不是 JSON") == set()
    assert _parse_judge_response("") == set()


def test_filter_bad_meme_jargons_with_llm() -> None:
    """LLM 判定结果应被应用到黑话候选。"""

    entries = [("yyds", "1"), ("典中典", "2")]
    kept, rejected = filter_bad_meme_jargons_with_llm(entries, {"典中典"}, session_id="test")
    assert kept == [("yyds", "1")]
    assert rejected == [("典中典", "2", "LLM 判定")]
