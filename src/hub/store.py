"""Reading and writing the user's data folder (docs/01 §1, §9).

- Every load validates. An invalid file raises ``DataError(path, field, message)``, which names the file
  (relative to ``data_dir``) and the field; bulk loaders collect these so other files still load.
- YAML goes through ruamel round-trip mode. A save merges the new values into the file's existing tree,
  so the user's comments and key order survive, while keys and list items the model no longer has are
  removed.
- Writes are atomic (``<file>.tmp`` then ``os.replace``), are validated before anything touches the
  disk, and are skipped entirely when nothing changed.
- Loads are cached by file content (a hash of the exact bytes): an unchanged file is not parsed again,
  a changed one always is, and callers always get their own deep copy (DECISIONS #27).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString

from hub.config import Config
from hub.models import Biography, Professor, Programme, Settings
from hub.models.course import Courses, TranscriptCourse
from hub.models.sync import CalendarMap, CalendarMapEntry, DigestState, LastSync

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

SETTINGS_FILE = "settings.yaml"
BIOGRAPHY_FILE = "biography.yaml"
PROGRAMMES_DIR = "programmes"
PROFESSORS_DIR = "professors"
COURSES_FILE = "courses.yaml"
CALENDAR_MAP_FILE = "sync/calendar_map.json"  # docs/01 §8
DIGEST_STATE_FILE = "sync/digest_state.json"
LAST_SYNC_FILE = "sync/last_sync.json"  # DECISIONS #106 Q5
SOURCES_DIR = "sources"


class DataError(Exception):
    """A data file that cannot be used: ``path`` relative to data_dir, ``field`` (or None), ``message``."""

    def __init__(self, path: str, field: str | None, message: str) -> None:
        super().__init__(path, field, message)
        self.path = path
        self.field = field
        self.message = message

    def __str__(self) -> str:
        where = f"{self.path} [{self.field}]" if self.field else self.path  # ASCII: safe on any console
        return f"{where}: {self.message}"


@dataclass
class Snapshot:
    """Everything loaded from data_dir for one request, plus the files that could not be loaded."""

    settings: Settings | None
    programmes: list[Programme]
    professors: list[Professor]
    biography: Biography
    errors: list[DataError] = field(default_factory=list)
    courses: list[TranscriptCourse] = field(default_factory=list)  # active courses only (not deleted)
    all_course_ids: set[str] = field(default_factory=set)  # deleted ones too: an id is never reused (#94)


def format_loc(loc: tuple[int | str, ...]) -> str | None:
    """Pydantic location → ``deadlines[0].tz`` / ``letters.princeton-mfin.status``."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or None


def _error_from_validation(rel: str, exc: ValidationError) -> DataError:
    errors = exc.errors()
    first = errors[0]
    message = first["msg"].removeprefix("Value error, ")
    if len(errors) > 1:
        message += f" (and {len(errors) - 1} more problem{'s' if len(errors) > 2 else ''})"
    return DataError(rel, format_loc(tuple(first["loc"])), message)


def _dump(model: BaseModel) -> dict[str, Any]:
    """Serialise a model for saving.

    Includes every field that was in the file or set in code, plus every field whose value now differs
    from its default, so in-place changes to a defaulted list, dict or nested model are kept. Fields
    left out are deleted from the file by the merge.
    """
    return _prune(model, model.model_dump(mode="python"))


def _prune(model: BaseModel, dumped: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        if name not in model.model_fields_set and value == info.get_default(call_default_factory=True):
            continue
        out[name] = _prune_value(value, dumped[name])
    return out


def _prune_value(value: Any, dumped: Any) -> Any:
    if isinstance(value, BaseModel):
        return _prune(value, dumped)
    if isinstance(value, list):
        return [_prune_value(v, d) for v, d in zip(value, dumped, strict=True)]
    if isinstance(value, dict):
        return {k: _prune_value(v, dumped[k]) for k, v in value.items()}
    return dumped


def _plain(value: Any) -> Any:
    """Model dump → plain YAML-ready values (enums become their string values)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _to_yaml(value: Any) -> Any:
    """Plain value → ruamel node (block style); multi-line text becomes a literal block."""
    if isinstance(value, dict):
        return CommentedMap((k, _to_yaml(v)) for k, v in value.items())
    if isinstance(value, list):
        return CommentedSeq(_to_yaml(v) for v in value)
    if isinstance(value, str):
        if all(ch == "\n" or ch.isprintable() for ch in value):
            return LiteralScalarString(value) if "\n" in value else value
        # \r,  , NBSP, tabs and other control characters: a double-quoted scalar with escapes is the
        # only style that round-trips them exactly. Wrapping explicitly also stops ruamel from forcing
        # the new text into the old node's literal-block style.
        return DoubleQuotedScalarString(value)
    return value


def _same(current: Any, new: Any) -> bool:
    if isinstance(current, bool) or isinstance(new, bool):
        return type(current) is type(new) and current == new
    if isinstance(current, datetime) is not isinstance(new, datetime):
        return False
    if isinstance(current, datetime):  # same instant written with another offset is still a change
        return current == new and current.utcoffset() == new.utcoffset()
    if isinstance(current, date) is not isinstance(new, date):
        return False
    return current == new


def to_block_style(node: Any) -> None:
    """Turn every collection in a ruamel tree into block style, in place (DECISIONS #19).

    ruamel cannot keep the layout of multi-line flow mappings, but block style round-trips line for
    line, so a one-field change stays a one-line diff. Empty collections are still written ``[]``/``{}``.
    """
    if isinstance(node, CommentedMap | CommentedSeq):
        node.fa.set_block_style()
        for child in node.values() if isinstance(node, CommentedMap) else node:
            to_block_style(child)


def _merge_map(target: CommentedMap, source: dict[str, Any]) -> bool:
    """Make ``target`` equal ``source`` in place, keeping comments; returns whether anything changed."""
    changed = False
    for key in [k for k in target if k not in source]:
        del target[key]
        changed = True
    for key, value in source.items():
        if key not in target:
            target[key] = _to_yaml(value)
            changed = True
            continue
        current = target[key]
        if isinstance(current, CommentedMap) and isinstance(value, dict):
            changed |= _merge_map(current, value)
        elif isinstance(current, CommentedSeq) and isinstance(value, list):
            changed |= _merge_seq(current, value)
        elif not _same(current, value):
            target[key] = _to_yaml(value)
            changed = True
    return changed


IDENTITY_KEYS = ("id", "order", "url")  # a list item's identity: id, a step's order, a source's url


def _identity_key(items: list[Any]) -> str | None:
    """The first identity key every item has, with values unique on that side; None → match by position."""
    for key in IDENTITY_KEYS:
        if all(isinstance(i, dict) and i.get(key) is not None for i in items):
            values = [i[key] for i in items]
            if len(set(values)) == len(values):
                return key
    return None


def _merge_seq(target: CommentedSeq, source: list[Any]) -> bool:
    """Make ``target`` equal ``source``. Items are matched by identity (id, step order or source url) so
    comments stay with their item when another is removed (DECISIONS #33); otherwise by position."""
    key = _identity_key([*target]) if target and source else None
    keyed = key is not None and _identity_key(source) == key

    def _item_id(item: Any) -> Any:
        return item.get(key) if isinstance(item, dict) else None

    changed = False
    if keyed:
        wanted = {_item_id(s): s for s in source}
        for index in range(len(target) - 1, -1, -1):  # drop removed items (CommentedSeq shifts comments)
            if _item_id(target[index]) not in wanted:
                del target[index]
                changed = True
        existing = {_item_id(t): t for t in target}
        for item in source:
            node = existing.get(_item_id(item))
            if node is None:
                target.append(_to_yaml(item))
                changed = True
            else:
                changed |= _merge_map(node, item)
        if [_item_id(t) for t in target] != [_item_id(s) for s in source]:  # reordered
            order = {_item_id(s): n for n, s in enumerate(source)}
            items = sorted(target, key=lambda t: order[_item_id(t)])
            del target[:]
            target.extend(items)
            changed = True
        return changed
    while len(target) > len(source):
        del target[len(target) - 1]
        changed = True
    for index, item in enumerate(source):
        if index >= len(target):
            target.append(_to_yaml(item))
            changed = True
            continue
        current = target[index]
        if isinstance(current, CommentedMap) and isinstance(item, dict):
            changed |= _merge_map(current, item)
        elif isinstance(current, CommentedSeq) and isinstance(item, list):
            changed |= _merge_seq(current, item)
        elif not _same(current, item):
            target[index] = _to_yaml(item)
            changed = True
    return changed


def _yaml() -> YAML:
    """A round-trip YAML instance matching the layout of the seed files."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = NO_WRAP  # never wrap a scalar, however long
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.default_flow_style = False  # new nodes are block style (DECISIONS #19)
    # never anchors or aliases (&id001 / *id001): a shared date made them, and a hand edit then broke or
    # changed every copy (data-integrity review, #67)
    yaml.representer.ignore_aliases = lambda *_: True
    yaml.representer.add_representer(  # write "null" explicitly, as the seed files do
        type(None), lambda rep, _: rep.represent_scalar("tag:yaml.org,2002:null", "null")
    )
    yaml.representer.add_representer(datetime, _represent_timestamp)
    return yaml


NO_WRAP = 2**30


def _represent_timestamp(representer: Any, value: datetime) -> Any:
    """Stored timestamps as unquoted ISO 8601 with ``T`` and the UTC offset (DECISIONS #18)."""
    if value.tzinfo is None:
        raise ValueError("stored timestamps must carry a UTC offset")
    return representer.represent_scalar("tag:yaml.org,2002:timestamp", value.isoformat())


_CREATE_LOCK = threading.Lock()  # serialises "does it exist? then write" for new files


class Store:
    """All reads and writes of the configured data_dir go through here (rule 12)."""

    def __init__(self, data_dir: Path, config: Config) -> None:
        self.data_dir = Path(data_dir)
        self.config = config
        # (resolved path, model class) -> (sha256 of the file's bytes, validated model)
        self._cache: dict[tuple[str, type[BaseModel]], tuple[bytes, BaseModel]] = {}

    @classmethod
    def from_config(cls, config: Config) -> Store:
        return cls(config.storage.data_dir, config)

    # ---- low level -------------------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.data_dir.resolve()).as_posix()
        except ValueError:
            return path.name

    def _read_bytes(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise DataError(self._rel(path), None, "file not found") from None
        except OSError as exc:
            raise DataError(self._rel(path), None, f"cannot be read ({exc.strerror or exc})") from None

    def _parse_tree(self, rel: str, raw: bytes) -> CommentedMap:
        try:
            # universal newlines, exactly as read_text() does (the file's CRLF is restored on save)
            text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise DataError(rel, None, "cannot be read: the file is not UTF-8 text") from None
        try:
            tree = _yaml().load(text)
        except MarkedYAMLError as exc:
            line = exc.problem_mark.line + 1 if exc.problem_mark else "?"
            raise DataError(rel, None, f"YAML syntax error at line {line}: {exc.problem}") from None
        except YAMLError as exc:
            raise DataError(rel, None, f"YAML syntax error: {exc}") from None
        if not isinstance(tree, CommentedMap):
            raise DataError(rel, None, "must contain a YAML mapping (key: value lines)")
        return tree

    def _read_tree(self, path: Path) -> CommentedMap:
        return self._parse_tree(self._rel(path), self._read_bytes(path))

    def _validate(self, rel: str, model_cls: type[M], data: Any) -> M:
        try:
            return model_cls.model_validate(data)
        except ValidationError as exc:
            raise _error_from_validation(rel, exc) from None

    def _load(self, path: Path, model_cls: type[M]) -> M:
        """Read and validate one file; an unchanged file (same bytes) is served from the cache."""
        raw = self._read_bytes(path)
        key = (str(path.resolve()), model_cls)
        digest = hashlib.sha256(raw).digest()
        hit = self._cache.get(key)
        if hit is not None and hit[0] == digest:
            return hit[1].model_copy(deep=True)  # type: ignore[return-value]
        rel = self._rel(path)
        model = self._validate(rel, model_cls, self._parse_tree(rel, raw))  # parsed from the hashed bytes
        self._cache[key] = (digest, model.model_copy(deep=True))
        return model

    def _save(self, path: Path, model: BaseModel) -> bool:
        """Validate, merge into the existing file, write atomically. Returns False if nothing changed."""
        rel = self._rel(path)
        data = _plain(_dump(model))
        tree = self._read_tree(path) if path.exists() else None
        crlf = path.exists() and b"\r\n" in path.read_bytes()  # keep the file's own line endings
        if tree is None:
            tree = _to_yaml(data)
            changed = True
        else:
            changed = _merge_map(tree, data)
        to_block_style(tree)  # a file still in flow style is reformatted once, on its first real save
        self._validate(rel, type(model), tree)  # a failed validation never overwrites a file
        if not changed:
            return False
        buffer = io.StringIO()
        _yaml().dump(tree, buffer)
        text = buffer.getvalue()
        try:  # the text actually written must load and validate, or nothing is written
            reparsed = _yaml().load(text)
        except YAMLError as exc:
            raise DataError(rel, None, f"could not be saved safely; nothing was written ({exc})") from None
        self._validate(rel, type(model), reparsed)
        if crlf:
            text = text.replace("\n", "\r\n")
        self.write_bytes_atomic(path, text.encode("utf-8"))
        log.info("saved %s", rel)
        return True

    def write_bytes_atomic(self, path: Path, content: bytes) -> None:
        """Write ``<file>.tmp``, flush it to disk, then ``os.replace`` it over ``path``.

        A file that stays locked or read-only after the retries raises a ``DataError`` naming it; the
        original file is untouched (DECISIONS #33).
        """
        try:
            self._write_bytes_atomic(path, content)
        except OSError as exc:
            reason = (
                "it is locked (OneDrive or another program) or read-only"
                if isinstance(exc, PermissionError)
                else (exc.strerror or str(exc))
            )
            raise DataError(self._rel(path), None, f"could not be written: {reason}. Try again.") from None

    def _write_bytes_atomic(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            retries = self.config.storage.write_retries
            for attempt in range(1, retries + 1):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:  # OneDrive or an editor may hold the file for a moment
                    if attempt == retries:
                        raise
                    time.sleep(self.config.storage.write_retry_delay_s * attempt)
        finally:
            tmp.unlink(missing_ok=True)

    def _load_folder(self, folder: str, loader: Any) -> tuple[list[Any], list[DataError]]:
        items, errors = [], []
        directory = self.data_dir / folder
        if not directory.is_dir():
            return [], []
        for path in sorted(directory.glob("*.yaml")):
            try:
                items.append(loader(path))
            except DataError as err:
                log.warning("invalid data file: %s", err)
                errors.append(err)
        return items, errors

    # ---- settings --------------------------------------------------------------------------------

    def load_settings(self) -> Settings:
        path = self.data_dir / SETTINGS_FILE
        if not path.exists():
            raise DataError(SETTINGS_FILE, None, "is missing; run `hub init-data` to create it")
        settings = self._load(path, Settings)
        self._check_display_tz(settings)
        return settings

    def _check_display_tz(self, settings: Settings) -> None:
        if settings.display_tz not in self.config.display_zone_ids:
            allowed = ", ".join(self.config.display_zone_ids)
            raise DataError(SETTINGS_FILE, "display_tz", f"must be one of the display zones: {allowed}")

    def save_settings(self, settings: Settings) -> bool:
        self._check_display_tz(settings)
        return self._save(self.data_dir / SETTINGS_FILE, settings)

    # ---- programmes ------------------------------------------------------------------------------

    def load_programme(self, path: Path) -> Programme:
        programme = self._load(path, Programme)
        if programme.id != path.stem:
            raise DataError(
                self._rel(path), "id", f"id {programme.id!r} does not match the file name {path.name}"
            )
        return programme

    def load_programmes(self) -> tuple[list[Programme], list[DataError]]:
        return self._load_folder(PROGRAMMES_DIR, self.load_programme)

    def save_programme(self, programme: Programme) -> bool:
        return self._save(self.data_dir / PROGRAMMES_DIR / f"{programme.id}.yaml", programme)

    def create_programme(self, programme: Programme) -> None:
        """Write a new programme file (a researched programme), never over an existing one."""
        self._create(self.data_dir / PROGRAMMES_DIR, programme.id, programme)

    def programme_digest(self, programme_id: str) -> str:
        """sha256 of the programme file's exact bytes: re-research refuses to save over a file that
        changed since its comparison was shown."""
        return hashlib.sha256(
            self._read_bytes(self.data_dir / PROGRAMMES_DIR / f"{programme_id}.yaml")
        ).hexdigest()

    # ---- professors ------------------------------------------------------------------------------

    def load_professor(self, path: Path) -> Professor:
        professor = self._load(path, Professor)
        if professor.id != path.stem:
            raise DataError(
                self._rel(path), "id", f"id {professor.id!r} does not match the file name {path.name}"
            )
        return professor

    def load_professors(self) -> tuple[list[Professor], list[DataError]]:
        return self._load_folder(PROFESSORS_DIR, self.load_professor)

    def save_professor(self, professor: Professor) -> bool:
        return self._save(self.data_dir / PROFESSORS_DIR / f"{professor.id}.yaml", professor)

    def create_professor(self, professor: Professor) -> None:
        """Write a new professor file, never over an existing one (Add and CSV import)."""
        self._create(self.data_dir / PROFESSORS_DIR, professor.id, professor)

    def _create(self, folder: Path, item_id: str, model: BaseModel) -> None:
        """Write ``<folder>/<item_id>.yaml`` only if no file of that name exists.

        The check ignores case, because Windows treats ``Lee.yaml`` and ``lee.yaml`` as one file, and it
        runs under a lock with the write, so two concurrent creates cannot pick the same file.
        """
        with _CREATE_LOCK:
            name = f"{item_id}.yaml".casefold()
            existing = [p for p in folder.glob("*") if p.name.casefold() == name] if folder.is_dir() else []
            if existing:
                raise DataError(
                    self._rel(existing[0]),
                    None,
                    "already exists; nothing was written",
                )
            self._save(folder / f"{item_id}.yaml", model)

    # ---- sync state (docs/01 §8; Phase 3) ---------------------------------------------------------------

    def _write_json(self, rel: str, model: BaseModel) -> None:
        path = self.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        self.write_bytes_atomic(path, (model.model_dump_json(indent=2) + "\n").encode("utf-8"))

    def _read_json(self, rel: str, model_cls: type[M]) -> M | None:
        """The file as ``model_cls``, None when missing; a DataError naming the file when unreadable."""
        path = self.data_dir / rel
        if not path.exists():
            return None
        try:
            return model_cls.model_validate_json(self._read_bytes(path))
        except ValidationError as exc:
            raise _error_from_validation(rel, exc) from None

    def load_calendar_map(self) -> dict[str, CalendarMapEntry] | None:
        """Reminder id → Google event, or None when there is no map (sync rebuilds it from Google)."""
        found = self._read_json(CALENDAR_MAP_FILE, CalendarMap)
        return None if found is None else dict(found.root)

    def save_calendar_map(self, mapping: dict[str, CalendarMapEntry]) -> None:
        """Written atomically after each successful Google call, so a crash never causes duplicates."""
        self._write_json(CALENDAR_MAP_FILE, CalendarMap(dict(sorted(mapping.items()))))

    def load_last_sync(self) -> LastSync | None:
        return self._read_json(LAST_SYNC_FILE, LastSync)

    def save_last_sync(self, last: LastSync) -> None:
        self._write_json(LAST_SYNC_FILE, last)

    def load_digest_state(self) -> DigestState:
        return self._read_json(DIGEST_STATE_FILE, DigestState) or DigestState()

    def save_digest_state(self, state: DigestState) -> None:
        self._write_json(DIGEST_STATE_FILE, state)

    # ---- biography -------------------------------------------------------------------------------

    def load_biography(self) -> Biography:
        path = self.data_dir / BIOGRAPHY_FILE
        return self._load(path, Biography) if path.exists() else Biography()

    def save_biography(self, biography: Biography) -> bool:
        return self._save(self.data_dir / BIOGRAPHY_FILE, biography)

    # ---- courses and imported documents (docs/01 §10) -------------------------------------------------

    def load_courses(self) -> Courses:
        path = self.data_dir / COURSES_FILE
        return self._load(path, Courses) if path.exists() else Courses()

    def courses_and_professors_digest(self) -> str:
        """sha256 over courses.yaml and every professors/*.yaml (names and exact bytes): the merge page
        refuses a submit when any of them changed since it was shown (batch merge, DECISIONS #101)."""
        return self._digest([self.data_dir / COURSES_FILE, *self._professor_paths()])

    def load_with_digest(self, digest: Callable[[], str]) -> tuple[Snapshot, str, bool]:
        """Everything loaded, with ``digest()`` taken before and after: (snapshot, digest before, unchanged).

        A page embeds the "before" digest, so a change while it loads makes its submit refused; a submit is
        accepted only when the posted digest equals both, so it never acts on files newer than the check
        (data-integrity Phase 3 r1 F6, DECISIONS #105)."""
        before = digest()
        snapshot = self.load_all()
        return snapshot, before, digest() == before

    def professors_digest(self) -> str:
        """sha256 over every professors/*.yaml (names and exact bytes): Update emails refuses a Save when any
        professor changed since the preview (DECISIONS #104)."""
        return self._digest(self._professor_paths())

    def _professor_paths(self) -> list[Path]:
        folder = self.data_dir / PROFESSORS_DIR
        return sorted(folder.glob("*.yaml")) if folder.is_dir() else []

    def _digest(self, paths: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(self._rel(path).encode("utf-8") + b"\0")
            digest.update((self._read_bytes(path) if path.exists() else b"<missing>") + b"\0")
        return digest.hexdigest()

    def add_courses(self, new: list[TranscriptCourse]) -> None:
        """Append courses to ``courses.yaml`` (created if missing). An id already in the file is refused:
        ids are never reused (docs/01 §1)."""
        if not new:
            return
        current = self.load_courses()
        taken = {c.id for c in current.courses}
        clash = [c.id for c in new if c.id in taken]
        if clash:
            raise DataError(
                COURSES_FILE, "courses", f"course id {clash[0]} already exists; nothing was written"
            )
        current.courses = [*current.courses, *new]
        self._save(self.data_dir / COURSES_FILE, current)

    def save_courses(self, courses: Courses) -> bool:
        """Write ``courses.yaml`` (shape and comments kept; an unchanged save writes nothing). Used by Edit,
        Delete and Restore on /courses (DECISIONS #94)."""
        return self._save(self.data_dir / COURSES_FILE, courses)

    def create_source_text(self, name: str, content: str) -> None:
        """Write ``sources/<name>`` (the identifier-stripped text of an imported document), never over an
        existing file. The original document is never stored (docs/01 §10)."""
        folder = self.data_dir / SOURCES_DIR
        with _CREATE_LOCK:
            if folder.is_dir() and any(p.name.casefold() == name.casefold() for p in folder.iterdir()):
                raise DataError(f"{SOURCES_DIR}/{name}", None, "already exists; nothing was written")
            self.write_bytes_atomic(folder / name, content.encode("utf-8"))
        log.info("saved %s/%s", SOURCES_DIR, name)

    def source_text(self, name: str | None) -> str:
        """The text of ``sources/<name>`` ("" when there is none or it can't be read): the merge preview looks
        there for the transcript's own form of a value (DECISIONS #95)."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return ""
        try:
            return (self.data_dir / SOURCES_DIR / name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def source_with_sha(self, sha256: str) -> str | None:
        """The ``sources/`` file already made from the document with this sha256 (its header line), if any:
        accepting from an older copy of the preview reuses it instead of writing a second one (#67)."""
        folder = self.data_dir / SOURCES_DIR
        if not folder.is_dir():
            return None
        wanted = f"# sha256: {sha256}"
        for path in sorted(folder.glob("*.txt")):
            try:
                head = path.read_text(encoding="utf-8").splitlines()[:2]
            except OSError:
                continue
            if wanted in head:
                return path.name
        return None

    def source_names(self) -> set[str]:
        folder = self.data_dir / SOURCES_DIR
        return {p.name for p in folder.iterdir()} if folder.is_dir() else set()

    # ---- everything ------------------------------------------------------------------------------

    def load_all(self) -> Snapshot:
        """Load every file; invalid ones are reported in ``errors`` and the rest still load."""
        errors: list[DataError] = []
        try:
            settings: Settings | None = self.load_settings()
        except DataError as err:
            settings, errors = None, [err]
        programmes, programme_errors = self.load_programmes()
        professors, professor_errors = self.load_professors()
        try:
            biography = self.load_biography()
        except DataError as err:
            biography, errors = Biography(), [*errors, err]
        try:
            every_course = self.load_courses().courses
        except DataError as err:
            every_course, errors = [], [*errors, err]
        courses = [c for c in every_course if not c.deleted]
        all_course_ids = {c.id for c in every_course}
        return Snapshot(
            settings,
            programmes,
            professors,
            biography,
            [*errors, *programme_errors, *professor_errors],
            courses,
            all_course_ids,
        )
