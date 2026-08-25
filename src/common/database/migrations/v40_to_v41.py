"""v40 schema 升级到 v41：新增动态管理员列表表。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v40_to_v41(context: MigrationExecutionContext) -> None:
    """创建动态管理员列表表 admin_users；种子数据由应用层 ensure_seed_admins 负责写入。"""

    context.start_progress(
        total_tables=1,
        total_records=1,
        description="v40 -> v41 迁移进度",
        table_unit_name="表",
        record_unit_name="表",
    )
    connection = context.connection
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER NOT NULL,
            platform VARCHAR(32) NOT NULL DEFAULT 'qq',
            user_id VARCHAR(64) NOT NULL,
            created_by VARCHAR(16) NOT NULL,
            note VARCHAR(255) NOT NULL DEFAULT '',
            created_timestamp DATETIME NOT NULL,
            updated_timestamp DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_admin_users_platform_user UNIQUE (platform, user_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_admin_users_platform ON admin_users (platform)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_admin_users_user_id ON admin_users (user_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_admin_users_created_timestamp ON admin_users (created_timestamp)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_admin_users_updated_timestamp ON admin_users (updated_timestamp)"
    )
    context.advance_progress(records=1, completed_tables=1, item_name="admin_users")
    logger.info("v40 -> v41 数据库迁移完成：动态管理员表已创建")
