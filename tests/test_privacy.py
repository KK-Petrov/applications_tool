"""C-0-03: no personal data or secrets in the repository; data folder and token files are outside Git.

"Tracked" here means everything Git would commit: tracked files plus untracked files that are not
ignored (``git ls-files --cached --others --exclude-standard``), so the check also covers work that
has not been committed yet.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath

import pytest

from paths import FIXTURES, REPO_ROOT

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
TOKEN_PATTERNS = {
    "Google OAuth client secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "Google access token": re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    "Google refresh token": re.compile(r"1//0[A-Za-z0-9_-]{30,}"),
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "client_secret value": re.compile(r"\"client_secret\"\s*:\s*\"[^\"]+\""),
    "refresh_token value": re.compile(r"\"refresh_token\"\s*:\s*\"[^\"]+\""),
}


DATA_FILE_NAMES = {"settings.yaml", "biography.yaml", "calendar_map.json", "digest_state.json"}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _committable_files() -> list[str]:
    res = _git("ls-files", "--cached", "--others", "--exclude-standard")
    assert res.returncode == 0, f"git is required for the privacy check: {res.stderr}"
    return [line for line in res.stdout.splitlines() if line and (REPO_ROOT / line).is_file()]


def _text(path: str) -> str | None:
    raw = (REPO_ROOT / path).read_bytes()
    if b"\0" in raw[:4096]:
        return None  # binary
    return raw.decode("utf-8", errors="replace")


FILES = _committable_files()


def test_there_are_files_to_scan() -> None:
    assert "CLAUDE.md" in FILES and "config/project.yaml" in FILES


def test_no_email_addresses_except_example_domains() -> None:
    found = []
    for path in FILES:
        text = _text(path)
        if text is None:
            continue
        for match in EMAIL.finditer(text):
            if match.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
                found.append(f"{path}: {match.group(0)}")
    assert found == [], "Email addresses outside example.com/example.org:\n" + "\n".join(found)


def test_no_tokens_or_secrets() -> None:
    found = []
    for path in FILES:
        text = _text(path)
        if text is None:
            continue
        for name, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                found.append(f"{path}: {name}")
    assert found == [], "Possible secrets:\n" + "\n".join(found)


def test_no_data_secret_or_token_paths() -> None:
    bad = []
    for path in FILES:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if "data" in parts[:-1] and not path.startswith("tests/fixtures/data/"):
            bad.append(path)
        if ".secrets" in parts or name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            bad.append(path)
        if re.fullmatch(r"(token|client_secret|credentials).*\.json", name):
            bad.append(path)
        in_fixtures_or_seed = path.startswith(("tests/fixtures/", "seed/"))
        if not in_fixtures_or_seed and (name in DATA_FILE_NAMES or name.endswith(".eval.json")):
            bad.append(path)  # a copied data file (it may hold personal data without an email address)
        if not in_fixtures_or_seed and ("professors" in parts[:-1] or "programmes" in parts[:-1]):
            bad.append(path)
    assert bad == [], "Files that must stay outside Git:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "path",
    ["data/settings.yaml", ".secrets/token.json", ".secrets/client_secret.json", "reports/eval.json", ".env"],
)
def test_gitignore_covers(path: str) -> None:
    assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, f"{path} is not ignored"


@pytest.mark.parametrize(
    "path",
    [
        "tests/fixtures/data/settings.yaml",
        "tests/fixtures/data/programmes/princeton-mfin.yaml",
        ".env.example",
    ],
)
def test_gitignore_keeps_fixtures_and_examples(path: str) -> None:
    assert _git("check-ignore", "-q", "--no-index", path).returncode != 0, f"{path} is ignored"


def test_configured_data_dir_is_outside_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    from hub.config import load_config

    monkeypatch.delenv("HUB_DATA_DIR", raising=False)
    data_dir = load_config().storage.data_dir.resolve()
    assert REPO_ROOT.resolve() not in [data_dir, *data_dir.parents]


def test_fixture_people_use_example_domains() -> None:
    for path in FIXTURES.rglob("*"):
        if path.is_file() and path.suffix in {".yaml", ".md", ".json", ".csv"}:
            for match in EMAIL.finditer(path.read_text(encoding="utf-8")):
                assert match.group(1).lower() in ALLOWED_EMAIL_DOMAINS, f"{path}: {match.group(0)}"


def test_fixtures_are_committable() -> None:
    fixture_files = {str(p.relative_to(REPO_ROOT).as_posix()) for p in FIXTURES.rglob("*") if p.is_file()}
    assert fixture_files <= set(FILES), "Some fixtures would not be committed"
