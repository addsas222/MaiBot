"""网络烂梗识别模块。

用于在表达学习、黑话学习等审核环节中识别网络烂梗（过度玩梗、低俗梗、
冒犯性梗等），避免麦麦把烂梗当作正常表达方式或黑话学习进记忆库。

识别分两层：
1. 规则层：词库子串匹配 + 低俗辱骂/性低俗/恶意诅咒/歧视攻击类正则匹配，
   稳定且零成本。新增烂梗时直接向 ``BAD_MEME_KEYWORDS`` 追加，或向
   ``BAD_MEME_VULGAR_PATTERNS`` 追加正则即可。
2. LLM 层：对通过规则层的候选做一次批量语义判定，兜住规则覆盖不到的新烂梗。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

import asyncio
import re

from sqlmodel import col, select

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.database.database import get_db_session
from src.common.database.database_model import ImageType, Images, Jargon, JargonCreatedBy
from src.common.http_client import get_main_http_client
from src.common.logger import get_logger
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

logger = get_logger("bad_meme_filter")

meme_judge_model = LLMServiceClient(task_name="learner", request_type="meme.judge")

BAD_MEME_KEYWORDS: List[str] = [
    # ===== 低俗 / 恶俗类 =====
    "依托答辩",
    "一坨答辩",
    "屎上雕花",
    "虾头",
    "玉足",
    "南通",
    "老鼠人",
    "鼠人",
    "男妈妈",
    # ===== 过度玩梗 / 刷屏类 =====
    "奥利给",
    "芭比q",
    "完蛋了芭比q",
    "栓q",
    "泰裤辣",
    "尊嘟假嘟",
    "尊嘟",
    "家人们谁懂啊",
    "绝绝子",
    "退退退",
    "疯狂星期四",
    "v我50",
    "鸡你太美",
    "只因你太美",
    "小黑子",
    "你干嘛哎哟",
    "食不食油饼",
    "什么档次",
    "你是我的神",
    "羊胎素",
    "挖呀挖呀挖",
    "挖呀挖",
    "恐龙抗狼",
    "钵钵鸡",
    "我嘞个豆",
    "老六",
    "电摇",
    "就这就这",
    "你急了",
    "你急了你急了",
    "谁问你了",
    "又没问你",
    "典中典",
    "一眼丁真",
    "你真是饿了",
    "惹到我你算是踢到棉花了",
    "三句话让男人为我花十八万",
    # ===== 经典过气烂梗 =====
    "蓝瘦香菇",
    "吓死宝宝了",
    "我也是醉了",
    "皮皮虾我们走",
    "老铁双击666",
    "双击666",
    "盘他",
    "好嗨哦",
    "小拳拳捶你胸口",
    "是兄弟就来砍我",
    "你礼貌吗",
    "我信你个鬼",
    "你个糟老头子坏得很",
    "感觉人生已经到达了高潮",
]

BAD_MEME_KEYWORDS_FILE = Path("data/bad_meme_keywords.txt")
"""用户可编辑的本地烂梗词库覆盖文件路径，每行一个词条，以 # 开头的行为注释。"""

_custom_keywords: List[str] = []
_custom_keywords_mtime: float = -1.0


def get_bad_meme_keywords() -> List[str]:
    """返回内置词库与本地覆盖词库合并后的完整词条列表。

    本地覆盖文件（data/bad_meme_keywords.txt）按 mtime 变化自动重载，
    无需重启即可在新增烂梗后当天生效。

    Returns:
        List[str]: 合并去重后的烂梗词条列表。
    """

    global _custom_keywords, _custom_keywords_mtime
    file_path = Path(BAD_MEME_KEYWORDS_FILE)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = -1.0

    if mtime != _custom_keywords_mtime:
        _custom_keywords_mtime = mtime
        _custom_keywords = []
        if mtime >= 0:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        keyword = line.strip()
                        if keyword and not keyword.startswith("#"):
                            _custom_keywords.append(keyword)
            except OSError as exc:
                logger.warning(f"读取本地烂梗词库失败，使用内置词库: {exc}")
                _custom_keywords = []

    seen = set(BAD_MEME_KEYWORDS)
    return BAD_MEME_KEYWORDS + [kw for kw in _custom_keywords if kw not in seen]

DANGER_WORDS_FILE = Path("data/danger_words.txt")
"""每日自动同步的危险词库文件路径，每行一个词条，以 # 开头的行为注释。"""

DANGER_WORDS_SOURCE_URL = (
    "https://v6.gh-proxy.org/https://raw.githubusercontent.com/dablelv/dirty-cnwords/master/curse/all.txt"
)
"""危险词源拉取地址（v6.gh-proxy.org 镜像）。"""

_danger_words: List[str] = []
_danger_words_mtime: float = -1.0


def get_danger_words() -> List[str]:
    """返回本地危险词库词条列表，按 mtime 变化自动重载。

    Returns:
        List[str]: 危险词条列表。
    """

    global _danger_words, _danger_words_mtime
    file_path = Path(DANGER_WORDS_FILE)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = -1.0

    if mtime != _danger_words_mtime:
        _danger_words_mtime = mtime
        _danger_words = []
        if mtime >= 0:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        word = line.strip()
                        if word and not word.startswith("#"):
                            _danger_words.append(word)
            except OSError as exc:
                logger.warning(f"读取本地危险词库失败: {exc}")
                _danger_words = []
    return list(_danger_words)


def find_danger_words(text: str) -> List[str]:
    """返回文本中命中的危险词条列表（去重）。

    Args:
        text: 待检测文本。

    Returns:
        List[str]: 命中的危险词条；未命中时返回空列表。
    """

    normalized_text = normalize_meme_text(text)
    if not normalized_text:
        return []

    matched_words: List[str] = []
    seen_words: set[str] = set()
    for word in get_danger_words():
        normalized_word = normalize_meme_text(word)
        if not normalized_word or normalized_word in seen_words:
            continue
        if normalized_word in normalized_text:
            seen_words.add(normalized_word)
            matched_words.append(word)
    return matched_words

# 低俗辱骂/性低俗/恶意诅咒/歧视攻击类烂梗规则：(类别, 正则)
BAD_MEME_VULGAR_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "低俗辱骂",
        re.compile(
            r"(?:"
            r"傻[逼比b]|煞笔|沙[逼比]|撒比|憨[逼批]|脑残|脑瘫|弱智|智障|蠢货|蠢猪|"
            r"废物|废[材柴]|乐色|辣鸡|杂种|畜生|禽兽|人渣|贱[人货]|婊子|"
            r"喷粪|嘴臭|口吐芬芳|孤儿|走狗|汉奸|出生|二[逼货]|装[逼比]|菜[逼比]|"
            r"原批|粥批|农批|崩批|铁批|米批|"
            r"mdzz|mmp"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "性低俗",
        re.compile(
            r"(?:"
            r"操你|草你|艹你|日你|干你|妈逼|妈批|妈卖批|麻痹|"
            r"屌|鸡巴|牛子|约炮|打炮|口交|乳交|自慰|手淫|荡妇|妓女|嫖娼|嫖客|"
            r"cnmd|wcnm|nmsl"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "恶意诅咒",
        re.compile(
            r"(?:"
            r"死全家|全家火葬场|出门被车|被车撞|去死|暴毙|坟头|骨灰盒|灵堂|"
            r"断子绝孙|绝户|死妈|司马|妈没了|亲妈|阳痿|绿帽|戴绿帽|破鞋|"
            r"克死|天打雷劈"
            r")",
        ),
    ),
    (
        "歧视攻击",
        re.compile(
            r"(?:"
            r"龟男|母狗|黑鬼|尼哥|支那|倭寇|白皮猪|猪猡|卖国贼|精日|慕洋犬|"
            r"女拳|男拳|肥猪|母猪|公狗|狗腿|崇洋媚外"
            r")",
        ),
    ),
]

# 拼音缩写类烂梗需要词边界，避免误伤正常英文单词
BAD_MEME_BOUNDED_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "低俗辱骂",
        re.compile(r"(?<![a-z])(?:sb|npc)(?![a-z])", re.IGNORECASE),
    ),
]

_BAD_MEME_CLEANUP_BATCH_SIZE = 500
_BAD_MEME_CLEANUP_POLL_SECONDS = 3600

_MEME_TEXT_PATTERN = re.compile(r"[\s，。！？、,.!?~～·\-—_:：;；\"'“”‘’()（）【】\[\]《》<>]+")


@dataclass(frozen=True)
class BadMemeMatch:
    """一次烂梗规则命中的结果。"""

    category: str
    matched: str


def normalize_meme_text(text: str) -> str:
    """规范化待检测文本，去掉空白与常见标点，统一小写，便于词库子串匹配。

    Args:
        text: 原始文本。

    Returns:
        str: 规范化后的文本。
    """

    normalized = _MEME_TEXT_PATTERN.sub("", text or "")
    return normalized.casefold()


def find_bad_memes(text: str) -> List[str]:
    """返回文本中命中的网络烂梗词条列表（去重、按词库顺序）。

    Args:
        text: 待检测文本。

    Returns:
        List[str]: 命中的烂梗词条；未命中时返回空列表。
    """

    normalized_text = normalize_meme_text(text)
    if not normalized_text:
        return []

    matched_memes: List[str] = []
    seen_memes: set[str] = set()
    for keyword in get_bad_meme_keywords():
        normalized_keyword = normalize_meme_text(keyword)
        if not normalized_keyword or normalized_keyword in seen_memes:
            continue
        if normalized_keyword in normalized_text:
            seen_memes.add(normalized_keyword)
            matched_memes.append(keyword)
    return matched_memes


def contains_bad_meme(text: str) -> bool:
    """判断文本中是否包含网络烂梗。

    Args:
        text: 待检测文本。

    Returns:
        bool: 包含网络烂梗时返回 True。
    """

    return bool(find_bad_memes(text))


def is_bad_meme(text: str) -> Optional[BadMemeMatch]:
    """判断文本是否命中烂梗规则（词库 + 正则两层）。

    Args:
        text: 待判断的文本。

    Returns:
        命中时返回 ``BadMemeMatch``，否则返回 ``None``。
    """

    if not text:
        return None
    if danger_matches := find_danger_words(text):
        return BadMemeMatch(category="危险词库", matched=danger_matches[0])
    if keyword_matches := find_bad_memes(text):
        return BadMemeMatch(category="烂俗词库", matched=keyword_matches[0])
    for category, pattern in BAD_MEME_VULGAR_PATTERNS + BAD_MEME_BOUNDED_PATTERNS:
        if match := pattern.search(text):
            return BadMemeMatch(category=category, matched=match.group(0))
    return None


def filter_bad_meme_jargons(
    entries: Sequence[Tuple[str, str]],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """按规则过滤黑话候选，移除命中烂梗规则的词条。

    Args:
        entries: 黑话候选列表，每个元素为 (content, source_id)。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (content, source_id, 类别)。
    """

    kept: List[Tuple[str, str]] = []
    rejected: List[Tuple[str, str, str]] = []
    for content, source_id in entries:
        if match := is_bad_meme(content):
            logger.info(
                f"{session_id} 跳过命中烂梗规则的黑话：content={content}, source_id={source_id}, "
                f"命中={match.category}:{match.matched}"
            )
            rejected.append((content, source_id, match.category))
            continue
        kept.append((content, source_id))
    return kept, rejected


def filter_bad_meme_expressions(
    expressions: Sequence[Tuple[str, str, str]],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str, str]]]:
    """按规则过滤表达方式，移除 situation 或 style 命中烂梗规则的候选。

    Args:
        expressions: 表达方式候选列表，每个元素为 (situation, style, source_id)。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (situation, style, source_id, 类别)。
    """

    kept: List[Tuple[str, str, str]] = []
    rejected: List[Tuple[str, str, str, str]] = []
    for situation, style, source_id in expressions:
        match = is_bad_meme(situation) or is_bad_meme(style)
        if match:
            logger.info(
                f"{session_id} 跳过命中烂梗规则的表达方式：situation={situation}, style={style}, "
                f"source_id={source_id}, 命中={match.category}:{match.matched}"
            )
            rejected.append((situation, style, source_id, match.category))
            continue
        kept.append((situation, style, source_id))
    return kept, rejected


def _render_judge_entries(entries: Sequence[Tuple[str, str]]) -> str:
    """把黑话候选渲染为 LLM 判定输入。"""

    return "\n".join(f'- content="{content}", source_id="{source_id}"' for content, source_id in entries)


def _parse_judge_response(response: str) -> Set[str]:
    """解析烂梗判定 LLM 响应，返回判定为烂梗的 content 集合。"""

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
            logger.error(f"烂梗判定响应解析失败: {exc}, response={raw[:200]}")
            return set()

    if not isinstance(parsed, list):
        logger.warning("烂梗判定响应格式异常，按无烂梗处理")
        return set()

    judged_bad: Set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("is_bad_meme", False):
            continue
        content = str(item.get("content", "")).strip()
        if content:
            judged_bad.add(content)
    return judged_bad


async def judge_bad_meme_with_llm(
    entries: Sequence[Tuple[str, str]],
    *,
    session_id: str = "",
) -> Set[str]:
    """用一次批量 LLM 判定识别烂梗黑话候选。

    Args:
        entries: 通过规则层的黑话候选列表。
        session_id: 当前会话 ID。

    Returns:
        被判定为烂梗的 content 集合。LLM 调用失败时返回空集合，不阻塞学习。
    """

    if not entries:
        return set()

    prompt_template = prompt_manager.get_prompt("judge_bad_meme")
    prompt_template.add_context("entries", _render_judge_entries(entries))
    prompt = await prompt_manager.render_prompt(prompt_template)

    try:
        generation_result = await meme_judge_model.generate_response(
            prompt=prompt,
            options=LLMGenerationOptions(temperature=0.2),
            session_id=session_id,
        )
        return _parse_judge_response(generation_result.response)
    except Exception as exc:
        logger.error(f"{session_id} 烂梗 LLM 判定失败，跳过 LLM 层过滤：{exc}")
        return set()


def filter_bad_meme_jargons_with_llm(
    entries: Sequence[Tuple[str, str]],
    bad_contents: Set[str],
    *,
    session_id: str = "",
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """把 LLM 判定结果应用到黑话候选，返回 (保留, 被过滤)。

    Args:
        entries: 黑话候选列表。
        bad_contents: LLM 判定为烂梗的 content 集合。
        session_id: 当前会话 ID，仅用于日志。

    Returns:
        (保留列表, 被过滤列表)。被过滤元素为 (content, source_id, 类别)。
    """

    kept: List[Tuple[str, str]] = []
    rejected: List[Tuple[str, str, str]] = []
    for content, source_id in entries:
        if content in bad_contents:
            logger.info(f"{session_id} 跳过 LLM 判定为烂梗的黑话：content={content}, source_id={source_id}")
            rejected.append((content, source_id, "LLM 判定"))
            continue
        kept.append((content, source_id))
    return kept, rejected


def cleanup_bad_meme_jargons_from_db() -> int:
    """清理数据库中已学习到、且命中烂梗规则的 AI 黑话记录。

    手动创建的黑话记录不清理，避免误删用户主动保留的内容。

    Returns:
        清理的记录条数。
    """

    removed = 0
    with get_db_session() as session:
        statement = select(Jargon).where(col(Jargon.created_by) == JargonCreatedBy.AI)
        for record in session.exec(statement).yield_per(100):
            if removed >= _BAD_MEME_CLEANUP_BATCH_SIZE:
                break
            if record.content and is_bad_meme(record.content):
                session.delete(record)
                removed += 1
                logger.info(f"清理数据库中已学习的烂梗黑话：content={record.content}")
    if removed:
        logger.info(f"烂梗黑话清理完成，共清理 {removed} 条记录")
    return removed


async def periodic_bad_meme_cleanup() -> None:
    """按周期清理数据库中已学习的烂梗黑话。"""

    while True:
        try:
            await asyncio.to_thread(cleanup_bad_meme_jargons_from_db)
        except Exception as exc:
            logger.error(f"烂梗黑话周期清理失败: {exc}", exc_info=True)
        await asyncio.sleep(_BAD_MEME_CLEANUP_POLL_SECONDS)


_DANGER_WORDS_SYNC_POLL_SECONDS = 24 * 3600
_DANGER_WORDS_FETCH_TIMEOUT = 30

_ANYSEARCH_SEARCH_URL = "https://api.anysearch.com/v1/search"
_ANYSEARCH_TIMEOUT = 30
_ANYSEARCH_MAX_RESULTS = 5
_ANYSEARCH_ZONE = "cn"
_ANYSEARCH_LANGUAGE = "zh-CN"
_NETWORK_BAD_MEME_QUERIES: List[str] = [
    "2026年 网络烂梗 盘点 恶俗梗 黑话",
    "网络恶俗梗 低俗梗 烂梗 整治 名单",
    "近期 网络烂梗 无意义刷屏 黑话 流行语",
]


def _merge_danger_words_file(new_words: Sequence[str]) -> List[str]:
    """把拉取到的危险词与本地词库合并去重，写回本地文件，返回最新完整列表。"""

    local_words = set(get_danger_words())
    merged_words = local_words | {word.strip() for word in new_words if word.strip()}
    sorted_words = sorted(merged_words)
    with open(DANGER_WORDS_FILE, "w", encoding="utf-8") as f:
        f.write("# 危险词库（每日自动同步自 dablelv/dirty-cnwords，勿手动编辑；本地新增可追加）\n")
        f.write("# 每行一个词条，以 # 开头的行会被忽略。\n")
        f.write("# 命中危险词的黑话/表达候选将被直接禁止导入。\n\n")
        f.write("\n".join(sorted_words))
        f.write("\n")
    return sorted_words


async def _search_network_bad_meme_texts() -> List[str]:
    """通过 anysearch 匿名 API 搜索网络烂梗相关文章，返回拼接后的文本片段列表。

    anysearch 无需 API Key，匿名可用（限流更低），返回结构化 JSON。
    失败时返回空列表，不中断主流程。
    """

    results: List[str] = []
    client = get_main_http_client()
    for query in _NETWORK_BAD_MEME_QUERIES:
        try:
            resp = await client.post(
                _ANYSEARCH_SEARCH_URL,
                json={
                    "query": query,
                    "max_results": _ANYSEARCH_MAX_RESULTS,
                    "zone": _ANYSEARCH_ZONE,
                    "language": _ANYSEARCH_LANGUAGE,
                },
                timeout=_ANYSEARCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.error(f"anysearch 搜索失败（query={query}）：{exc}")
            continue
        for item in payload.get("data", {}).get("results", []):
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "") or item.get("snippet", "")).strip()
            if title or content:
                results.append(f"标题：{title}\n内容：{content[:2000]}")
    return results


def _collect_emoji_description_texts(limit: int = 200) -> List[str]:
    """收集表情包库中表情包的描述文本，作为网络烂梗/危险词自动发现的补充输入源。

    表情包描述由 VLM 生成，其中可能包含表情包画面上的文字/梗。
    失败时返回空列表，不中断提取流程。
    """

    texts: List[str] = []
    try:
        with get_db_session() as session:
            statement = (
                select(Images.description)
                .where(Images.image_type == ImageType.EMOJI, Images.description != "")
                .limit(limit)
            )
            for row in session.exec(statement).all():
                desc = str(row[0]).strip() if row else ""
                if desc:
                    texts.append(f"表情包描述：{desc[:200]}")
    except Exception as exc:
        logger.error(f"收集表情包描述失败: {exc}")
    return texts


def _parse_extract_response(response: str) -> List[str]:
    """解析网络烂梗提取 LLM 响应，返回词条列表。"""

    import json

    from json_repair import repair_json

    raw = (response or "").strip()
    if not raw:
        return []
    if match := re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL):
        raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = json.loads(repair_json(raw))
        except Exception as exc:
            logger.error(f"网络烂梗提取响应解析失败: {exc}, response={raw[:200]}")
            return []

    if not isinstance(parsed, list):
        logger.warning("网络烂梗提取响应格式异常，按空结果处理")
        return []

    terms: List[str] = []
    for item in parsed:
        term = str(item).strip()
        # 过滤单字词条，避免单字作为危险词子串匹配时大面积误伤正常对话
        if len(term) >= 2 and term not in terms:
            terms.append(term)
    return terms


async def extract_network_bad_meme_words(session_id: str = "") -> List[str]:
    """搜索网络烂梗并让 LLM 提取词条，返回候选烂梗词条列表。

    LLM 调用失败时返回空列表，不阻塞同步流程。
    """

    search_texts = await _search_network_bad_meme_texts()
    emoji_texts = _collect_emoji_description_texts()
    all_texts = search_texts + emoji_texts
    if not all_texts:
        logger.warning("网络烂梗搜索与表情包描述均无内容，跳过提取")
        return []

    prompt_template = prompt_manager.get_prompt("extract_network_bad_memes")
    prompt_template.add_context("search_results", "\n\n".join(all_texts))
    prompt = await prompt_manager.render_prompt(prompt_template)

    try:
        generation_result = await meme_judge_model.generate_response(
            prompt=prompt,
            options=LLMGenerationOptions(temperature=0.2),
            session_id=session_id,
        )
        return _parse_extract_response(generation_result.response)
    except Exception as exc:
        logger.error(f"{session_id} 网络烂梗提取失败：{exc}")
        return []


async def sync_danger_words() -> None:
    """通过 v6.gh-proxy.org 镜像拉取危险词源，合并写入本地词库。

    拉取失败时保留现有本地词库，不中断服务。
    """

    client = get_main_http_client()
    resp = await client.get(
        DANGER_WORDS_SOURCE_URL, timeout=_DANGER_WORDS_FETCH_TIMEOUT, follow_redirects=True
    )
    resp.raise_for_status()
    lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
    merged_words = _merge_danger_words_file(lines)
    logger.info(f"危险词库同步完成，共 {len(merged_words)} 条")


async def periodic_danger_word_sync() -> None:
    """按周期（默认每天）同步危险词库，并联网搜索新增网络烂梗词条。"""

    while True:
        try:
            await sync_danger_words()
        except Exception as exc:
            logger.error(f"危险词库周期同步失败: {exc}", exc_info=True)
        try:
            network_words = await extract_network_bad_meme_words()
            if network_words:
                merged_words = _merge_danger_words_file(network_words)
                logger.info(f"网络烂梗搜索提取完成，合并后词库共 {len(merged_words)} 条，新增 {len(network_words)} 条候选")
        except Exception as exc:
            logger.error(f"网络烂梗搜索提取失败: {exc}", exc_info=True)
        await asyncio.sleep(_DANGER_WORDS_SYNC_POLL_SECONDS)
