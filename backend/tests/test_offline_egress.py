"""
W4.6 — the application must make no outbound connection at runtime.

These tests pin the fixes for the residual outbound references found in the
parameter audit. They are deliberately source-level where the dependency is
absent from the light developer environment, so the guarantee is still checked
on every run rather than only where the full stack is installed.
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

# Hosts that must never appear in code that runs at runtime.
FORBIDDEN_HOSTS = [
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "lovable.dev",
]

# Loopback is the only permitted destination (the local model server).
ALLOWED_URL_PATTERN = re.compile(
    r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?", re.IGNORECASE
)


def _sources(root: Path, suffixes, skip_parts=()):
    for p in root.rglob("*"):
        if p.suffix not in suffixes or not p.is_file():
            continue
        if any(part in p.parts for part in skip_parts):
            continue
        yield p


# ── ChromaDB telemetry ─────────────────────────────────────────────────────

def test_chroma_client_disables_telemetry():
    """
    ChromaDB posts anonymised usage events to a third-party endpoint by
    default. On a secured installation an outbound attempt is a finding even
    when it fails.
    """
    src = (BACKEND / "services" / "vector_store.py").read_text(encoding="utf-8")
    assert "anonymized_telemetry=False" in src, (
        "PersistentClient must be constructed with telemetry disabled"
    )


def test_chroma_telemetry_setting_precedes_fallback():
    """The plain fallback client must only be reachable after the guarded attempt."""
    src = (BACKEND / "services" / "vector_store.py").read_text(encoding="utf-8")
    guarded = src.index("anonymized_telemetry=False")
    fallback = src.rindex("chromadb.PersistentClient(path=target_path)")
    assert guarded < fallback, "telemetry-disabled construction must be attempted first"


def test_chroma_settings_accepts_the_flag():
    """
    If chromadb is installed, confirm the setting name is real. Guards against
    the upstream option being renamed without anyone noticing.
    """
    chroma_config = pytest.importorskip(
        "chromadb.config", reason="chromadb not installed in this environment"
    )
    s = chroma_config.Settings(anonymized_telemetry=False)
    assert s.anonymized_telemetry is False


# ── Backend source must not reference external hosts ───────────────────────

def test_backend_has_no_forbidden_hosts():
    offenders = []
    # Third-party packages are out of scope here: the virtual environment
    # lives inside backend/ and is not application source.
    skip_parts = ("tests", "runtime", "checkpoints", ".venv", "venv", "site-packages")
    for p in _sources(BACKEND, {".py"}, skip_parts=skip_parts):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for host in FORBIDDEN_HOSTS:
            if host in text:
                offenders.append(f"{p.relative_to(REPO)} -> {host}")
    assert not offenders, (
        "Backend source references external hosts:\n  " + "\n  ".join(offenders)
    )


# ── Frontend source must not reference external hosts ──────────────────────

def test_frontend_has_no_forbidden_hosts():
    frontend = REPO / "frontend" / "src"
    if not frontend.is_dir():
        pytest.skip("frontend/src not present")
    offenders = []
    for p in _sources(frontend, {".css", ".ts", ".tsx", ".js", ".jsx"}):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for host in FORBIDDEN_HOSTS:
            if host in text:
                offenders.append(f"{p.relative_to(REPO)} -> {host}")
    assert not offenders, (
        "Frontend source references external hosts:\n  " + "\n  ".join(offenders)
    )


def test_frontend_index_html_has_no_forbidden_hosts():
    index = REPO / "frontend" / "index.html"
    if not index.is_file():
        pytest.skip("frontend/index.html not present")
    text = index.read_text(encoding="utf-8", errors="ignore")
    offenders = [h for h in FORBIDDEN_HOSTS if h in text]
    assert not offenders, f"frontend/index.html references {offenders}"


def test_no_render_blocking_font_import():
    """
    A CSS @import of a remote stylesheet is render-blocking. Offline it delays
    first paint by the full connection timeout on every launch.
    """
    css = REPO / "frontend" / "src" / "index.css"
    if not css.is_file():
        pytest.skip("frontend/src/index.css not present")
    head = css.read_text(encoding="utf-8", errors="ignore")
    remote_imports = re.findall(r"@import\s+url\(\s*['\"]?(https?://[^'\")]+)", head)
    assert not remote_imports, f"remote @import found: {remote_imports}"


# ── Self-hosted fonts ──────────────────────────────────────────────────────

FONT_FAMILIES = {
    "Inter": 6,                  # 300-800
    "JetBrains Mono": 3,         # 400, 500, 600
    "Caveat": 3,                 # 400, 600, 700
    "Kalam": 3,                  # 300, 400, 700
    "Architects Daughter": 1,
    "Patrick Hand": 1,
}


def test_font_stylesheet_is_present():
    assert (REPO / "frontend" / "public" / "fonts.css").is_file(), (
        "public/fonts.css missing; run the font vendoring script"
    )


def test_every_used_family_is_vendored():
    """Each family referenced by tailwind.config or the app must be bundled."""
    css = (REPO / "frontend" / "public" / "fonts.css").read_text(encoding="utf-8")
    missing = [f for f in FONT_FAMILIES if f"font-family: '{f}'" not in css]
    assert not missing, f"families not vendored: {missing}"


def test_all_font_weights_present_for_each_family():
    css = (REPO / "frontend" / "public" / "fonts.css").read_text(encoding="utf-8")
    faces = re.findall(r"font-family: '([^']+)';\s*\n\s*font-style: normal;\s*\n\s*font-weight: (\d+)", css)
    by_family = {}
    for fam, weight in faces:
        by_family.setdefault(fam, set()).add(weight)
    shortfalls = {
        fam: sorted(by_family.get(fam, set()))
        for fam, want in FONT_FAMILIES.items()
        if len(by_family.get(fam, set())) < want
    }
    assert not shortfalls, f"families missing weights: {shortfalls}"


def test_font_files_exist_and_are_non_empty():
    """Every src url in fonts.css must resolve to a real file on disk."""
    public = REPO / "frontend" / "public"
    css = (public / "fonts.css").read_text(encoding="utf-8")
    refs = re.findall(r"src:\s*url\('\./([^']+)'\)", css)
    assert refs, "fonts.css declares no font sources"
    broken = []
    for rel in refs:
        f = public / rel
        if not f.is_file() or f.stat().st_size == 0:
            broken.append(rel)
    assert not broken, f"font files missing or empty: {broken}"


def test_font_urls_are_relative():
    """
    The Electron build uses base "./" and loads over file://, where an absolute
    URL resolves against the filesystem root.
    """
    css = (REPO / "frontend" / "public" / "fonts.css").read_text(encoding="utf-8")
    absolute = re.findall(r"src:\s*url\('(/[^']*|https?://[^']*)'\)", css)
    assert not absolute, f"non-relative font urls: {absolute}"


def test_index_html_links_fonts_relatively():
    index = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'href="./fonts.css"' in index, (
        'index.html must link fonts with a relative href for the file:// build'
    )


def test_font_license_is_bundled():
    """The families are OFL; redistribution requires the license to travel."""
    ofl = REPO / "frontend" / "public" / "fonts" / "OFL.txt"
    assert ofl.is_file() and ofl.stat().st_size > 1000, "OFL.txt missing or truncated"


def test_dashboard_console_uses_system_fonts():
    """The backend console must not depend on a bundled or remote family."""
    src = (BACKEND / "routers" / "dashboard_router.py").read_text(encoding="utf-8")
    for family in ("'Outfit'", "'JetBrains Mono'"):
        assert family not in src, (
            f"dashboard still references {family}; it has no font mount"
        )


# ── Interactive API docs pull from public CDNs ─────────────────────────────

def test_cdn_backed_api_docs_are_off_by_default():
    """
    /docs and /redoc load Swagger UI and ReDoc from cdn.jsdelivr.net and fonts
    from fonts.googleapis.com. The machine-readable schema has no external
    references and stays available.
    """
    from config import settings
    from main import app

    assert settings.ENABLE_API_DOCS is False
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/docs" not in paths and "/redoc" not in paths
    assert "/openapi.json" in paths
