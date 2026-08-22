"""图片与表情包内容合规审校模块。

用于在黑话学习、表达学习等审核环节中识别聊天中出现的违规图片描述或
表情包文本（色情低俗、暴力血腥、政治敏感、广告诈骗、隐私泄露等），
避免麦麦把违规内容当作正常表达方式或黑话学习进记忆库。

识别分两层：
1. 规则层：敏感正则匹配，稳定且零成本。新增违规词时直接向
   ``BAD_IMAGE_PATTERNS`` 追加即可。
2. LLM 层：对通过规则层的候选做一次批量语义判定，兜住规则覆盖不到的新违规表达。
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

import re

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.logger import get_logger
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

logger = get_logger("image_content_filter")

image_judge_model = LLMServiceClient(task_name="learner", request_type="image.judge")

# 图片/表情包描述违规规则：(类别, 正则)
BAD_IMAGE_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "色情低俗",
        re.compile(
            r"(?:"
            r"裸体|全裸|半裸|裸露|露胸|酥胸|裸照|裸图|一丝不挂|"
            r"性暗示|性挑逗|性诱惑|挑逗|撩人|媚眼|诱惑|勾引|涩图|涩涩|性感|"
            r"薄纱|透明装|真空上阵|内衣照|泳装照|比基尼|"
            r"胸|乳|翘臀|丝袜|白丝|黑丝|情趣"
            r")",
        ),
    ),
    (
        "暴力血腥",
        re.compile(
            r"(?:"
            r"血腥|血淋淋|流血|尸体|死尸|内脏|断肢|残肢|"
            r"分尸|砍头|杀人现场|恐怖|惊悚|灵异|鬼图"
            r")",
        ),
    ),
    (
        "广告诈骗",
        re.compile(
            r"(?:"
            r"加群|加V|加微信|关注公众号|扫码|点击链接|"
            r"刷单|兼职日结|返利|赌博|博彩|彩票|网赚|"
            r"色情交易|约炮|一夜情|援交|福利姬"
            r")",
        ),
    ),
    (
        "隐私泄露",
        re.compile(
            r"(?:"
            r"身份证|银行卡|收款码|验证码|支付密码|银行密码|"
            r"手持身份证|实名认证资料|通讯录泄露"
            r")",
        ),
    ),
]

_IMAGE_MARKER_PATTERN = re.compile(r"\[(?:图片|表情包?)[:：]?([^\]]*)\]")


def _extract_image_content(text: str) -> str:
    """从文本中提取 [图片：...]/[表情包:...] 标记内部的实际描述内容。"""

    match = _IMAGE_MARKER_PATTERN.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


@dataclass(frozen=True)
class BadImageMatch:
    """一次图片/表情包违规规则命中的结果。"""

    category: str
    matched: str


def is_bad_image_content(text: str) -> Optional[BadImageMatch]:
    """判断图片描述/表情包文本是否命中违规规则。

    Args:
        text: 待判断的文本（可为 [图片：描述] 形式的原始文本）。

    Returns:
        命中时返回 ``BadImageMatch``，否则返回 ``None``。
    """

    content = _extract_image_content(text)
    if not content:
        return None
    for category, pattern in BAD_IMAGE_PATTERNS:
        if match := pattern.search(content):
            return BadImageMatch(category=category, matched=match.group(0))
    return None


def filter_bad_image_jargons(
    entries: Sequence[Tuple[str, str]],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """按规则过滤黑话候选，移除命中图片/表情包违规规则的词条。

    Args:
        entries: 黑话候选列表，每个元素为 (content, source_id)。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (content, source_id, 类别)。
    """

    kept: List[Tuple[str, str]] = []
    rejected: List[Tuple[str, str, str]] = []
    for content, source_id in entries:
        if match := is_bad_image_content(content):
            logger.info(
                f"{session_id} 跳过命中违规图片/表情包规则的黑话：content={content}, source_id={source_id}, "
                f"命中={match.category}:{match.matched}"
            )
            rejected.append((content, source_id, match.category))
            continue
        kept.append((content, source_id))
    return kept, rejected


def filter_bad_image_expressions(
    expressions: Sequence[Tuple[str, str, str]],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str]]]:
    """按规则过滤表达方式，移除 situation 或 style 命中图片/表情包违规规则的候选。

    Args:
        expressions: 表达方式候选列表，每个元素为 (situation, style, source_id)。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (situation, style, source_id, 类别)。
    """

    kept: List[Tuple[str, str, str]] = []
    rejected: List[Tuple[str, str, str, str]] = []
    for situation, style, source_id in expressions:
        match = is_bad_image_content(situation) or is_bad_image_content(style)
        if match:
            logger.info(
                f"{session_id} 跳过命中违规图片/表情包规则的表达方式：situation={situation}, style={style}, "
                f"source_id={source_id}, 命中={match.category}:{match.matched}"
            )
            rejected.append((situation, style, source_id, match.category))
            continue
        kept.append((situation, style, source_id))
    return kept, rejected


def _render_judge_entries(entries: Sequence[Tuple[str, str]]) -> str:
    """把图片/表情包候选渲染为 LLM 判定输入。"""

    return "\n".join(f'- content="{content}", source_id="{source_id}"' for content, source_id in entries)


def _parse_judge_response(response: str) -> Set[str]:
    """解析图片/表情包违规判定 LLM 响应，返回判定为违规的 content 集合。"""

    import json

    from json_repair import repair_json

    raw = (response or "").strip()
    if not raw:
        return set()
    if match := re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL):
        raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = json.loads(repair_json(raw))
        except Exception as exc:
            logger.error(f"图片/表情包违规判定响应解析失败: {exc}, response={raw[:200]}")
            return set()

    if not isinstance(parsed, list):
        logger.warning("图片/表情包违规判定响应格式异常，按无违规处理")
        return set()

    judged_bad: Set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("is_bad", False):
            continue
        content = str(item.get("content", "")).strip()
        if content:
            judged_bad.add(content)
    return judged_bad


async def judge_bad_image_with_llm(
    entries: Sequence[Tuple[str, str]],
    *,
    session_id: str = "",
) -> Set[str]:
    """用一次批量 LLM 判定识别违规图片描述/表情包黑话候选。

    Args:
        entries: 通过规则层的图片/表情包黑话候选列表。
        session_id: 当前会话 ID。

    Returns:
        被判定为违规的 content 集合。LLM 调用失败时返回空集合，不阻塞学习。
    """

    if not entries:
        return set()

    prompt_template = prompt_manager.get_prompt("judge_bad_image")
    prompt_template.add_context("entries", _render_judge_entries(entries))
    prompt = await prompt_manager.render_prompt(prompt_template)

    try:
        generation_result = await image_judge_model.generate_response(
            prompt=prompt,
            options=LLMGenerationOptions(temperature=0.2),
            session_id=session_id,
        )
        return _parse_judge_response(generation_result.response)
    except Exception as exc:
        logger.error(f"{session_id} 图片/表情包违规 LLM 判定失败，跳过 LLM 层过滤：{exc}")
        return set()


def filter_bad_image_jargons_with_llm(
    entries: Sequence[Tuple[str, str]],
    bad_contents: Set[str],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """把 LLM 判定结果应用到黑话候选，返回 (保留, 被过滤)。

    Args:
        entries: 图片/表情包黑话候选列表。
        bad_contents: LLM 判定为违规的 content 集合。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (content, source_id, 类别)。
    """

    kept: List[Tuple[str, str]] = []
    rejected: List[Tuple[str, str, str]] = []
    for content, source_id in entries:
        if content in bad_contents:
            logger.info(f"{session_id} 跳过 LLM 判定为违规的图片/表情包黑话：content={content}, source_id={source_id}")
            rejected.append((content, source_id, "LLM 判定"))
            continue
        kept.append((content, source_id))
    return kept, rejected
