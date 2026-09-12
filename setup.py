#!/usr/bin/env python3
"""
VoiceSum first-run setup.

Fetches everything the application needs that is too large to live in git, then
verifies it. After this completes the app runs fully offline; network access is
required only while this script is running.

    python setup.py              # everything
    python setup.py --check      # verify an existing install, download nothing
    python setup.py --skip-llm   # skip the Ollama model pull

Why models are not in the repository: the speech model alone is 2.9 GB, which
exceeds GitHub's 100 MB per-file limit and also Git LFS's 2 GB per-file limit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
RUNTIME = BACKEND / "runtime"
MODELS_DIR = RUNTIME / "models"
EMBEDDINGS_DIR = RUNTIME / "embeddings"

# Speech, alignment, diarization and speaker-embedding models, as one archive.
SPEECH_ARCHIVE_ID = "1Vrkf_s32iXrhHmnXDqM_qEsyDTjh4EyB"

# Text embedding model. Pulled from Hugging Face rather than the archive,
# because the archive ships Qwen3-Embedding, which is excluded for this
# deployment. See docs/observations for the provenance record.
EMBEDDING_REPO = "mixedbread-ai/mxbai-embed-large-v1"
EMBEDDING_NAME = "mxbai-embed-large-v1"

# Language model, served locally by Ollama.
LLM_MODEL = "phi4"

REQUIRED_MODELS = ("speech_engine", "audio_context", "ecapa_tdnn", "align_engine")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}    {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def step(msg: str) -> None:
    print(f"\n{msg}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


# ── Checks ─────────────────────────────────────────────────────────────────

def check_python() -> bool:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        fail(f"Python {major}.{minor}; 3.10 or newer is required")
        return False
    ok(f"Python {major}.{minor}")
    return True


def check_ffmpeg() -> bool:
    if shutil.which("ffmpeg"):
        ok("ffmpeg on PATH")
        return True
    fail("ffmpeg not found. Install it and put it on PATH; audio decoding needs it.")
    return False


def check_ollama() -> bool:
    if not shutil.which("ollama"):
        warn("ollama not found. The language model features need it: https://ollama.com")
        return False
    ok("ollama on PATH")
    return True


def models_present() -> list[str]:
    return [m for m in REQUIRED_MODELS if not (MODELS_DIR / m).is_dir()]


def embedding_present() -> bool:
    d = EMBEDDINGS_DIR / EMBEDDING_NAME
    return d.is_dir() and (d / "config.json").is_file()


# ── Downloads ──────────────────────────────────────────────────────────────

def ensure_pip(package: str, import_name: str | None = None) -> bool:
    try:
        __import__(import_name or package)
        return True
    except ImportError:
        print(f"  installing {package} ...")
        r = run([sys.executable, "-m", "pip", "install", "--quiet", package])
        if r.returncode != 0:
            fail(f"could not install {package}")
            return False
        return True


def download_speech_models() -> bool:
    missing = models_present()
    if not missing:
        ok("speech, alignment, diarization and speaker models already present")
        return True

    print(f"  missing: {', '.join(missing)}")
    if not ensure_pip("gdown"):
        return False
    import gdown  # noqa: E402

    archive = RUNTIME / "_models.zip"
    RUNTIME.mkdir(parents=True, exist_ok=True)
    print("  downloading ~3.7 GB, this takes a while ...")
    try:
        gdown.download(id=SPEECH_ARCHIVE_ID, output=str(archive), quiet=False)
    except Exception as exc:
        fail(f"download failed: {exc}")
        return False

    print("  extracting ...")
    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(RUNTIME)
    except Exception as exc:
        fail(f"extract failed: {exc}")
        return False
    finally:
        archive.unlink(missing_ok=True)

    still_missing = models_present()
    if still_missing:
        fail(f"still missing after extract: {', '.join(still_missing)}")
        return False
    ok("speech, alignment, diarization and speaker models installed")
    return True


def download_embedding_model() -> bool:
    if embedding_present():
        ok(f"{EMBEDDING_NAME} already present")
        return True

    if not ensure_pip("huggingface_hub", "huggingface_hub"):
        return False

    # The application forces offline mode at import; this script must not.
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ.pop(var, None)

    from huggingface_hub import snapshot_download  # noqa: E402

    print(f"  downloading {EMBEDDING_REPO} (~640 MB) ...")
    try:
        snapshot_download(
            EMBEDDING_REPO,
            local_dir=str(EMBEDDINGS_DIR / EMBEDDING_NAME),
            ignore_patterns=["*.onnx", "*.onnx_data", "onnx/*", "*.gguf",
                             "openvino/*", "*.msgpack", "*.h5"],
        )
    except Exception as exc:
        fail(f"download failed: {exc}")
        return False

    ok(f"{EMBEDDING_NAME} installed")
    return True


def pull_llm() -> bool:
    if not shutil.which("ollama"):
        warn("skipping language model: ollama not installed")
        return False
    try:
        tags = subprocess.check_output(["ollama", "list"], text=True, timeout=30)
        if LLM_MODEL in tags:
            ok(f"{LLM_MODEL} already pulled")
            return True
    except Exception:
        pass

    print(f"  pulling {LLM_MODEL} (~9 GB) ...")
    r = run(["ollama", "pull", LLM_MODEL])
    if r.returncode != 0:
        fail(f"ollama pull {LLM_MODEL} failed")
        return False
    ok(f"{LLM_MODEL} pulled")
    return True


def install_python_deps() -> bool:
    req = BACKEND / "requirements.txt"
    if not req.is_file():
        fail("backend/requirements.txt not found")
        return False
    print("  installing backend dependencies ...")
    r = run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    if r.returncode != 0:
        fail("dependency install failed; see the output above")
        return False
    ok("backend dependencies installed")
    return True


def install_frontend() -> bool:
    if not shutil.which("npm"):
        warn("npm not found; skipping frontend install")
        return False
    fe = ROOT / "frontend"
    if (fe / "node_modules").is_dir():
        ok("frontend dependencies already installed")
        return True
    print("  installing frontend dependencies ...")
    r = run(["npm", "install", "--no-audit", "--no-fund"], cwd=fe, shell=(os.name == "nt"))
    if r.returncode != 0:
        fail("npm install failed")
        return False
    ok("frontend dependencies installed")
    return True


# ── Verify ─────────────────────────────────────────────────────────────────

def verify() -> bool:
    step("Verifying install")
    good = True

    missing = models_present()
    if missing:
        fail(f"models missing: {', '.join(missing)}")
        good = False
    else:
        ok(f"models present: {', '.join(REQUIRED_MODELS)}")

    if embedding_present():
        ok(f"embedding model present: {EMBEDDING_NAME}")
    else:
        fail(f"embedding model missing: {EMBEDDING_NAME}")
        good = False

    if shutil.which("ffmpeg"):
        ok("ffmpeg on PATH")
    else:
        fail("ffmpeg missing")
        good = False

    if (ROOT / "frontend" / "node_modules").is_dir():
        ok("frontend dependencies present")
    else:
        warn("frontend dependencies not installed (run: cd frontend && npm install)")

    return good


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceSum first-run setup")
    parser.add_argument("--check", action="store_true", help="verify only, download nothing")
    parser.add_argument("--skip-llm", action="store_true", help="skip the Ollama model pull")
    parser.add_argument("--skip-frontend", action="store_true", help="skip npm install")
    args = parser.parse_args()

    print("VoiceSum setup")
    print("=" * 60)

    if args.check:
        return 0 if verify() else 1

    step("Checking prerequisites")
    if not check_python():
        return 1
    check_ffmpeg()
    check_ollama()

    step("Installing Python dependencies")
    install_python_deps()

    step("Downloading speech, alignment, diarization and speaker models")
    download_speech_models()

    step("Downloading text embedding model")
    download_embedding_model()

    if not args.skip_llm:
        step("Pulling the local language model")
        pull_llm()

    if not args.skip_frontend:
        step("Installing frontend dependencies")
        install_frontend()

    good = verify()

    print("\n" + "=" * 60)
    if good:
        print(f"{GREEN}Setup complete.{RESET}\n")
        print("Start the backend:")
        print("  cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8000")
        print("\nStart the frontend, in a second terminal:")
        print("  cd frontend && npm run dev")
        print("\nThen open http://localhost:8080/signup")
        print(f"{DIM}The first account created becomes the administrator.{RESET}")
    else:
        print(f"{RED}Setup incomplete.{RESET} Fix the items marked FAIL and re-run.")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
