"""No code path can send email to anyone but the owner (CLAUDE.md rule 9; docs/05 §4; C-3-06).

A static scan of ``src/``: only ``hub/google/digest.py`` may call the Gmail API, import an email-building or
mail-sending module, or name a recipient header; its send function takes no recipient argument, and the only
value its To (and From) header is ever given is the owner address from settings. The behaviour is checked
against the fake Gmail in tests/test_digest.py.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from hub.google import digest

SRC = Path(__file__).resolve().parents[1] / "src" / "hub"
DIGEST = SRC / "google" / "digest.py"
MAIL_MODULES = ("email", "smtplib", "imaplib", "poplib")
RECIPIENT_HEADERS = {"to", "cc", "bcc", "reply-to", "resent-to"}


def _modules() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"), str(p))) for p in sorted(SRC.rglob("*.py"))]


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_only_the_digest_imports_mail_modules() -> None:
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] in MAIL_MODULES for n in names) and path != DIGEST:
                offenders.append(f"{_rel(path)}:{node.lineno}")
    assert offenders == []


def test_only_the_digest_calls_the_gmail_api() -> None:
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            gmail = isinstance(node, ast.Constant) and node.value == "gmail"
            send = (
                isinstance(node, ast.Attribute)
                and node.attr in ("messages", "send")
                and _is_messages_send(node)
            )
            if (gmail or send) and path not in (DIGEST, SRC / "google" / "auth.py"):
                offenders.append(f"{_rel(path)}:{node.lineno}")
    # auth.py only builds the client ("gmail", "v1"); it never sends
    assert offenders == []
    auth_src = (SRC / "google" / "auth.py").read_text(encoding="utf-8")
    assert ".messages(" not in auth_src and ".send(" not in auth_src


def _is_messages_send(node: ast.Attribute) -> bool:
    """``….messages().send(…)``: the Gmail send chain."""
    if node.attr != "send":
        return False
    call = node.value
    return (
        isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "messages"
    )


def test_no_module_names_a_recipient_header_but_the_digest() -> None:
    offenders = []
    for path, tree in _modules():
        if path == DIGEST:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                key = node.slice.value
                if isinstance(key, str) and key.casefold() in RECIPIENT_HEADERS:
                    offenders.append(f"{_rel(path)}:{node.lineno}")
    assert offenders == []


def test_the_send_function_has_no_recipient_argument() -> None:
    params = inspect.signature(digest.send).parameters
    assert not any(p in params for p in ("to", "recipient", "recipients", "address", "email", "cc", "bcc"))


def test_the_digest_sets_to_and_from_only_from_the_owner_address() -> None:
    """Every assignment ``msg["To"] = …`` / ``msg["From"] = …`` in digest.py takes the owner address, and no
    other recipient header is set."""
    tree = ast.parse(DIGEST.read_text(encoding="utf-8"))
    headers: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    headers.setdefault(str(target.slice.value).casefold(), []).append(ast.unparse(node.value))
    assert set(headers) & RECIPIENT_HEADERS == {"to"}
    assert headers["to"] == ["owner"] and headers["from"] == ["owner"]
    source = DIGEST.read_text(encoding="utf-8")
    assert "owner = settings.owner_email" in source  # the only source of ``owner``
    assert source.count("owner = ") == 1
