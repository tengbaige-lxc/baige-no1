import asyncio
import secrets
from sqlalchemy import select
from app.db.base import AsyncSessionLocal, engine, Base
from app.models import user, role, menu, log, message
from app.core.security import get_password_hash
from app.core.config import settings


async def init_db():
    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight schema patch for existing SQLite databases (no Alembic yet)
        def _ensure_columns(sync_conn):
            rows = sync_conn.exec_driver_sql("PRAGMA table_info(risk_configs)").fetchall()
            columns = {r[1] for r in rows}
            if "max_symbol_margin_percent" not in columns:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE risk_configs ADD COLUMN max_symbol_margin_percent FLOAT DEFAULT 0.30"
                )
        await conn.run_sync(_ensure_columns)
    
    async with AsyncSessionLocal() as db:
        # Check if admin exists
        result = await db.execute(
            select(user.User).where(user.User.username == settings.FIRST_SUPERUSER)
        )
        admin = result.scalar_one_or_none()
        
        if not admin:
            # Create default roles
            admin_role = role.Role(name="超级管理员", code="super_admin", description="系统超级管理员")
            user_role = role.Role(name="普通用户", code="user", description="普通用户")
            db.add_all([admin_role, user_role])
            await db.flush()
            
            # Phase 3b（S2）：不再有 admin123 固定默认口令。
            # 环境变量显式指定 → 按它建号且绝不回显；未指定 → 随机生成，
            # 只在创建这一次回显（否则运维没有任何途径拿到首登口令）。
            initial_password = settings.FIRST_SUPERUSER_PASSWORD
            generated = initial_password is None
            if generated:
                initial_password = secrets.token_urlsafe(12)

            admin_user = user.User(
                username=settings.FIRST_SUPERUSER,
                email="admin@baige.com",
                full_name="系统管理员",
                hashed_password=get_password_hash(initial_password),
                is_superuser=True,
                is_active=True,
                role_id=admin_role.id,
            )
            db.add(admin_user)
            await db.commit()
            if generated:
                print(
                    f"✅ 初始化完成: 管理员账号 {settings.FIRST_SUPERUSER}，"
                    f"初始口令(随机生成，仅本次显示，请立即登录修改): {initial_password}"
                )
            else:
                print(
                    f"✅ 初始化完成: 管理员账号 {settings.FIRST_SUPERUSER}"
                    f"（口令来自环境变量 FIRST_SUPERUSER_PASSWORD，不回显）"
                )


if __name__ == "__main__":
    asyncio.run(init_db())
