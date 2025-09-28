import asyncio
import logging
import random
import time

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import AsyncAdaptedQueuePool, NullPool, PoolProxiedConnection, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text
from sqlalchemy.util import await_only


class FaultTolerantQueuePool(AsyncAdaptedQueuePool):
    def connect(self) -> PoolProxiedConnection:
        retries = 3
        retry_delay = 0.2  # retry delays: 200ms, 400ms, 800ms

        def on_invalidate(_dbapi_connection, _connection_record, _exception):
            nonlocal retries
            # pre_ping does one retry, so reduce number of retries
            retries -= 1

        event.listen(self, "invalidate", on_invalidate)
        try:
            while True:
                try:
                    return super().connect()
                except Exception:
                    if retries <= 1:
                        raise

                    retries -= 1
                    retry_delay *= 2

                    # add 10% jitter
                    sleep_sec = retry_delay + random.random() * 0.1 * retry_delay
                    await_only(asyncio.sleep(sleep_sec))
        finally:
            event.remove(self, "invalidate", on_invalidate)


class FaultTolerantNullPool(NullPool):
    def connect(self) -> PoolProxiedConnection:
        retries = 3
        retry_delay = 0.2  # retry delays: 200ms, 400ms, 800ms

        while True:
            try:
                return super().connect()
            except Exception:
                if retries <= 1:
                    raise

                retries -= 1
                retry_delay *= 2

                # add 10% jitter
                sleep_sec = retry_delay + random.random() * 0.1 * retry_delay

                if self._is_asyncio:
                    await_only(asyncio.sleep(sleep_sec))
                else:
                    time.sleep(sleep_sec)


# DB setup
DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db"

engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    echo_pool=True,
    # pool_pre_ping=True, # use with FaultTolerantQueuePool
    # pool_size=1,  # use with FaultTolerantQueuePool
    # max_overflow=10,  # use with FaultTolerantQueuePool
    # pool_timeout=2,  # use with FaultTolerantQueuePool
    connect_args={"timeout": 2, "command_timeout": 2},
    poolclass=FaultTolerantNullPool,
    # poolclass=FaultTolerantQueuePool,  # uncomment to use QueuePool
)

session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# FastAPI app and endpoint
app = FastAPI(title="FastAPI PostgreSQL App", version="1.0.0")


async def get_db():
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


@app.get("/pg_version/")
async def pg_version(db: AsyncSession = Depends(get_db)):
    try:
        async with db.begin():
            result = await db.execute(text("SELECT version()"))
            return {"postgres_version": result.scalars().first()}
    except Exception as e:
        logging.error(str(e))
        raise HTTPException(status_code=500, detail="Error: " + str(e))
