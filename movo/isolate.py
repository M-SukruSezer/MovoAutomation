"""İzin sözleşmesini çekirdeğe onaylatmak: Landlock.

Şimdiye kadar izin modeli bir *sözleşme*ydi: eylem manifestinde ne
kullanacağını beyan ediyor, ``ctx.require`` beyan etmediğini kullanmaya
kalkınca duruyordu. Dürüst eylemler için bu yeterli. Dürüst olmayan ya da
yalnızca hatalı olan bir eylem içinse hiçbir şey ifade etmiyordu --
``ctx.require`` çağırmadan ``open(...,'w')`` yazmak yetiyordu.

Landlock bu boşluğu kapatıyor: ayrıcalıksız, süreç başına, geri alınamaz
bir dosya sistemi (ve TCP) kısıtlaması. Projenin kendi izin sözlüğü
doğrudan çekirdek haklarına çevriliyor:

    fs.read   → yalnızca adımın yol argümanları okunabilir
    fs.write  → yalnızca hedef klasörler ve geçici klasör yazılabilir
    fs.delete → silme hakkı ayrıca verilir
    exec      → beyan edilmemişse hiçbir şey çalıştırılamaz
    net       → beyan edilmemişse TCP bağlantısı kurulamaz
    dbus:...  → bus soketlerine erişim

Kısıtlama geri alınamaz olduğu için adım ayrı bir çocuk süreçte
çalıştırılıyor: motor kendi haklarını kaybetmiyor, adım da kendi
sözleşmesinin dışına çıkamıyor.

Beyan edilen izinler yine de bir tavan, ölçüm değil: ``fs.write`` diyen
bir eylem hedef klasörün tamamına yazabilir. Kazanç, *kapsamın*
daralması -- bir resim eylemi artık ~/.ssh'e dokunamaz.
"""

from __future__ import annotations

import ctypes
import json
import os
import site
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

NR_CREATE_RULESET = 444
NR_ADD_RULE = 445
NR_RESTRICT_SELF = 446
PR_SET_NO_NEW_PRIVS = 38
RULE_PATH_BENEATH = 1

# Dosya sistemi hakları (uapi/linux/landlock.h)
FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13          # ABI 2
FS_TRUNCATE = 1 << 14       # ABI 3

NET_BIND_TCP = 1 << 0       # ABI 4
NET_CONNECT_TCP = 1 << 1

READ = FS_READ_FILE | FS_READ_DIR

#: Yalnızca klasörlere verilebilen haklar. Dosyaya ya da aygıta bunları
#: istemek EINVAL döndürür, o yüzden kural yazarken ayıklanıyor.
DIR_ONLY = (FS_READ_DIR | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_REFER
            | FS_MAKE_CHAR | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK
            | FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM)
MAKE = (FS_MAKE_REG | FS_MAKE_DIR | FS_MAKE_SYM | FS_MAKE_SOCK
        | FS_MAKE_FIFO | FS_MAKE_CHAR | FS_MAKE_BLOCK)
WRITE = FS_WRITE_FILE | MAKE | FS_REFER | FS_TRUNCATE
DELETE = FS_REMOVE_FILE | FS_REMOVE_DIR

#: Python'un kendisi için gereken yollar. Bunlar kısıtlansaydı yorumlayıcı
#: geç içe aktarma yapamaz, eylem daha ilk satırında ölürdü.
def runtime_paths() -> list[str]:
    out = {sys.prefix, sys.base_prefix, sys.exec_prefix,
           "/usr/lib", "/usr/lib64", "/usr/share", "/lib", "/lib64",
           "/etc", "/proc/self", "/dev/urandom", "/dev/null"}
    out |= {p for p in sys.path if p}
    try:
        out |= set(site.getsitepackages())
    except Exception:
        pass
    out.add(str(Path(__file__).resolve().parent))
    return sorted(out)


def bus_paths() -> list[str]:
    """D-Bus soketleri: unix soketine bağlanmak yazma hakkı ister."""
    out = ["/run/dbus", "/var/run/dbus"]
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        out += [xdg + "/bus", xdg]
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if addr.startswith("unix:path="):
        out.append(addr.split("unix:path=", 1)[1].split(",")[0])
    return out


_libc = None


def _lc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    # uapi'de __attribute__((packed)); Python 3.14 açık düzen istiyor
    _pack_ = 1
    if hasattr(ctypes.Structure, "_layout_") or sys.version_info >= (3, 14):
        _layout_ = "ms"
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32)]


def abi() -> int:
    """Çekirdeğin Landlock ABI sürümü; 0 ise destek yok."""
    try:
        rc = _lc().syscall(ctypes.c_long(NR_CREATE_RULESET), None,
                           ctypes.c_size_t(0), ctypes.c_uint32(1))
    except Exception:
        return 0
    return rc if rc > 0 else 0


def available() -> tuple[bool, str]:
    v = abi()
    if v <= 0:
        return False, "çekirdekte Landlock yok (Linux 5.13+ ve CONFIG_SECURITY_LANDLOCK)"
    return True, f"Landlock ABI {v}"


# ------------------------------------------------------------------- ilke

@dataclass
class Policy:
    """Bir adımın çekirdeğe onaylatılacak hak kümesi."""
    read: set = field(default_factory=set)
    write: set = field(default_factory=set)
    delete: set = field(default_factory=set)
    allow_exec: bool = False
    allow_net: bool = False
    allow_bus: bool = False
    note: str = ""

    def summary(self) -> str:
        parts = []
        if self.read:
            parts.append(f"{len(self.read)} okuma")
        parts.append(f"{len(self.write)} yazma" if self.write else "yazma yok")
        if self.delete:
            parts.append(f"{len(self.delete)} silme")
        parts.append("çalıştırma açık" if self.allow_exec else "çalıştırma kapalı")
        if self.allow_net:
            parts.append("ağ açık")
        if self.allow_bus:
            parts.append("bus açık")
        return ", ".join(parts)


def _dirs_of(values) -> set:
    """Çözülmüş argümanlardaki yolların kapsayacağı klasörler."""
    out: set = set()

    def ekle(v):
        if isinstance(v, (list, tuple)):
            for x in v:
                ekle(x)
            return
        if not isinstance(v, str) or not v.strip():
            return
        p = Path(v).expanduser()
        if not p.is_absolute():
            return
        # Dosyaysa klasörünü, klasörse kendisini kapsa: eylem yazarken
        # geçici dosya üretebilir, kardeşine de dokunabilir.
        out.add(str(p if p.is_dir() else p.parent))

    for v in (values or {}).values():
        ekle(v)
    return out


def policy_for(permissions, args: dict, workdir: Path) -> Policy:
    """Manifest izinleri + çözülmüş argümanlar -> çekirdek hak kümesi."""
    perms = set(permissions or ())
    paths = _dirs_of(args)
    pol = Policy()

    pol.read |= set(runtime_paths())
    pol.read.add(str(workdir))
    pol.write.add(str(workdir))          # ara dosya alanı her zaman açık
    pol.delete.add(str(workdir))

    if "fs.read" in perms:
        pol.read |= paths
    if "fs.write" in perms:
        pol.write |= paths
        pol.read |= paths                # yazmak çoğu zaman okumayı gerektirir
    if "fs.delete" in perms:
        pol.delete |= paths
    if "exec" in perms:
        pol.allow_exec = True
    if "net" in perms:
        pol.allow_net = True
        pol.read.add("/etc/resolv.conf")
    if any(p.startswith("dbus:") for p in perms):
        pol.allow_bus = True
    if "notify" in perms or "display" in perms:
        pol.allow_bus = True
    return pol


# --------------------------------------------------------------- uygulama

def apply(pol: Policy) -> str:
    """Kısıtlamayı *bu* sürece uygular. Geri alınamaz."""
    libc = _lc()
    v = abi()
    if v <= 0:
        raise OSError("Landlock desteklenmiyor")

    handled_fs = READ | WRITE | DELETE | FS_EXECUTE
    if v < 3:
        handled_fs &= ~FS_TRUNCATE
    if v < 2:
        handled_fs &= ~FS_REFER
    handled_net = (NET_BIND_TCP | NET_CONNECT_TCP) if v >= 4 else 0

    attr = _RulesetAttr(handled_fs, handled_net, 0)
    fd = libc.syscall(ctypes.c_long(NR_CREATE_RULESET), ctypes.byref(attr),
                      ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

    def kural(path: str, access: int) -> None:
        access &= handled_fs
        if not access:
            return
        try:
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return                        # yok olan yol kurala dönüşmez
        try:
            import stat as _stat
            if not _stat.S_ISDIR(os.stat(pfd).st_mode):
                access &= ~DIR_ONLY
            if not access:
                return
            rule = _PathBeneath(access, pfd)
            rc = libc.syscall(ctypes.c_long(NR_ADD_RULE), ctypes.c_int(fd),
                              ctypes.c_uint32(RULE_PATH_BENEATH),
                              ctypes.byref(rule), ctypes.c_uint32(0))
            if rc < 0:
                raise OSError(ctypes.get_errno(), f"landlock_add_rule({path})")
        finally:
            os.close(pfd)

    try:
        exec_bits = FS_EXECUTE if pol.allow_exec else 0
        for p in sorted(pol.read):
            kural(p, READ | exec_bits)
        if pol.allow_bus:
            for p in bus_paths():
                kural(p, READ | FS_WRITE_FILE)
        for p in sorted(pol.write):
            kural(p, READ | WRITE | exec_bits)
        for p in sorted(pol.delete):
            kural(p, READ | WRITE | DELETE | exec_bits)
        if pol.allow_net and handled_net:
            # Ağ izni varsa TCP'yi hiç ele almayalım: port port kural
            # yazmak bu katmanın işi değil.
            pass

        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
        rc = libc.syscall(ctypes.c_long(NR_RESTRICT_SELF), ctypes.c_int(fd),
                          ctypes.c_uint32(0))
        if rc < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(fd)
    return f"Landlock ABI {v}"


# ------------------------------------------------------- çatallanmış çalışma

class IsolationUnavailable(Exception):
    """Yalıtım istendi ama kurulamadı."""


def _reset_after_fork() -> None:
    """Çocukta ebeveynden miras kalan paylaşımlı durumu bırak."""
    from . import dbus as dbuslib
    dbuslib._conns.clear()
    dbuslib._names.clear()
    dbuslib._gi_cache.clear()


def can_fork() -> tuple[bool, str]:
    if not hasattr(os, "fork"):
        return False, "bu platformda fork yok"
    if threading.current_thread() is not threading.main_thread():
        # Editörden çalıştırmak akışı bir iş parçacığında yürütüyor;
        # oradan çatallanmak GLib kilitlerini çocuğa kilitli taşır.
        return False, "ana iş parçacığında değil"
    return True, ""


def run_isolated(work, policy: Policy, ctx) -> dict:
    """``work()`` çağrısını kısıtlanmış bir çocuk süreçte çalıştırır.

    ``work`` JSON'a çevrilebilir bir sözlük döndürmeli. Dönen sözlük:
    {"ok", "out"|"err", "perm", "logs", "applied"}.
    """
    ok, why = can_fork()
    if not ok:
        raise IsolationUnavailable(why)
    good, why = available()
    if not good:
        raise IsolationUnavailable(why)

    # Çocuk ctx.lines'ı miras alıyor; yalnızca orada eklenenler geri
    # dönmeli, yoksa ebeveynin zaten bastığı satırlar ikilenir.
    onceki = len(ctx.lines)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                    # ---- çocuk
        code = 0
        try:
            os.close(r)
            _reset_after_fork()
            ctx.quiet = True                        # kayıtlar ebeveynde basılır
            applied = apply(policy)
            out = work() or {}
            payload = {"ok": True, "out": out, "logs": ctx.lines[onceki:],
                       "applied": applied}
        except BaseException as e:                  # noqa: BLE001
            from .runner import PermissionDenied
            payload = {"ok": False, "err": f"{type(e).__name__}: {e}",
                       "perm": isinstance(e, PermissionDenied),
                       "logs": getattr(ctx, "lines", [])[onceki:]}
        try:
            os.write(w, json.dumps(payload, default=str).encode())
        except Exception:
            code = 70
        finally:
            os.close(w)
        os._exit(code)

    os.close(w)                                     # ---- ebeveyn
    chunks = []
    with os.fdopen(r, "rb") as f:
        while True:
            b = f.read(65536)
            if not b:
                break
            chunks.append(b)
    _, status = os.waitpid(pid, 0)
    raw = b"".join(chunks)
    if not raw:
        if os.WIFSIGNALED(status):
            return {"ok": False, "perm": False, "logs": [],
                    "err": f"yalıtılmış adım {os.WTERMSIG(status)} sinyaliyle öldü"}
        return {"ok": False, "perm": False, "logs": [],
                "err": f"yalıtılmış adım çıktı vermeden bitti (durum {status})"}
    try:
        return json.loads(raw.decode())
    except ValueError as e:
        return {"ok": False, "perm": False, "logs": [],
                "err": f"yalıtılmış adımın yanıtı okunamadı: {e}"}
