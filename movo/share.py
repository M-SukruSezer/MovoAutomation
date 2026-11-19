"""Akış paylaşımı: dışa aktarma ve -- asıl iş -- içe alma.

Akış zaten tek bir YAML dosyası, yani e-postayla gönderilebilir. Eksik
olan gönderme değil, **alma** tarafı: başkasının akışını açtığında üç
şeyi görmen gerekir.

  1. Ne istiyor?      → onay katmanının istemi, alma anında gösterilir
  2. Ne eksik?        → tanımadığın eylemler, paketle geldi mi gelmedi mi
  3. Nereye dokunur?  → ``/home/ayse/...`` senin makinende yok

Paket tek bir okunabilir YAML belgesi. Tarball değil, çünkü bu projenin
iddiası akışın *okunabilir ve diff'lenebilir* olması; paylaşım biçimi
bunu bozarsa paylaşılan şey artık akış olmaz.

Paketin kod taşımadığına dikkat: içindeki eylem manifestleri ya
deklaratif (``kind: dbus``) ya da motorun kendi gövdelerine işaret eden
girdiler olabilir. Yabancı bir Python modülüne işaret eden manifest
reddedilir -- paylaşım kanalı bir kod yükleme kanalına dönüşmesin.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import __version__

PACKAGE_VERSION = 1
#: Pakete konabilecek python girdileri yalnızca motorun kendi modülleri.
SAFE_ENTRY_PREFIX = "movo."
SAFE_KINDS = {"dbus", "command"}


class ShareError(Exception):
    """Paket kurulamadı ya da okunamadı."""


# --------------------------------------------------------------- taşınabilirlik

def contract_home(text: str, home: str | None = None) -> str:
    """/home/ayse/Resimler -> ~/Resimler. Paketi taşınabilir yapan şey."""
    h = home or str(Path.home())
    if not isinstance(text, str) or not h:
        return text
    if text == h:
        return "~"
    if text.startswith(h + os.sep):
        return "~" + text[len(h):]
    return text


def portable_line(text: str, home: str | None = None) -> str:
    """Serbest metnin *içindeki* ev yolunu da kısaltır.

    contract_home yalnızca baştaki yolu alır; özet satırları ise
    "dokunduğu yerler: /home/ayse/..." gibi gömülü yol taşır.
    """
    h = home or str(Path.home())
    return text.replace(h, "~") if h else text


def _walk(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    return value


def portable(raw: dict, home: str | None = None) -> tuple[dict, list[str]]:
    """Ev dizinini ~ yapar; dışarıda kalan mutlak yolları bildirir."""
    h = home or str(Path.home())
    disarida: list[str] = []

    def fix(s: str) -> str:
        yeni = contract_home(s, h)
        # Ev dışında kalan mutlak yol alıcıda anlamsız olur; sessizce
        # taşımak yerine söylüyoruz.
        for parca in yeni.replace("\n", " ").split():
            p = parca.strip("'\"`,;:")
            if p.startswith("/") and not p.startswith(("/tmp/", "/dev/", "/proc/")):
                if p not in disarida:
                    disarida.append(p)
        return yeni

    return _walk(raw, fix), disarida


# -------------------------------------------------------------------- paket

@dataclass
class Bundle:
    flow: dict
    actions: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return str(self.flow.get("slug") or self.flow.get("name") or "movo")

    @property
    def name(self) -> str:
        return str(self.flow.get("name") or self.slug)


def _manifest_ok(raw: dict) -> str:
    """Paketteki eylem manifesti güvenli mi? Sorun varsa gerekçe döner."""
    impl = raw.get("impl") or {}
    kind = str(impl.get("kind", ""))
    if kind in SAFE_KINDS:
        return ""
    if kind == "python":
        entry = str(impl.get("entry", ""))
        if entry.startswith(SAFE_ENTRY_PREFIX):
            return ""
        return (f"'{entry}' motorun dışında bir Python modülüne işaret ediyor; "
                f"paket kod taşıyamaz")
    return f"bilinmeyen impl.kind '{kind}'"


def build(flow_raw: dict, actions: list, ask_lines: list,
          note: str = "") -> tuple[Bundle, list[str]]:
    """Paylaşım paketini kurar. (paket, uyarılar) döner."""
    movo, disarida = portable(dict(flow_raw))
    temiz_actions = []
    uyarilar = list(f"ev dizini dışında mutlak yol: {p}" for p in disarida)
    for a in actions:
        neden = _manifest_ok(a)
        if neden:
            uyarilar.append(f"'{a.get('id')}' pakete konmadı: {neden}")
            continue
        temiz_actions.append(portable(dict(a))[0])
    # Özet de taşınabilir olmalı: gönderenin kullanıcı adı pakete
    # sızmasın, alıcıda anlamsız bir yol görünmesin.
    ev = str(Path.home())
    meta = {
        "sürüm": PACKAGE_VERSION,
        "motor": __version__,
        "tarih": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "istem": [portable_line(line, ev) for line in ask_lines],
    }
    if note:
        meta["not"] = note
    return Bundle(flow=movo, actions=temiz_actions, meta=meta), uyarilar


def dumps(b: Bundle) -> str:
    basliklar = [
        "# movo paylaşım paketi — tek dosya, okunabilir, diff'lenebilir.",
        "# Almak için:  movo al <bu dosya>   (önce --prova ile bakın)",
        "",
    ]
    govde = {"paket": b.meta, "movo": b.flow}
    if b.actions:
        govde["eylemler"] = b.actions
    return "\n".join(basliklar) + yaml.safe_dump(
        govde, allow_unicode=True, sort_keys=False, width=100)


def loads(text: str) -> Bundle:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ShareError(f"paket okunamadı: {e}") from None
    if not isinstance(raw, dict) or "movo" not in raw:
        raise ShareError("bu bir movo paylaşım paketi değil ('movo' bölümü yok)")
    meta = raw.get("paket") or {}
    if not isinstance(meta, dict):
        raise ShareError("'paket' bölümü bozuk")
    sur = int(meta.get("sürüm", 0) or 0)
    if sur > PACKAGE_VERSION:
        raise ShareError(
            f"paket sürümü {sur}, bu motor en fazla {PACKAGE_VERSION} okuyor")
    flow = raw.get("movo")
    if not isinstance(flow, dict):
        raise ShareError("'movo' bölümü bir sözlük olmalı")
    actions = raw.get("eylemler") or []
    if not isinstance(actions, list):
        raise ShareError("'eylemler' bölümü bir liste olmalı")
    for a in actions:
        if not isinstance(a, dict) or "id" not in a:
            raise ShareError("eylem manifestlerinin her birinde 'id' olmalı")
        neden = _manifest_ok(a)
        if neden:
            raise ShareError(f"'{a.get('id')}' kabul edilmedi: {neden}")
    return Bundle(flow=flow, actions=actions, meta=meta)


def expand(raw: dict, home: str | None = None):
    """~ yollarını alıcının ev dizinine açar."""
    h = home or str(Path.home())
    return _walk(raw, lambda s: h + s[1:] if s == "~" or s.startswith("~/") else s)


def missing_actions(b: Bundle, reg) -> tuple[list[str], list[str]]:
    """(paketle gelenler, hâlâ eksik olanlar)."""
    gereken = {str(s.get("action", "")) for s in (b.flow.get("steps") or [])
               if isinstance(s, dict)}
    gereken.discard("")
    paket = {str(a.get("id")) for a in b.actions}
    gelen = sorted(gereken & paket)
    eksik = sorted(a for a in gereken if a not in paket and a not in reg)
    return gelen, eksik
