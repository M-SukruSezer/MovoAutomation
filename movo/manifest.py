"""Eylem manifest standardı.

Bu projenin çekirdek fikri: bir eylem, kodundan ayrı olarak kendini beyan
eder. Bir uygulama paketi kendi eylemlerini
``~/.local/share/movo/actions/*.action.yaml`` içine koyarak akış motoruna
görünür hale gelir -- .desktop dosyalarının uygulama menüsü için yaptığı
işin otomasyon karşılığı.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import dbus as dbuslib
from . import types as T

PERMISSIONS = {
    "fs.read": "dosya okur",
    "fs.write": "dosya oluşturur veya değiştirir",
    "fs.delete": "dosya siler",
    "exec": "harici komut çalıştırır",
    "net": "ağ ayarlarına dokunur",
    "notify": "masaüstü bildirimi gösterir",
    "display": "ekran ayarlarını değiştirir",
}

#: ``dbus:<servis>`` biçimindeki izinler listede yer almaz; serbest ama
#: kapsamlıdır. "D-Bus kullanabilir" diye bir yetki yoktur, çünkü D-Bus'ta
#: her şey yapılabilir: izin hangi *bus adıyla* konuşulduğunu söyler.
DBUS_PERM = dbuslib.PERM_PREFIX


def perm_label(perm: str) -> str:
    """İzni insan diline çevirir; dbus:<ad> izinleri serbest biçimdir."""
    if perm.startswith(DBUS_PERM):
        return f"{perm[len(DBUS_PERM):]} servisiyle D-Bus üzerinden konuşur"
    return PERMISSIONS.get(perm, perm)


def perm_known(perm: str) -> bool:
    if perm.startswith(DBUS_PERM):
        return len(perm) > len(DBUS_PERM)
    return perm in PERMISSIONS


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Port:
    name: str
    type: str
    label: str = ""
    required: bool = False
    default: object = None
    choices: tuple = ()
    #: Bu çıktının tipi hangi girdiyle daraltılıyor? Tetikleyicinin
    #: ``produces:`` anahtarının eylemlere açılmış hâli: file.glob
    #: ``list<file>`` döndürür ama kullanıcı "bu klasör resim tutuyor"
    #: diyebilir ve akışın geri kalanı resim görür.
    narrowed_by: str = ""


@dataclass(frozen=True)
class ActionDef:
    id: str
    name: str
    summary: str
    category: str
    inputs: dict[str, Port]
    outputs: dict[str, Port]
    impl_kind: str
    impl: object
    permissions: tuple[str, ...] = ()
    source: Path | None = None
    tags: tuple[str, ...] = field(default=())

    def input(self, name: str) -> Port:
        if name not in self.inputs:
            raise ManifestError(
                f"{self.id}: '{name}' diye bir girdi yok "
                f"(mevcut: {', '.join(self.inputs) or 'yok'})"
            )
        return self.inputs[name]

    def output(self, name: str) -> Port:
        if name not in self.outputs:
            raise ManifestError(
                f"{self.id}: '{name}' diye bir çıktı yok "
                f"(mevcut: {', '.join(self.outputs) or 'yok'})"
            )
        return self.outputs[name]


def _ports(raw: dict, where: str, kind: str) -> dict[str, Port]:
    out: dict[str, Port] = {}
    for name, spec in (raw or {}).items():
        if not isinstance(spec, dict):
            raise ManifestError(f"{where}: {kind} '{name}' bir sözlük olmalı")
        t = spec.get("type")
        if not t:
            raise ManifestError(f"{where}: {kind} '{name}' için tip belirtilmemiş")
        if not T.is_known(t):
            raise ManifestError(f"{where}: bilinmeyen tip '{t}' ({kind} '{name}')")
        out[name] = Port(
            name=name,
            type=t,
            label=spec.get("label", name),
            required=bool(spec.get("required", False)),
            default=spec.get("default"),
            choices=tuple(spec.get("choices", ()) or ()),
            narrowed_by=str(spec.get("narrowed_by", "") or ""),
        )
    return out


def load_file(path: Path) -> ActionDef:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ManifestError(f"{path}: YAML okunamadı: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: manifest bir sözlük olmalı")

    for key in ("id", "name", "impl"):
        if key not in raw:
            raise ManifestError(f"{path}: '{key}' alanı zorunlu")

    inputs = _ports(raw.get("inputs"), str(path), "girdi")
    outputs = _ports(raw.get("outputs"), str(path), "çıktı")

    for oname, port in outputs.items():
        if port.narrowed_by and port.narrowed_by not in inputs:
            raise ManifestError(
                f"{path}: '{oname}' çıktısı '{port.narrowed_by}' girdisiyle "
                f"daraltılıyor ama öyle bir girdi yok")

    impl = raw["impl"]
    if not isinstance(impl, dict) or "kind" not in impl:
        raise ManifestError(f"{path}: impl.kind zorunlu (python, command veya dbus)")
    kind = impl["kind"]
    if kind == "python":
        if ":" not in str(impl.get("entry", "")):
            raise ManifestError(f"{path}: impl.entry 'modul:fonksiyon' biçiminde olmalı")
        body: object = impl["entry"]
    elif kind == "command":
        argv = impl.get("argv")
        if not isinstance(argv, list) or not argv:
            raise ManifestError(f"{path}: impl.argv boş olmayan bir liste olmalı")
        body = [str(a) for a in argv]
    elif kind == "dbus":
        try:
            body = dbuslib.parse(impl, inputs, outputs, str(path))
        except dbuslib.DBusSpecError as e:
            raise ManifestError(str(e)) from None
    else:
        raise ManifestError(f"{path}: bilinmeyen impl.kind '{kind}'")

    perms = tuple(raw.get("permissions", ()) or ())
    for p in perms:
        if not perm_known(p):
            raise ManifestError(
                f"{path}: bilinmeyen izin '{p}' "
                f"(bilinenler: {', '.join(PERMISSIONS)}; ayrıca dbus:<servis adı>)")
    if kind == "command" and "exec" not in perms:
        perms = perms + ("exec",)
    if kind == "dbus" and not dbuslib.covered(body.pattern, perms):
        # Manifest konuştuğu servisi beyan etmek zorunda: izin listesi
        # otomatik doldurulmaz, yoksa sözleşme kendi kendini imzalardı.
        raise ManifestError(
            f"{path}: impl '{body.pattern}' servisine çağrı yapıyor ama permissions "
            f"içinde onu kapsayan bir 'dbus:...' izni yok")

    return ActionDef(
        id=str(raw["id"]),
        name=str(raw["name"]),
        summary=str(raw.get("summary", "")),
        category=str(raw.get("category", "genel")),
        inputs=inputs,
        outputs=outputs,
        impl_kind=kind,
        impl=body,
        permissions=perms,
        source=path,
        tags=tuple(raw.get("tags", ()) or ()),
    )


def search_path() -> list[Path]:
    """Eylem manifestlerinin arandığı yerler, öncelik sırasıyla."""
    paths: list[Path] = []
    env = os.environ.get("MOVO_ACTION_PATH")
    if env:
        paths += [Path(p).expanduser() for p in env.split(":") if p]
    paths.append(Path(__file__).parent / "actions")
    xdg = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    paths.append(Path(xdg).expanduser() / "movo" / "actions")
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            paths.append(Path(d) / "movo" / "actions")
    return paths
