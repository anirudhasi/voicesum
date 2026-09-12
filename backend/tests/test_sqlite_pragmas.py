"""
W3.2 — SQLite must be configured for concurrent use.

The engine was created with only check_same_thread=False: no WAL, no
busy_timeout, no synchronous setting. This application's normal state is a
long pipeline writing while the interface polls for progress, which under the
default rollback journal blocks readers and surfaces to the user as
"database is locked".
"""
import asyncio

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from database import SQLITE_PRAGMAS, _install_sqlite_pragmas


import pytest_asyncio


@pytest_asyncio.fixture
async def engine(tmp_path):
    """A configured engine per test. pytest_asyncio.fixture, not pytest.fixture:
    a plain fixture would hand the test the async generator itself."""
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}",
        connect_args={"check_same_thread": False},
    )
    _install_sqlite_pragmas(eng)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _pragma(eng, name):
    async with eng.connect() as conn:
        result = await conn.execute(text(f"PRAGMA {name}"))
        row = result.fetchone()
    return row[0] if row else None


# ── The pragmas actually take effect ───────────────────────────────────────

@pytest.mark.asyncio
async def test_wal_is_enabled(engine):
    """The fix for reader blocking during a long pipeline write."""
    assert str(await _pragma(engine, "journal_mode")).lower() == "wal"


@pytest.mark.asyncio
async def test_busy_timeout_is_set(engine):
    """Contention should wait, not raise immediately."""
    assert int(await _pragma(engine, "busy_timeout")) == 10000


@pytest.mark.asyncio
async def test_synchronous_is_normal(engine):
    # 1 == NORMAL
    assert int(await _pragma(engine, "synchronous")) == 1


@pytest.mark.asyncio
async def test_temp_store_is_memory(engine):
    # 2 == MEMORY
    assert int(await _pragma(engine, "temp_store")) == 2


@pytest.mark.asyncio
async def test_pragmas_apply_to_every_new_connection(engine):
    """A pool connection opened later must be configured too."""
    for _ in range(3):
        assert str(await _pragma(engine, "journal_mode")).lower() == "wal"
        assert int(await _pragma(engine, "busy_timeout")) == 10000


# ── The behaviour that motivated the change ────────────────────────────────

@pytest.mark.asyncio
async def test_a_reader_is_not_blocked_by_an_open_write(engine):
    """
    The scenario that produced "database is locked": the pipeline holds a
    write transaction while the interface polls.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))

    async with engine.connect() as writer:
        await writer.execute(text("BEGIN"))
        await writer.execute(text("INSERT INTO t (v) VALUES ('pending')"))

        # Reader must complete while the write transaction is still open.
        async with engine.connect() as reader:
            result = await reader.execute(text("SELECT COUNT(*) FROM t"))
            assert result.fetchone()[0] == 0   # uncommitted write not visible

        await writer.rollback()


@pytest.mark.asyncio
async def test_concurrent_readers_during_writes(engine):
    """Several readers polling while a writer works, as the UI does."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        await conn.execute(text("INSERT INTO t (v) VALUES ('seed')"))

    async def read_once():
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM t"))
            return result.fetchone()[0]

    async def write_many():
        for i in range(20):
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO t (v) VALUES (:v)"), {"v": f"row-{i}"}
                )
            await asyncio.sleep(0)

    results = await asyncio.gather(
        write_many(), *[read_once() for _ in range(10)]
    )
    assert all(isinstance(r, int) for r in results[1:]), "a reader failed"


# ── Configuration is declared, not scattered ───────────────────────────────

def test_pragma_set_is_declared_in_one_place():
    names = {name for name, _ in SQLITE_PRAGMAS}
    assert {"journal_mode", "busy_timeout", "synchronous"} <= names


def test_foreign_keys_is_deliberately_absent():
    """
    Enforcement is a behaviour change, not a configuration one: the two
    declared constraints have never been enforced, so existing installations
    may hold rows that violate them. Enabling it needs a data audit first.
    """
    names = {name for name, _ in SQLITE_PRAGMAS}
    assert "foreign_keys" not in names


def test_a_failing_pragma_does_not_break_the_connection(tmp_path, monkeypatch):
    """One unsupported pragma must not make connections unusable."""
    import database

    monkeypatch.setattr(
        database, "SQLITE_PRAGMAS", (("not_a_real_pragma", "1"), ("busy_timeout", "10000"))
    )

    async def run():
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
        database._install_sqlite_pragmas(eng)
        async with eng.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.fetchone()[0] == 1
        await eng.dispose()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
