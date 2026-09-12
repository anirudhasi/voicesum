"""
W1.9 — no prompt key may resolve to an empty template.

An empty prompt does not fail. The model receives nothing and returns something
plausible but ungrounded, and the defect surfaces much later as poor output
rather than as an error. Three keys were affected:

    raw_mom_extraction          mapped to "" despite RAW_MOM_EXTRACTION_PROMPT
    raw_mom_repair              mapped to "" despite RAW_MOM_REPAIR_PROMPT
    rom_enhance_action_points   a placeholder constant on a live call path

_get_prompt now raises rather than returning an empty string.
"""
import pytest

import services.ai_provider as ai_provider
from services.ai_provider import _get_prompt


def _registry_keys():
    """Every key _get_prompt can resolve, read from its own constant map."""
    import inspect
    import re
    source = inspect.getsource(_get_prompt)
    return re.findall(r'^\s*"([a-z0-9_]+)":\s*\S', source, re.MULTILINE)


def test_registry_is_not_empty():
    keys = _registry_keys()
    assert len(keys) > 20, f"expected the full registry, found {len(keys)}"


@pytest.mark.parametrize("key", _registry_keys())
def test_every_registered_key_resolves_to_a_real_template(key):
    """The guarantee: no registered key yields an empty prompt."""
    template = _get_prompt(key)
    assert template and template.strip(), f"prompt '{key}' resolves empty"
    assert len(template.strip()) > 20, f"prompt '{key}' is implausibly short"


def test_unknown_key_raises_rather_than_returning_empty():
    with pytest.raises(KeyError) as exc:
        _get_prompt("no_such_prompt_key")
    assert "no_such_prompt_key" in str(exc.value)


def test_the_placeholder_key_is_no_longer_registered():
    """
    rom_enhance_action_points was a documented placeholder sitting on a live
    call path in enhance_action_points(). It now raises instead of sending an
    empty prompt to the model.
    """
    assert "rom_enhance_action_points" not in _registry_keys()
    with pytest.raises(KeyError):
        _get_prompt("rom_enhance_action_points")


@pytest.mark.parametrize("key,constant", [
    ("raw_mom_extraction", "RAW_MOM_EXTRACTION_PROMPT"),
    ("raw_mom_repair", "RAW_MOM_REPAIR_PROMPT"),
])
def test_raw_mom_keys_resolve_to_their_real_constants(key, constant):
    """These had real templates defined but were wired to empty strings."""
    expected = getattr(ai_provider, constant)
    assert expected.strip(), f"{constant} is itself empty"
    assert _get_prompt(key) == expected


def test_no_module_prompt_constant_is_empty():
    """
    A second guard, independent of the registry: catches a constant emptied by
    a future edit even before it is wired up.
    """
    empty = [
        name for name in dir(ai_provider)
        if name.endswith("_PROMPT")
        and isinstance(getattr(ai_provider, name), str)
        and not getattr(ai_provider, name).strip()
    ]
    assert not empty, f"empty prompt constants: {empty}"


def test_customised_prompts_still_take_precedence(monkeypatch):
    """The DB-backed override path must survive the stricter fallback."""
    monkeypatch.setattr(
        "services.prompt_service.get_prompt_sync",
        lambda key: "A customised template long enough to be real.",
    )
    assert _get_prompt("mom") == "A customised template long enough to be real."


def test_blank_customisation_falls_back_to_the_constant(monkeypatch):
    """A blank stored override must not blank the prompt."""
    monkeypatch.setattr("services.prompt_service.get_prompt_sync", lambda key: "   ")
    assert _get_prompt("mom") == ai_provider.MOM_PROMPT
