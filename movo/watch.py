"""Klasör izleyici: ctypes ile doğrudan inotify (ek paket gerekmez).

systemd .path unit'leri "klasör değişti" diyebilir ama hangi dosyanın
geldiğini söylemez; akış motoruna tetikleyen dosyanın yolu lazım. Bu yüzden
klasör tetikleyicisi uzun ömürlü bir servis olarak kurulur ve çekirdeği
inotify olayları dinler. inotify kullanılamazsa yoklamaya düşer.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fnmatch
import os
import re
import select
import signal
import struct
import time
from pathlib import Path

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000

EVENT_NAMES = {
    "created": IN_CLOSE_WRITE | IN_CREATE,
    "moved_in": IN_MOVED_TO,
}
_HDR = struct.Struct("iIII")
_stop = False


def _on_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True


def expand_braces(pattern: str) -> list[str]:
    """*.{png,jpg} -> ['*.png', '*.jpg']"""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: list[str] = []
    for part in m.group(1).split(","):
        out += expand_braces(pattern[: m.start()] + part.strip() + pattern[m.end():])
    return out


def matches(name: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in expand_braces(pattern))


def _settled(path: Path, settle_ms: int) -> bool:
    """Dosya yazılması bitti mi? (indirme/kopyalama sırasında tetiklenmeyi önler)"""
    if settle_ms <= 0:
        return path.is_file()
    last = -1
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last:
            return True
        last = size
        time.sleep(settle_ms / 1000.0)
    return path.is_file()


class Inotify:
    def __init__(self):
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        self.libc = ctypes.CDLL(libc_name, use_errno=True)
        self.fd = self.libc.inotify_init1(0o4000)  # IN_NONBLOCK
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 başarısız")

    def add(self, path: Path, mask: int) -> int:
        wd = self.libc.inotify_add_watch(self.fd, str(path).encode(), mask)
        if wd < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"{path} izlenemedi: {os.strerror(err)}")
        return wd

    def read(self, timeout: float = 1.0):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        try:
            data = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return []
        except OSError as e:
            if e.errno == errno.EINTR:
                return []
            raise
        events, off = [], 0
        while off + _HDR.size <= len(data):
            wd, mask, cookie, length = _HDR.unpack_from(data, off)
            off += _HDR.size
            name = data[off:off + length].split(b"\0", 1)[0].decode("utf-8", "replace")
            off += length
            events.append((wd, mask, name))
        return events

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def watch_folder(path: Path, on_file, match: str | None = None,
                 events: str = "created,moved_in", settle_ms: int = 300,
                 ignore_existing: bool = True, once: bool = False,
                 quiet: bool = False) -> int:
    """Klasörü izler, eşleşen her yeni dosya için on_file(Path) çağırır."""
    global _stop
    _stop = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)

    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    mask = 0
    for ev in str(events).split(","):
        ev = ev.strip()
        if ev in EVENT_NAMES:
            mask |= EVENT_NAMES[ev]
    mask = mask or (IN_CLOSE_WRITE | IN_MOVED_TO)

    handled = 0
    if not ignore_existing:
        for f in sorted(path.iterdir()):
            if f.is_file() and matches(f.name, match):
                on_file(f)
                handled += 1
                if once:
                    return handled

    try:
        ino = Inotify()
        ino.add(path, mask)
        backend = "inotify"
    except OSError as e:
        if not quiet:
            print(f"! inotify kullanılamadı ({e}); yoklamaya geçildi", flush=True)
        return _poll_folder(path, on_file, match, settle_ms, once, quiet, handled)

    if not quiet:
        print(f"◉ {path} izleniyor ({backend}"
              + (f", desen {match}" if match else "") + "). Durdurmak için Ctrl-C.",
              flush=True)
    recent: dict[str, float] = {}
    try:
        while not _stop:
            for _wd, ev_mask, name in ino.read(1.0):
                if ev_mask & IN_Q_OVERFLOW:
                    if not quiet:
                        print("! inotify kuyruğu taştı, bazı olaylar kaçtı", flush=True)
                    continue
                if ev_mask & IN_IGNORED or not name:
                    continue
                if not matches(name, match):
                    continue
                target = path / name
                now = time.monotonic()
                if now - recent.get(name, 0) < 1.0:
                    continue
                recent[name] = now
                if not _settled(target, settle_ms):
                    continue
                on_file(target)
                handled += 1
                if once:
                    return handled
            recent = {k: v for k, v in recent.items() if time.monotonic() - v < 30}
    finally:
        ino.close()
    return handled


def _poll_folder(path: Path, on_file, match, settle_ms, once, quiet, handled=0) -> int:
    seen = {f.name for f in path.iterdir() if f.is_file()}
    while not _stop:
        time.sleep(1.0)
        try:
            now = {f.name for f in path.iterdir() if f.is_file()}
        except FileNotFoundError:
            continue
        for name in sorted(now - seen):
            if matches(name, match) and _settled(path / name, settle_ms):
                on_file(path / name)
                handled += 1
                if once:
                    return handled
        seen = now
    return handled
