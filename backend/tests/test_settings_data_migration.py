"""
Existing installations must not keep superseded or prohibited settings.

Found on a live install: every default changed in code had never reached the
user's account. The user_settings table kept the column defaults it was
created with, registration never wrote these fields, and the language-model
provider reads the most recent row and applies it application-wide. The live
row held use_ollama=0 (routing to the excluded local Qwen path),
embedding_model='Qwen3-Embedding-0.6B' (excluded and not on disk), a
Qwen-and-DeepSeek-first model priority, and a chunk size over the embedding
window.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config import DEFAULT_OLLAMA_MODEL_PRIORITY, settings
from database import _migrate_superseded_user_settings

TABLE = """
CREATE TABLE user_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL UNIQUE,
    use_ollama INTEGER NOT NULL DEFAULT 0,
    ollama_model_priority TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    rag_chunk_size INTEGER NOT NULL
)
"""


async def _db(tmp_path, rows):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(TABLE))
        for r in rows:
            await conn.execute(text(
                "INSERT INTO user_settings (user_id, use_ollama, ollama_model_priority, "
                "embedding_model, rag_chunk_size) VALUES (:u, :o, :p, :e, :c)"
            ), r)
    return engine


async def _rows(engine):
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT * FROM user_settings ORDER BY id"))
        return [dict(x) for x in r.mappings()]


async def _migrate(engine):
    async with engine.begin() as conn:
        await _migrate_superseded_user_settings(conn)


LIVE_ROW = {  # exactly what the live installation held
    "u": "real-user", "o": 0,
    "p": "gemma,qwen,llama,deepseek,mistral",
    "e": "Qwen3-Embedding-0.6B", "c": 400,
}


@pytest.mark.asyncio
async def test_the_live_row_is_repaired(tmp_path):
    engine = await _db(tmp_path, [LIVE_ROW])
    await _migrate(engine)
    row = (await _rows(engine))[0]
    assert row["use_ollama"] == 1
    assert row["embedding_model"] == settings.EMBEDDING_MODEL
    assert row["ollama_model_priority"] == DEFAULT_OLLAMA_MODEL_PRIORITY
    assert row["rag_chunk_size"] == settings.RAG_CHUNK_SIZE
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_prohibited_model_survives(tmp_path):
    engine = await _db(tmp_path, [LIVE_ROW])
    await _migrate(engine)
    row = (await _rows(engine))[0]
    blob = (row["ollama_model_priority"] + row["embedding_model"]).lower()
    assert "qwen" not in blob and "deepseek" not in blob
    await engine.dispose()


@pytest.mark.asyncio
async def test_custom_priority_keeps_order_minus_prohibited(tmp_path):
    """A deliberate choice is respected, except for the excluded entries."""
    engine = await _db(tmp_path, [{**LIVE_ROW, "p": "mistral-small,qwen2.5,llama3.1,deepseek-r1"}])
    await _migrate(engine)
    assert (await _rows(engine))[0]["ollama_model_priority"] == "mistral-small,llama3.1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_custom_priority_of_only_prohibited_falls_back_to_default(tmp_path):
    engine = await _db(tmp_path, [{**LIVE_ROW, "p": "qwen,deepseek"}])
    await _migrate(engine)
    assert (await _rows(engine))[0]["ollama_model_priority"] == DEFAULT_OLLAMA_MODEL_PRIORITY
    await engine.dispose()


@pytest.mark.asyncio
async def test_deliberate_custom_values_are_left_alone(tmp_path):
    """Only superseded defaults change; a user's own choices do not."""
    custom = {"u": "tuned", "o": 1, "p": "llama3.1,phi4",
              "e": "snowflake-arctic-embed-l", "c": 250}
    engine = await _db(tmp_path, [custom])
    await _migrate(engine)
    row = (await _rows(engine))[0]
    assert row["ollama_model_priority"] == "llama3.1,phi4"
    assert row["embedding_model"] == "snowflake-arctic-embed-l"
    assert row["rag_chunk_size"] == 250
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    engine = await _db(tmp_path, [LIVE_ROW])
    await _migrate(engine)
    first = await _rows(engine)
    await _migrate(engine)
    await _migrate(engine)
    assert await _rows(engine) == first
    await engine.dispose()


@pytest.mark.asyncio
async def test_empty_table_is_a_no_op(tmp_path):
    engine = await _db(tmp_path, [])
    await _migrate(engine)
    assert await _rows(engine) == []
    await engine.dispose()


def test_priority_default_has_one_definition():
    """Eight copies of this string are how it drifted; there must be one."""
    from pathlib import Path
    backend = Path(__file__).resolve().parent.parent
    hits = []
    for path in backend.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        if DEFAULT_OLLAMA_MODEL_PRIORITY in path.read_text(encoding="utf-8", errors="ignore"):
            hits.append(path.relative_to(backend).as_posix())
    assert hits == ["config.py"], f"priority default duplicated in: {hits}"


def test_default_priority_puts_the_strongest_permitted_model_first():
    assert DEFAULT_OLLAMA_MODEL_PRIORITY.split(",")[0] == "phi4"
    entries = DEFAULT_OLLAMA_MODEL_PRIORITY.lower()
    assert "qwen" not in entries and "deepseek" not in entries


def test_registration_writes_settings_explicitly():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "routers" / "auth.py").read_text(encoding="utf-8")
    for field in ("use_ollama", "ollama_model_priority", "embedding_model", "rag_chunk_size"):
        assert field in src.split("INSERT INTO user_settings")[1].split("VALUES")[0], field
