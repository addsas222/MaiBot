"""酒馆式世界书触发注入器。

扫描最近聊天消息中的世界书触发词，命中条目作为一次性设定注入回复上下文；
另提供首次私聊接触时的开场白提示。数据来自
``data/character_cards/_worldbooks/*.json``（由 SillyTavern 世界书导入生成）
与 ``_active_card.json``（由角色卡"应用为人格"生成）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger
from src.common.message_repository import count_messages, find_messages
from src.common.version import PROJECT_ROOT

logger = get_logger("maisaka_worldbook")

WORLDBOOK_INDEX_DIR = Path(PROJECT_ROOT) / "data" / "character_cards" / "_worldbooks"
ACTIVE_CARD_PATH = Path(PROJECT_ROOT) / "data" / "character_cards" / "_active_card.json"

SCAN_WINDOW_SIZE = 12
_MAX_TOTAL_CHARS = 2000


class WorldbookIndexCache:
    """按目录 mtime 缓存合并后的世界书条目，避免每轮回复重复读盘解析。"""

    def __init__(self) -> None:
        self._entries: List[Dict[str, str]] = []
        self._signature: Optional[Tuple[int, ...]] = None

    def _current_signature(self) -> Tuple[int, ...]:
        if not WORLDBOOK_INDEX_DIR.is_dir():
            return (0,)
        files = sorted(WORLDBOOK_INDEX_DIR.glob("*.json"))
        return tuple(int(p.stat().st_mtime) for p in files)

    def get(self) -> List[Dict[str, str]]:
        try:
            signature = self._current_signature()
        except OSError:
            signature = None
        if signature is not None and self._signature == signature:
            return self._entries
        entries: List[Dict[str, str]] = []
        if WORLDBOOK_INDEX_DIR.is_dir():
            for path in sorted(WORLDBOOK_INDEX_DIR.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(payload, list):
                    entries.extend(item for item in payload if isinstance(item, dict))
        self._entries = entries
        self._signature = signature
        return entries


_cache = WorldbookIndexCache()


def _load_active_first_mes() -> str:
    try:
        payload = json.loads(ACTIVE_CARD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("first_mes") or "").strip() if isinstance(payload, dict) else ""


def build_worldbook_injection(session_id: str) -> str:
    """对最近消息做触发词扫描，返回命中的世界书设定文本；无命中返回空串。

    仅在 SillyTavern 子内核激活时生效。
    """
    from src.services.external_app_service import get_external_app_service

    if not get_external_app_service().is_engine_active("sillytavern"):
        return ""
    session = chat_manager.get_existing_session_by_session_id(str(session_id or "").strip())
    if session is None:
        return ""
    entries = _cache.get()
    if not entries:
        return ""

    recent = find_messages(
        session_id=session.session_id,
        sort=[("time", -1)],
        limit=SCAN_WINDOW_SIZE,
    )
    if not recent:
        return ""
    haystack = "\n".join(
        str(getattr(message, "processed_plain_text", "") or "") for message in recent
    ).casefold()
    if not haystack.strip():
        return ""

    matched: Dict[str, Dict[str, str]] = {}
    total_chars = 0
    for entry in entries:
        keys = [str(k).strip().casefold() for k in entry.get("keys", []) if str(k).strip()]
        content = str(entry.get("content") or "").strip()
        if not keys or not content:
            continue
        uid = str(entry.get("uid") or "")
        if uid in matched:
            continue
        if any(key in haystack for key in keys):
            matched[uid] = entry
            total_chars += len(content)
            if total_chars >= _MAX_TOTAL_CHARS:
                break

    if not matched:
        return ""
    sections: List[str] = []
    used_chars = 0
    for entry in matched.values():
        comment = str(entry.get("comment") or "").strip()
        content = str(entry.get("content") or "").strip()
        if used_chars + len(content) > _MAX_TOTAL_CHARS:
            break
        title = f"（{comment}）" if comment else ""
        sections.append(f"- {title}{content}")
        used_chars += len(content)
    if not sections:
        return ""
    logger.debug(f"世界书触发注入: 命中 {len(sections)} 条")
    return "【世界书设定-内部参考】以下设定与近期对话相关，回答时可参考：\n" + "\n".join(sections)


def build_greeting_hint_if_first_contact(session_id: str) -> str:
    """首次私聊接触且激活卡带开场白时，返回引导本次回复以开场白开头的提示。

    仅在 SillyTavern 子内核激活时生效。
    """
    from src.services.external_app_service import get_external_app_service

    if not get_external_app_service().is_engine_active("sillytavern"):
        return ""
    session = chat_manager.get_existing_session_by_session_id(str(session_id or "").strip())
    if session is None or session.is_group_session:
        return ""
    first_mes = _load_active_first_mes()
    if not first_mes:
        return ""
    try:
        if count_messages(session_id=session.session_id) > 1:
            return ""
    except Exception as exc:
        logger.debug(f"开场白判断跳过：消息计数失败（{exc}）")
        return ""
    return (
        "【初次见面】这是你与对方的第一次对话。请以这句开场白开启对话（可自然改写）：\n"
        f"{first_mes}"
    )
