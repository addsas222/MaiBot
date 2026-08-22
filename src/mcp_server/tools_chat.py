"""聊天消息类 MCP 工具。

提供会话查询、历史读取与主动发送能力。发送链路复用麦麦业务层统一出口
``src.services.send_service``，会话 ID 一律通过 ``chat_manager`` 解析真实聊天流。
"""

from __future__ import annotations

from typing import Any

from src.chat.message_receive.chat_manager import chat_manager
from src.chat.utils.utils import get_bot_account
from src.common.message_repository import find_messages
from src.services import send_service


def register_chat_tools(mcp: Any) -> None:
    """注册聊天消息类工具。"""

    @mcp.tool()
    def list_chats(keyword: str = "") -> list[dict[str, str]]:
        """列出麦麦所有可用的聊天会话（群聊/私聊），返回展示名称与 session_id。

        Args:
            keyword: 可选，按名称或 session_id 模糊过滤。
        """
        options = chat_manager.get_named_session_options()
        items = [
            {"name": name, "session_id": session_id}
            for name, session_id in options.items()
            if not keyword.strip() or keyword.strip() in name or keyword.strip() in session_id
        ]
        items.sort(key=lambda item: item["name"])
        return items

    @mcp.tool()
    def get_chat_history(session_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """获取指定会话最近的聊天记录，按时间从旧到新排列。

        Args:
            session_id: 目标会话 ID，可通过 list_chats 获取。
            limit: 返回条数上限（1-200，默认 30）。
        """
        normalized_limit = max(1, min(int(limit), 200))
        messages = find_messages(session_id=session_id, limit=normalized_limit, limit_mode="latest")
        bot_account = get_bot_account(messages[0].platform) if messages else ""
        return [
            {
                "message_id": message.message_id,
                "timestamp": message.timestamp.timestamp(),
                "sender_id": message.message_info.user_info.user_id,
                "sender_nickname": message.message_info.user_info.user_nickname,
                "text": message.processed_plain_text or "",
                "is_bot": message.message_info.user_info.user_id == bot_account,
                "reply_to": message.reply_to,
            }
            for message in messages
        ]

    @mcp.tool()
    def resolve_chat(platform: str, target_id: str, chat_type: str) -> list[dict[str, Any]]:
        """按平台、目标 ID 与聊天类型解析麦麦真实存在的会话。

        Args:
            platform: 平台名，如 qq / telegram / webui。
            target_id: 群 ID（chat_type=group）或用户 ID（chat_type=private）。
            chat_type: group（群聊）或 private（私聊）。
        """
        sessions = chat_manager.resolve_sessions_by_target(
            platform=platform,
            target_id=target_id,
            chat_type=chat_type,
        )
        return [
            {
                "session_id": session.session_id,
                "platform": session.platform,
                "chat_name": session.group_name if session.is_group_session else session.user_nickname,
                "group_id": session.group_id,
                "user_id": session.user_id,
                "account_id": session.account_id,
                "last_active_timestamp": (
                    session.last_active_timestamp.timestamp() if session.last_active_timestamp else None
                ),
            }
            for session in sessions
        ]

    @mcp.tool()
    async def send_text_message(session_id: str, text: str) -> dict[str, Any]:
        """通过麦麦向指定会话主动发送一条文本消息。

        Args:
            session_id: 目标会话 ID，可通过 list_chats 获取。
            text: 要发送的文本内容。
        """
        session = chat_manager.get_existing_session_by_session_id(session_id)
        if session is None:
            return {"success": False, "error": f"会话不存在: {session_id}"}
        success = await send_service.text_to_stream(text=text, stream_id=session_id)
        return {"success": success, "session_id": session_id}
