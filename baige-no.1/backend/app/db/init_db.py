import asyncio
from sqlalchemy import select
from app.db.base import AsyncSessionLocal, engine, Base
from app.models import user, role, menu, log, message
from app.core.security import get_password_hash
from app.core.config import settings


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
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
            
            # Create admin user
            admin_user = user.User(
                username=settings.FIRST_SUPERUSER,
                email="admin@baige.com",
                full_name="系统管理员",
                hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
                is_superuser=True,
                is_active=True,
                role_id=admin_role.id,
            )
            db.add(admin_user)
            await db.commit()
            print(f"✅ 初始化完成: 管理员账号 {settings.FIRST_SUPERUSER} / {settings.FIRST_SUPERUSER_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(init_db())
