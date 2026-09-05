from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

LOCK_FILE = '.deploy-pack.lock'
MARK_JOURNAL_FILE = '.deploy-pack-mark-transaction.json'
_LOCAL = threading.local()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    try:
        old_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.tmp-', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode if mode is not None else (old_mode if old_mode is not None else 0o600))
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    atomic_write_bytes(path, text.encode('utf-8'), mode=mode)


def atomic_write_json(path: Path, value: object, *, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + '\n', mode=mode)


@contextmanager
def repository_lock(root: Path, *, timeout_seconds: float = 10.0):
    root = root.resolve()
    key = str(root)
    held = getattr(_LOCAL, "held", {})
    if key in held:
        held[key]["depth"] += 1
        try:
            yield
        finally:
            held[key]["depth"] -= 1
        return
    path = root / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open('a+b')
    os.chmod(path, 0o600)
    if fcntl is not None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise TimeoutError(f'deploy-pack repository is locked: {path}')
                time.sleep(0.05)
    if not hasattr(_LOCAL, "held"):
        _LOCAL.held = {}
    _LOCAL.held[key] = {"depth": 1, "fh": fh}
    try:
        # Any stateful operation is a recovery boundary for an interrupted mark.
        if (root / MARK_JOURNAL_FILE).exists():
            recover_mark_transaction(root)
        yield
    finally:
        rec = _LOCAL.held.pop(key, None)
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _snapshot(path: Path) -> dict:
    if not path.exists():
        return {'exists': False, 'dataBase64': None, 'mode': None}
    return {
        'exists': True,
        'dataBase64': base64.b64encode(path.read_bytes()).decode('ascii'),
        'mode': path.stat().st_mode & 0o777,
    }


def _restore(path: Path, snap: dict) -> None:
    if not snap.get('exists'):
        path.unlink(missing_ok=True)
        _fsync_dir(path.parent)
        return
    atomic_write_bytes(path, base64.b64decode(snap['dataBase64']), mode=int(snap.get('mode') or 0o600))


def begin_mark_transaction(root: Path, paths: list[Path], metadata: dict) -> dict:
    journal_path = root / MARK_JOURNAL_FILE
    if journal_path.exists():
        recover_mark_transaction(root)
    journal = {
        'schemaVersion': 1,
        'state': 'prepared',
        'metadata': metadata,
        'files': {str(p.relative_to(root)): _snapshot(p) for p in paths},
    }
    atomic_write_json(journal_path, journal)
    return journal


def commit_mark_transaction(root: Path) -> None:
    path = root / MARK_JOURNAL_FILE
    path.unlink(missing_ok=True)
    _fsync_dir(root)


def recover_mark_transaction(root: Path) -> bool:
    path = root / MARK_JOURNAL_FILE
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding='utf-8'))
    for rel, snap in (value.get('files') or {}).items():
        _restore(root / rel, snap)
    path.unlink(missing_ok=True)
    _fsync_dir(root)
    return True
