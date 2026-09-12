"""
test_embedding_model_setting.py — Unit tests for embedding model setting selection and listing.
"""

import pytest
from unittest.mock import patch, MagicMock
from models.settings import UserSettings, UserSettingsUpdate
from config import settings


def test_user_settings_model_default():
    """
    The default must be a permitted model.

    Qwen3-Embedding is Alibaba-origin and excluded for this deployment, so a
    fresh install must not land on it.
    """
    us = UserSettings(user_id="test_user")
    assert us.embedding_model == "mxbai-embed-large-v1"


def test_user_settings_update_optional_embedding_model():
    """Verify UserSettingsUpdate accepts embedding_model."""
    update = UserSettingsUpdate(embedding_model="snowflake-arctic-embed-l")
    assert update.embedding_model == "snowflake-arctic-embed-l"


@pytest.mark.asyncio
async def test_get_embedding_models_endpoint():
    """Test get_embedding_models endpoint logic."""
    from routers.settings_router import get_embedding_models

    mock_user = {"id": "user123"}
    res = await get_embedding_models(current_user=mock_user)

    assert "active_model" in res
    assert "models" in res
    assert isinstance(res["models"], list)

    model_ids = [m["id"] for m in res["models"]]
    assert "mxbai-embed-large-v1" in model_ids
    assert "snowflake-arctic-embed-l" in model_ids

    # The picker must not offer a prohibited model: doing so would let a user
    # reintroduce one from the interface.
    assert not [m for m in model_ids if "qwen" in m.lower()], (
        f"prohibited models offered in the picker: {model_ids}"
    )
