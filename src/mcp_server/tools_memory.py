"""记忆、黑话与表达类 MCP 工具。

记忆操作复用 ``src.services.memory_service`` 进程内共享内核；
黑话与表达直接读写数据库（ORM 见 ``src.common.database.database_model``）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import column
from sqlmodel import select

from src.chat.message_receive.chat_manager import chat_manager
from src.common.database.database import get_db_session
from src.common.database.database_model import Expression, Jargon, JargonCreatedBy
from src.learners.jargon_explainer import search_jargon
from src.services.memory_service import memory_service


def _normalize_jargon_created_by(jargon: Jargon) -> JargonCreatedBy:
    """兼容历史空值或异常值的黑话创建来源。"""

    if jargon.created_by in (JargonCreatedBy.MANUAL, JargonCreatedBy.MANUAL.value):
        return JargonCreatedBy.MANUAL
    return JargonCreatedBy.AI


def _jargon_scopes_overlap(jargon: Jargon, target_session_ids: set[str], target_is_global: bool) -> bool:
    """判断黑话记录作用域是否与目标手动记录重叠。"""

    if target_is_global or jargon.is_global:
        return True
    try:
        session_id_dict = json.loads(jargon.session_id_dict) if jargon.session_id_dict else {}
    except json.JSONDecodeError:
        session_id_dict = {}
    return bool(target_session_ids.intersection(session_id_dict))


def register_memory_tools(mcp: Any) -> None:
    """注册记忆、黑话与表达类工具。"""

    @mcp.tool()
    async def search_memory(query: str, limit: int = 5, session_id: str = "") -> dict[str, Any]:
        """搜索麦麦的记忆库。

        Args:
            query: 搜索关键词或问题。
            limit: 返回条数上限（1-20，默认 5）。
            session_id: 可选，限定搜索的聊天流范围。
        """
        normalized_limit = max(1, min(int(limit), 20))
        result = await memory_service.search(query, limit=normalized_limit, chat_id=session_id or "")
        return result.to_dict()

    @mcp.tool()
    async def get_memory_stats() -> dict[str, Any]:
        """获取麦麦记忆库的统计信息。"""
        return await memory_service.memory_stats()

    @mcp.tool()
    async def ingest_memory_text(text: str, session_id: str = "", tags: Optional[list[str]] = None) -> dict[str, Any]:
        """手动向麦麦记忆库写入一条文本记忆。

        Args:
            text: 要记住的文本内容。
            session_id: 可选，记忆归属的聊天流 ID。
            tags: 可选，记忆标签列表。
        """
        import time

        result = await memory_service.ingest_text(
            external_id=f"mcp-{int(time.time() * 1000)}",
            source_type="manual",
            text=text,
            chat_id=session_id or "",
            tags=tags,
        )
        return {
            "success": result.success,
            "stored_ids": result.stored_ids,
            "skipped_ids": result.skipped_ids,
            "detail": result.detail,
        }

    @mcp.tool()
    def search_jargon_keyword(keyword: str, limit: int = 10, session_id: str = "") -> list[dict[str, str]]:
        """搜索麦麦学会的黑话。

        Args:
            keyword: 黑话关键词。
            limit: 返回条数上限（默认 10）。
            session_id: 可选，限定搜索的聊天流范围。
        """
        normalized_limit = max(1, min(int(limit), 50))
        return search_jargon(keyword, chat_id=session_id or None, limit=normalized_limit)

    @mcp.tool()
    def list_jargons(limit: int = 50) -> list[dict[str, Any]]:
        """列出麦麦黑话库，按使用次数从高到低排列。

        Args:
            limit: 返回条数上限（1-200，默认 50）。
        """
        normalized_limit = max(1, min(int(limit), 200))
        with get_db_session() as session:
            jargons = session.exec(
                select(Jargon).order_by(Jargon.count.desc(), Jargon.updated_timestamp.desc()).limit(normalized_limit)
            ).all()
        return [
            {
                "id": jargon.id,
                "content": jargon.content,
                "meaning": jargon.meaning,
                "count": jargon.count,
                "is_global": jargon.is_global,
                "created_by": _normalize_jargon_created_by(jargon).value,
            }
            for jargon in jargons
        ]

    @mcp.tool()
    def create_jargon(content: str, meaning: str, session_id: str) -> dict[str, Any]:
        """手动创建一条黑话，归属到真实存在的聊天流。

        Args:
            content: 黑话内容。
            meaning: 黑话释义。
            session_id: 黑话归属的聊天流 ID（通过 list_chats 获取）。
        """
        content = str(content or "").strip()
        meaning = str(meaning or "").strip()
        if not content:
            return {"success": False, "error": "黑话内容不能为空"}
        session_id = str(session_id or "").strip()
        if chat_manager.get_existing_session_by_session_id(session_id) is None:
            return {"success": False, "error": f"聊天流不存在: {session_id}"}

        target_session_ids = {session_id}
        with get_db_session() as session:
            same_content_jargons = session.exec(select(Jargon).where(col(Jargon.content) == content)).all()
            existing = next(
                (
                    jargon
                    for jargon in same_content_jargons
                    if _normalize_jargon_created_by(jargon) == JargonCreatedBy.MANUAL
                    and _jargon_scopes_overlap(jargon, target_session_ids, False)
                ),
                None,
            )
            if existing is not None:
                return {"success": False, "error": "该范围中已存在相同内容的手动黑话"}

            replaced_ai_count = 0
            for existing_jargon in same_content_jargons:
                if _normalize_jargon_created_by(existing_jargon) != JargonCreatedBy.AI:
                    continue
                if not _jargon_scopes_overlap(existing_jargon, target_session_ids, False):
                    continue
                session.delete(existing_jargon)
                replaced_ai_count += 1

            jargon = Jargon(
                content=content,
                meaning=meaning,
                session_id_dict=json.dumps({session_id: 1}, ensure_ascii=False),
                count=0,
                is_jargon=bool(meaning),
                is_complete=False,
                is_global=False,
                created_by=JargonCreatedBy.MANUAL,
            )
            session.add(jargon)
            session.flush()
            jargon_id = jargon.id
        return {"success": True, "id": jargon_id, "replaced_ai_count": replaced_ai_count}

    @mcp.tool()
    def delete_jargon(jargon_id: int) -> dict[str, Any]:
        """删除指定 ID 的黑话。

        Args:
            jargon_id: 黑话 ID，可通过 list_jargons 获取。
        """
        with get_db_session() as session:
            jargon = session.get(Jargon, jargon_id)
            if jargon is None:
                return {"success": False, "error": f"黑话不存在: {jargon_id}"}
            session.delete(jargon)
        return {"success": True, "id": jargon_id}

    @mcp.tool()
    def list_expressions(session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """列出麦麦的表达方式，按使用次数从高到低排列。

        Args:
            session_id: 可选，仅列出该聊天流作用域内的表达方式。
            limit: 返回条数上限（1-200，默认 50）。
        """
        normalized_limit = max(1, min(int(limit), 200))
        statement = select(Expression).order_by(
            Expression.count.desc(), Expression.last_active_time.desc()
        ).limit(normalized_limit)
        if session_id.strip():
            statement = statement.where(
                (Expression.session_id == session_id.strip()) | (Expression.session_id.is_(None))
            )
        with get_db_session() as session:
            expressions = session.exec(statement).all()
        return [
            {
                "id": expression.id,
                "situation": expression.situation,
                "style": expression.style,
                "content_list": json.loads(expression.content_list) if expression.content_list else [],
                "count": expression.count,
                "checked": expression.checked,
                "session_id": expression.session_id,
            }
            for expression in expressions
        ]
