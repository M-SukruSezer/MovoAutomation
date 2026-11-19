"""Onay katmanı: izin hikâyesinin eksik ayağı.

Üç katman var ve üçü de başka bir soruya cevap veriyor:

    manifest  → eylem ne istiyor?        (beyan)
    onay      → kullanıcı ne kabul etti? (bu dosya)
    Landlock  → çekirdek ne bırakıyor?   (zorlama)

Beyan ve zorlama vardı; arada kullanıcı yoktu. Bir akış ``fs.write`` ve
``exec`` isteyip arka planda çalışmaya başlayabiliyordu, kimse sormadan.

Buradaki fikir basit: akışın *istediği şeylerin* bir parmak izi alınıyor
ve kullanıcının onayı o parmak izine bağlanıyor. Akış sonradan daha
fazlasını istemeye başlarsa -- yeni bir izin, yeni bir yol, yeni bir
eylem -- onay **bayatlıyor** ve yenilenmesi gerekiyor. Onayı anlamlı
kılan budur: bir kez tıklanıp unutulan bir şey değil, neye evet
denildiğinin kaydı.

Parmak izi akışın tamamından değil, yalnızca onay gerektiren
alanlarından alınıyor. Adım adını değiştirmek ya da ``max_width``'i
1600'den 1200'e çekmek onayı bozmaz; ``shell.run`` eklemek bozar.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import types as T
from .flowref import refs_in
from .manifest import perm_label

#: Kullanıcının bakmadan geçmemesi gereken yetkiler.
SENSITIVE = {"exec", "net", "fs.delete"}
ESCAPE_ACTIONS = {"shell.run"}


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "movo"


def store_path() -> Path:
    return state_dir() / "onaylar.json"


# --------------------------------------------------------------------- istem

@dataclass(frozen=True)
class Ask:
    """Bir akışın kullanıcıdan istedikleri."""
    slug: str
    name: str
    permissions: tuple = ()
    actions: tuple = ()
    paths: tuple = ()
    trigger: str = ""

    def fingerprint(self) -> str:
        ham = json.dumps({
            "izinler": list(self.permissions),
            "eylemler": list(self.actions),
            "yollar": list(self.paths),
            "tetikleyici": self.trigger,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(ham.encode("utf-8")).hexdigest()[:16]

    @property
    def sensitive(self) -> bool:
        return bool(SENSITIVE & set(self.permissions)) or bool(
            ESCAPE_ACTIONS & set(self.actions))

    def why_sensitive(self) -> list[str]:
        out = [perm_label(p) for p in self.permissions if p in SENSITIVE]
        if ESCAPE_ACTIONS & set(self.actions):
            out.append("kabuk komutu çalıştırır (shell.run)")
        return out

    def lines(self) -> list[str]:
        """İnsan diliyle: bu akış neye evet demeni istiyor?"""
        out = [f"tetikleyici: {self.trigger}"] if self.trigger else []
        for p in self.permissions:
            out.append(perm_label(p))
        if self.paths:
            out.append("dokunduğu yerler: " + ", ".join(self.paths))
        out.append("eylemler: " + ", ".join(self.actions))
        return out

    def as_dict(self) -> dict:
        return {"izinler": list(self.permissions), "eylemler": list(self.actions),
                "yollar": list(self.paths), "tetikleyici": self.trigger}


def _literal_paths(flow, reg) -> list[str]:
    """Akışta düz yazılmış yollar: onayın asıl konusu.

    Kablodan gelen değerler çalışma anında belli olur ve zaten bir
    önceki adımın onayladığı yollardan türer; burada kullanıcının
    dosyada *gördüğü* yolları topluyoruz.
    """
    out: set[str] = set()
    for step in flow.steps:
        if step.action not in reg:
            continue
        act = reg.get(step.action)
        for key, value in (step.args or {}).items():
            port = act.inputs.get(key)
            if port is None or not isinstance(value, str) or refs_in(value):
                continue
            if not T.is_assignable(port.type, "path"):
                continue
            v = value.strip()
            if v:
                out.add(str(Path(v).expanduser()))
    for key in ("path", "dir"):
        v = (flow.trigger or {}).get(key)
        if isinstance(v, str) and v.strip():
            out.add(str(Path(v.strip()).expanduser()))
    return sorted(out)


def ask_for(flow, reg) -> Ask:
    from .flow import TRIGGERS

    spec = TRIGGERS.get(flow.trigger_type, {})
    detay = ""
    for key in ("path", "on_calendar", "connection", "interface"):
        if (flow.trigger or {}).get(key):
            detay = f" · {flow.trigger[key]}"
            break
    return Ask(
        slug=flow.slug,
        name=flow.name,
        permissions=tuple(sorted(flow.permissions(reg))),
        actions=tuple(sorted({s.action for s in flow.steps})),
        paths=tuple(_literal_paths(flow, reg)),
        trigger=f"{spec.get('label', flow.trigger_type)}{detay}",
    )


# --------------------------------------------------------------------- kayıt

@dataclass
class Grant:
    slug: str
    fingerprint: str
    at: str
    name: str = ""
    source: str = ""
    ask: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"slug": self.slug, "parmak_izi": self.fingerprint,
                "tarih": self.at, "ad": self.name, "kaynak": self.source,
                "istem": self.ask}

    @classmethod
    def from_dict(cls, d: dict) -> "Grant":
        return cls(slug=str(d.get("slug", "")),
                   fingerprint=str(d.get("parmak_izi", "")),
                   at=str(d.get("tarih", "")), name=str(d.get("ad", "")),
                   source=str(d.get("kaynak", "")), ask=dict(d.get("istem") or {}))


def load() -> dict[str, Grant]:
    p = store_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    out = {}
    for d in raw.get("onaylar", []):
        g = Grant.from_dict(d)
        if g.slug:
            out[g.slug] = g
    return out


def save(grants: dict[str, Grant]) -> Path:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"sürüm": 1,
            "onaylar": [g.as_dict() for g in sorted(grants.values(),
                                                    key=lambda g: g.slug)]}
    tmp = p.with_suffix(".json.yeni")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)                      # yarım yazılmış kayıt dosyası olmasın
    return p


def grant(ask: Ask, source: str = "") -> Grant:
    grants = load()
    g = Grant(slug=ask.slug, fingerprint=ask.fingerprint(),
              at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              name=ask.name, source=source, ask=ask.as_dict())
    grants[ask.slug] = g
    save(grants)
    return g


def revoke(slug: str) -> bool:
    grants = load()
    if slug not in grants:
        return False
    del grants[slug]
    save(grants)
    return True


def check(ask: Ask) -> tuple[str, Grant | None]:
    """'var' | 'bayat' | 'yok'."""
    g = load().get(ask.slug)
    if g is None:
        return "yok", None
    if g.fingerprint != ask.fingerprint():
        return "bayat", g
    return "var", g


def diff(ask: Ask, g: Grant) -> list[str]:
    """Onaydan bu yana ne değişti? Bayat onayın anlamı budur."""
    out: list[str] = []
    eski = g.ask or {}
    for alan, etiket, bicim in (
            ("izinler", "izin", perm_label),
            ("eylemler", "eylem", str),
            ("yollar", "yol", str)):
        onceki = set(eski.get(alan) or ())
        simdi = set(getattr(ask, {"izinler": "permissions",
                                  "eylemler": "actions",
                                  "yollar": "paths"}[alan]))
        for x in sorted(simdi - onceki):
            out.append(f"+ yeni {etiket}: {bicim(x)}")
        for x in sorted(onceki - simdi):
            out.append(f"- kalkan {etiket}: {bicim(x)}")
    if (eski.get("tetikleyici") or "") != ask.trigger:
        out.append(f"~ tetikleyici: {eski.get('tetikleyici') or '—'} "
                   f"→ {ask.trigger}")
    return out or ["(onay gerektiren bir alan değişmemiş görünüyor)"]
