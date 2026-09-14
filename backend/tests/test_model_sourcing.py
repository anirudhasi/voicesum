"""
No model from a Chinese developer may be selected by default (project constraint).

Found while documenting features: Stage 2 training fell back to 'qwen2.5:7b'
when the model server listed no models, and Stage 3 labelled variants
'local-qwen'. Settings and the interface had already been changed; these
defaults had not.
"""
import re
from pathlib import Path

from config import DEFAULT_OLLAMA_MODEL_PRIORITY, settings

BACKEND = Path(__file__).resolve().parent.parent
RESTRICTED = re.compile(r"['\"](qwen|deepseek)[^'\"]*['\"]", re.IGNORECASE)


def test_default_priority_excludes_restricted_models():
    for source in (DEFAULT_OLLAMA_MODEL_PRIORITY, settings.EMBEDDING_MODEL):
        assert not re.search(r"qwen|deepseek", source, re.IGNORECASE), source


def test_training_services_hard_code_no_restricted_model():
    offenders = []
    for p in (BACKEND / "services" / "training").glob("*.py"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "warnings" in code:  # compatibility notes listing architectures
                continue
            if RESTRICTED.search(code):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
