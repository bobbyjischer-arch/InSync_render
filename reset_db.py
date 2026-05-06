"""
reset_db.py
===========
Drops ALL tables and recreates them from the current SQLAlchemy models.
Use only in development — all data will be lost.

Usage:
    python reset_db.py           # reset only
    python reset_db.py --seed    # reset + create a test admin user (id=1)
"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from werkzeug.security import generate_password_hash

from database.engine import ASYNC_DATABASE_URL
from database.models import Base, User


async def reset(seed: bool = False) -> None:
    engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        print("⚠️  Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("✅  Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)

    if seed:
        AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with AsyncSessionLocal() as db:
            admin = User(
                first_name="admin",
                email="admin@insync.local",
                password_hash=generate_password_hash("Admin1234"),
                reminder_minutes_before=60,
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
            print(f"🌱  Seed user: id={admin.id}  login=admin  password=Admin1234")

    await engine.dispose()
    print("✅  Done.")


if __name__ == "__main__":
    asyncio.run(reset(seed="--seed" in sys.argv))
