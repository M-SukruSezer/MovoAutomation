"""Akışı sistemin kendi zamanlayıcısına derler.

Motor kendi arka plan zamanlayıcısını yazmaz; systemd kullanıcı birimleri
üretir. Böylece durum takibi, günlük (journal), yeniden başlatma ve açılışta
çalışma bedavaya gelir -- ve kullanıcı akışı systemctl ile de durdurabilir.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .flow import Flow

DISPATCHER_DIR = Path("/etc/NetworkManager/dispatcher.d")


def user_unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "systemd" / "user"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "movo"


def unit_name(flow: Flow, kind: str = "service") -> str:
    return f"movo-{flow.slug}.{kind}"


def _exe() -> str:
    """Birimlerin çağıracağı movo komutu."""
    env = os.environ.get("MOVO_BIN")
    if env:
        return env
    found = shutil.which("movo")
    if found:
        return found
    return str(Path(__file__).resolve().parents[1] / "bin" / "movo")


def render(flow: Flow) -> dict[str, str]:
    """Akış için üretilecek birim dosyaları: ad -> içerik."""
    src = str(Path(flow.source).resolve()) if flow.source else ""
    exe = _exe()
    desc = (flow.description or flow.name).splitlines()[0][:120]
    common = (
        f"[Unit]\n"
        f"Description=Akış: {flow.name}\n"
        f"Documentation=file://{src}\n"
        f"X-Movo-Flow={src}\n\n"
    )

    if flow.trigger_type == "dbus":
        # Sinyal "şu an" gelir: klasör izleyicisi gibi uzun ömürlü bir
        # servis. Oturum bus'ına eriştiği için kullanıcı birimi olmak
        # zorunda -- sistem bus'ını dinlese bile.
        from .listen import spec_from
        spec = spec_from(flow.trigger)
        return {unit_name(flow, "service"): (
            common
            + "[Service]\n"
            "Type=simple\n"
            f"ExecStart={exe} izle {src} --quiet\n"
            "Restart=always\n"
            "RestartSec=2\n"
            "NoNewPrivileges=yes\n"
            f"Environment=MOVO_FLOW_NAME={flow.slug}\n"
            f"# dinlenen: {spec.label()}\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )}

    if flow.trigger_type == "folder":
        path = Path(str(flow.trigger["path"])).expanduser()
        return {unit_name(flow, "service"): (
            common
            + "[Service]\n"
            f"Type=simple\n"
            f"ExecStart={exe} izle {src} --quiet\n"
            "Restart=always\n"
            "RestartSec=2\n"
            "NoNewPrivileges=yes\n"
            f"Environment=MOVO_FLOW_NAME={flow.slug}\n"
            f"# izlenen klasör: {path}\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )}

    if flow.trigger_type == "schedule":
        on_cal = str(flow.trigger["on_calendar"])
        persistent = "true" if flow.trigger.get("persistent", True) else "false"
        return {
            unit_name(flow, "service"): (
                common
                + "[Service]\n"
                "Type=oneshot\n"
                f"ExecStart={exe} calistir {src} --quiet\n"
                "NoNewPrivileges=yes\n"
            ),
            unit_name(flow, "timer"): (
                f"[Unit]\nDescription=Akış zamanlayıcı: {desc}\n\n"
                "[Timer]\n"
                f"OnCalendar={on_cal}\n"
                f"Persistent={persistent}\n"
                f"Unit={unit_name(flow, 'service')}\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
        }

    if flow.trigger_type == "network":
        return {}  # dispatcher ile kurulur, bkz. dispatcher_script()

    return {}


def dispatcher_script(flow: Flow) -> str:
    """NetworkManager dispatcher script'i (root yetkisi ister, kullanıcı kurar)."""
    conn = str(flow.trigger["connection"])
    state = str(flow.trigger.get("state", "up"))
    src = str(Path(flow.source).resolve()) if flow.source else ""
    uid = os.getuid()
    return f"""#!/bin/sh
# Akış: {flow.name}
# Kuran: movo kur {src}
# NetworkManager bu script'i her ağ olayında root olarak çalıştırır; akışı
# kullanıcı oturumunda tetiklemek için systemd-run ile kullanıcıya iner.
IFACE="$1"
ACTION="$2"

[ "$ACTION" = "{state}" ] || exit 0
[ "$CONNECTION_ID" = "{conn}" ] || exit 0

exec systemd-run --quiet --user --machine={os.environ.get('USER', 'kullanici')}@.host \\
  --setenv=MOVO_TRIGGER_CONNECTION="$CONNECTION_ID" \\
  --setenv=MOVO_TRIGGER_STATE="$ACTION" \\
  --setenv=MOVO_TRIGGER_INTERFACE="$IFACE" \\
  --uid={uid} \\
  {_exe()} calistir {src} --quiet
"""


def dispatcher_path(flow: Flow) -> Path:
    return DISPATCHER_DIR / f"90-movo-{flow.slug}"


def systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, check=check)


def install(flow: Flow, enable: bool = True) -> list[Path]:
    units = render(flow)
    if not units:
        return []
    d = user_unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in units.items():
        p = d / name
        p.write_text(body, encoding="utf-8")
        written.append(p)
    systemctl("daemon-reload")
    if enable:
        target = (unit_name(flow, "timer") if flow.trigger_type == "schedule"
                  else unit_name(flow, "service"))
        systemctl("enable", "--now", target)
    return written


def uninstall(flow: Flow) -> list[str]:
    removed = []
    units = list(render(flow)) or [unit_name(flow, "service")]
    for name in reversed(units):
        systemctl("disable", "--now", name)
    for name in units:
        p = user_unit_dir() / name
        if p.exists():
            p.unlink()
            removed.append(name)
    systemctl("daemon-reload")
    return removed


def status(flow: Flow) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in render(flow) or []:
        active = systemctl("is-active", name).stdout.strip() or "yok"
        enabled = systemctl("is-enabled", name).stdout.strip() or "yok"
        out[name] = f"{active} / {enabled}"
    return out
