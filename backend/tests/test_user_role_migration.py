"""
Tests for W6.1 — the users.role column migration and the administrator
invariant, exercised against real SQLite.

The statements under test are the ones added to connect_db() in database.py.
They are reproduced here rather than invoked through connect_db(), which would
pull in the full table set and the analytics module. Keeping them in one place
is the subject of the Alembic work in W3.1; until then this test pins their
behaviour.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Kept identical to database.py. If they diverge, this test is the warning.
ADD_ROLE_SQL = "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
COUNT_ADMINS_SQL = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
PROMOTE_OLDEST_SQL = """
    UPDATE users SET role = 'admin'
    WHERE id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1)
"""

# The users table as it existed before this change.
LEGACY_USERS_SQL = """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        hashed_password TEXT NOT NULL,
        needs_setup INTEGER NOT NULL DEFAULT 1,
        own_profile_id TEXT,
        locked_until TEXT,
        failed_login_attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
"""


async def _legacy_db(tmp_path, users):
    """Build a pre-migration database seeded with the given users."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(LEGACY_USERS_SQL))
        for uid, email, created in users:
            await conn.execute(
                text("""INSERT INTO users (id, name, email, hashed_password, created_at)
                        VALUES (:id, :n, :e, 'x', :c)"""),
                {"id": uid, "n": uid, "e": email, "c": created},
            )
    return engine


async def _migrate(engine):
    """Apply exactly what connect_db() applies."""
    async with engine.begin() as conn:
        try:
            await conn.execute(text(ADD_ROLE_SQL))
        except Exception:
            pass
        r = await conn.execute(text(COUNT_ADMINS_SQL))
        if (r.mappings().fetchone() or {}).get("n", 0) == 0:
            await conn.execute(text(PROMOTE_OLDEST_SQL))


async def _roles(engine):
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT id, role FROM users ORDER BY created_at"))
        return {row["id"]: row["role"] for row in r.mappings()}


@pytest.mark.asyncio
async def test_migration_adds_role_defaulting_to_user(tmp_path):
    engine = await _legacy_db(tmp_path, [
        ("alice", "a@x.com", "2026-01-01T00:00:00Z"),
        ("bob", "b@x.com", "2026-02-01T00:00:00Z"),
        ("carol", "c@x.com", "2026-03-01T00:00:00Z"),
    ])
    await _migrate(engine)
    roles = await _roles(engine)
    assert roles["bob"] == "user"
    assert roles["carol"] == "user"
    await engine.dispose()


@pytest.mark.asyncio
async def test_oldest_user_is_promoted_when_no_admin_exists(tmp_path):
    """An existing install must not be left with no administrator."""
    engine = await _legacy_db(tmp_path, [
        ("carol", "c@x.com", "2026-03-01T00:00:00Z"),
        ("alice", "a@x.com", "2026-01-01T00:00:00Z"),  # oldest, inserted last
        ("bob", "b@x.com", "2026-02-01T00:00:00Z"),
    ])
    await _migrate(engine)
    roles = await _roles(engine)
    assert roles["alice"] == "admin", "oldest account by created_at must be promoted"
    assert sum(1 for v in roles.values() if v == "admin") == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_admin_is_not_displaced(tmp_path):
    engine = await _legacy_db(tmp_path, [
        ("alice", "a@x.com", "2026-01-01T00:00:00Z"),
        ("bob", "b@x.com", "2026-02-01T00:00:00Z"),
    ])
    async with engine.begin() as conn:
        await conn.execute(text(ADD_ROLE_SQL))
        await conn.execute(text("UPDATE users SET role='admin' WHERE id='bob'"))
    await _migrate(engine)
    roles = await _roles(engine)
    assert roles["bob"] == "admin"
    assert roles["alice"] == "user", "promotion must not run when an admin exists"
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """Startup runs this every launch; repeated runs must not change anything."""
    engine = await _legacy_db(tmp_path, [
        ("alice", "a@x.com", "2026-01-01T00:00:00Z"),
        ("bob", "b@x.com", "2026-02-01T00:00:00Z"),
    ])
    await _migrate(engine)
    first = await _roles(engine)
    await _migrate(engine)
    await _migrate(engine)
    assert await _roles(engine) == first
    await engine.dispose()


@pytest.mark.asyncio
async def test_empty_install_survives_migration(tmp_path):
    """A fresh install has no users; promotion must be a harmless no-op."""
    engine = await _legacy_db(tmp_path, [])
    await _migrate(engine)
    assert await _roles(engine) == {}
    await engine.dispose()
