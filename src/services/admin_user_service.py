"""动态管理员列表服务：基于数据库 ``admin_users`` 表管理管理员身份。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any
import re

from sqlmodel import col, or_, select

from src.common.database.database import get_db_session
from src.common.database.database_model import AdminCreatedBy, AdminUser
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("admin_user_service")

# 出厂初始管理员（当前维护者账号），仅在表完全为空的首启种子时写入
PRESET_CURRENT_ADMIN_USER_ID = "543011300"

# 宽松用户 ID 校验：数字/字母/下划线/@/-/. 组合
_USER_ID_PATTERN = re.compile(r"[A-Za-z0-9_@.\-]+")


def _normalize_user_id(user_id: str) -> str:
    """剥除空白并规范化用户 ID。"""

    return str(user_id or "").strip()


def _normalize_platform(platform: str) -> str:
    """剥除空白的平台名；空串表示通配全平台。"""

    return str(platform or "").strip()


def _validate_user_id(user_id: str) -> str:
    """校验并返回规范化的用户 ID；不合法直接抛 ValueError。"""

    normalized = _normalize_user_id(user_id)
    if not normalized:
        raise ValueError("user_id 不能为空")
    if _USER_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"user_id 含非法字符，仅允许数字/字母/下划线/@/-/.：{user_id!r}")
    return normalized


def _record_to_dict(record: AdminUser) -> dict[str, Any]:
    """把 AdminUser 行转换为对外字典（created_by 统一输出枚举值文本）。"""

    created_by = record.created_by
    if isinstance(created_by, AdminCreatedBy):
        created_by_text: str = created_by.value
    else:
        created_by_text = str(created_by)
    return {
        "id": record.id,
        "platform": record.platform,
        "user_id": record.user_id,
        "created_by": created_by_text,
        "note": record.note,
    }


async def is_admin(platform: str, user_id: str) -> bool:
    """判断用户是否为管理员。

    命中规则：存在 ``user_id`` 匹配且 ``platform`` 等于传入值或为空串（通配全平台）的条目。
    ``user_id`` 为空白直接返回 False。
    """

    normalized_user_id = _normalize_user_id(user_id)
    if not normalized_user_id:
        return False
    normalized_platform = _normalize_platform(platform)
    with get_db_session() as session:
        statement = select(AdminUser).where(
            col(AdminUser.user_id) == normalized_user_id,
            or_(
                col(AdminUser.platform) == normalized_platform,
                col(AdminUser.platform) == "",
            ),
        )
        return session.exec(statement).first() is not None


async def list_admins() -> list[dict]:
    """列出全部管理员条目（id/platform/user_id/created_by/note）。"""

    with get_db_session() as session:
        records = session.exec(select(AdminUser).order_by(col(AdminUser.id).asc())).all()
        return [_record_to_dict(record) for record in records]


async def add_admin(
    user_id: str,
    platform: str = "qq",
    note: str = "",
    created_by: AdminCreatedBy = AdminCreatedBy.MANUAL,
) -> dict:
    """新增管理员条目；同平台同 user_id 已存在时抛 ValueError。"""

    normalized_user_id = _validate_user_id(user_id)
    normalized_platform = _normalize_platform(platform)
    now = datetime.now()
    with get_db_session() as session:
        existing = session.exec(
            select(AdminUser).where(
                col(AdminUser.platform) == normalized_platform,
                col(AdminUser.user_id) == normalized_user_id,
            )
        ).first()
        if existing is not None:
            raise ValueError(
                f"管理员已存在：platform={normalized_platform!r} user_id={normalized_user_id!r}"
            )
        record = AdminUser(
            platform=normalized_platform,
            user_id=normalized_user_id,
            created_by=created_by,
            note=str(note or ""),
            created_timestamp=now,
            updated_timestamp=now,
        )
        session.add(record)
        session.flush()
        result = _record_to_dict(record)
    logger.info(
        f"已新增管理员：platform={normalized_platform!r} user_id={normalized_user_id!r} "
        f"来源={created_by.value}"
    )
    return result


async def remove_admin(user_id: str, platform: str = "qq") -> bool:
    """移除管理员条目；不存在时抛 ValueError。"""

    normalized_user_id = _normalize_user_id(user_id)
    if not normalized_user_id:
        raise ValueError("user_id 不能为空")
    normalized_platform = _normalize_platform(platform)
    with get_db_session() as session:
        statement = select(AdminUser).where(
            col(AdminUser.platform) == normalized_platform,
            col(AdminUser.user_id) == normalized_user_id,
        )
        record = session.exec(statement).first()
        if record is None:
            raise ValueError(
                f"管理员不存在：platform={normalized_platform!r} user_id={normalized_user_id!r}"
            )
        # 防锁死保护：至少保留一个管理员条目，否则所有管理入口永久失效且种子不会复活
        total_admins = len(session.exec(select(col(AdminUser.id))).all())
        if total_admins <= 1:
            raise ValueError("不能移除最后一个管理员条目，否则将失去全部管理入口")
        session.delete(record)
    logger.info(f"已移除管理员：platform={normalized_platform!r} user_id={normalized_user_id!r}")
    return True


async def ensure_seed_admins() -> None:
    """首次初始化出厂管理员种子。

    仅当表完全为空时执行：写入配置 ``global_config.admin.preset_users`` 中每个用户
    （created_by=PRESET，备注"出厂预设"），以及内置初始管理员 ``543011300``
    （created_by=PRESET_CURRENT，备注"初始管理员"）。
    幂等性由"表空才执行"保证：管理员被移除后重启不会复活。
    """

    with get_db_session() as session:
        first_record = session.exec(select(AdminUser).limit(1)).first()
        if first_record is not None:
            return

        now = datetime.now()
        seen_user_ids: set[str] = set()
        rows_to_add: list[AdminUser] = []
        for raw_user_id in list(global_config.admin.preset_users):
            normalized = _validate_user_id(raw_user_id)
            if normalized in seen_user_ids:
                continue
            seen_user_ids.add(normalized)
            rows_to_add.append(
                AdminUser(
                    platform="",  # 通配全平台
                    user_id=normalized,
                    created_by=AdminCreatedBy.PRESET,
                    note="出厂预设",
                    created_timestamp=now,
                    updated_timestamp=now,
                )
            )

        if PRESET_CURRENT_ADMIN_USER_ID not in seen_user_ids:
            rows_to_add.append(
                AdminUser(
                    platform="",
                    user_id=PRESET_CURRENT_ADMIN_USER_ID,
                    created_by=AdminCreatedBy.PRESET_CURRENT,
                    note="初始管理员",
                    created_timestamp=now,
                    updated_timestamp=now,
                )
            )

        for row in rows_to_add:
            session.add(row)
    logger.info(f"已完成出厂管理员种子写入（表此前为空）：user_ids={[row.user_id for row in rows_to_add]}")


# 单一导入入口：既可 ``from src.services.admin_user_service import is_admin``，
# 也可 ``from src.services import admin_user_service`` 后以模块属性调用。
admin_user_service = SimpleNamespace(
    is_admin=is_admin,
    list_admins=list_admins,
    add_admin=add_admin,
    remove_admin=remove_admin,
    ensure_seed_admins=ensure_seed_admins,
)
