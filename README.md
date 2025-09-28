# SqlAlchemy Fault Tolerant Pool
Minimal demo for SqlAlchemy connection pools, with built-in retry logic.

## Overview

Lack of connection retries is one of the gaps of SQLAlchemy. This mechanism is especially useful in microservices environments, where connectivity is affected by network congestion, port exhaustion, or container scaling side effects.

### SQLAlchemy fundamentals

It's not intuitive, but SQLAlchemy doesn't establish DB connection when the `Session` is created - e.g. at the start of a FastAPI request. A connection is only requested once DB transaction begins — which is scattered across your code. To make things worse, SQLAlchemy begins transactions implicitly, to comply with PEP-249. TLDR: it's really hard to track DB connection lifecycle in the application code.

There is a central place for DB connection management though - SQLAlchemy connection pools. Within the pool, there are two main scenarios: creating new DB connection, and checking-out existing one. If you're using PgBouncer with Postgres (as you should), `NullPool` is the right choice - it doesn't cache any connections. Otherwise, use `AsyncAdaptedQueuePool`.

### Measuring connectivity issues

All real-world software problems must have measurement. This is especially true for reliability problems. Once we have measurement, we can build dashboards, alerts, and perform deep dives into specific incidents.

SQLAlchemy has robust event API that can be used for logging, but it doesn't emit events for connection errors. One option is to query application logs or create log-based metrics. Tip: search for `sqlalchemy.exc` in logs, or DBAPI-specific errors like `ConnectionRefusedError` from `asyncpg`. Another option is to override the Pool class and instrument it directly with OpenTelemetry.

## Prototyping and testing

Prototyping is essential, since SQLAlchemy can be used in many different ways: sync or async, with different DBAPI drivers, etc. I tested my solution with both `asyncpg` and `psycopg` drivers in async mode.

I'm a big fan of imperative style, and luckily SQLAlchemy class hierarchy is pretty straightforward. Extending base classes makes it easy to add custom observability and retry logic. I tested solutions for both pooled and non-pooled connections - locally with Docker, and on GCP. Check the guidance below.

## Testing

### Setup python environment
```shell
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Testing different pool implementations

To test with connection pooling, create engine with following arguments:

```python
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    echo_pool=True,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=2,
    connect_args={"timeout": 2},
    poolclass=FaultTolerantQueuePool,
)
```

To test without connection pooling (e.g. when using PgBouncer), create engine with following arguments:

```python
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    echo_pool=True,
    connect_args={"timeout": 2},
    poolclass=FaultTolerantNullPool,
)
```


### Launch server
Postgres instance:

```shell
docker run --name postgres-dev \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=test_db \
  -p 5432:5432 \
  postgres:latest
```

FastAPI instance:
```shell
uvicorn demo:app
```

### Test DB connection
```shell
curl http://localhost:8000/pg_version/
```

To verify connection retries, stop Postgres instance, and invoce `curl` again. The pool class is attempting connection several times before raising the exception.

## Adopting to production environment

The only missing piece before shipping to production is logging and metrics - add to `connect` method, using your frameworks of choice.

Note that `FaultTolerantNullPool` can be used in both sync and async contexts, so it selects appropriate sleep option to delay retries.