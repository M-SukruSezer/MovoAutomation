"""Akış tip sistemi.

Blokların birbirine takılabilmesini sağlayan görünmez katman: her eylem
girdi/çıktı portlarını bir tiple beyan eder, akış yüklenirken bağlantılar
statik olarak doğrulanır. Böylece hatalı bir akış çalışmadan önce yakalanır.
"""

from __future__ import annotations

import re

#: ``list<resim>`` biçimindeki liste tipleri. Tip kafesi düz bir sözlük
#: olarak kaldı; listeler onun üstüne tek kurallı bir sarmalayıcı olarak
#: bindi: list<A> -> list<B> ancak A -> B ise. Değişmez değerler taşıdığı
#: için bu kovaryans güvenli.
LIST_RE = re.compile(r"^list<\s*(.+?)\s*>$")

# tip -> üst tip. Alt tip, üst tipin beklendiği her yere takılabilir.
PARENTS: dict[str, str | None] = {
    "any": None,
    "text": "any",
    "number": "any",
    "bool": "any",
    # Yol metnin altındadır: her yol geçerli bir metindir, tersi değil.
    # Böylece bir dosya adı metin portuna takılır ama rastgele metin
    # dosya portuna takılmaz -- tehlikeli olan yön kapalı kalır.
    "path": "text",
    "directory": "path",
    "file": "path",
    "image": "file",
    "audio": "file",
    "video": "file",
}

LABELS = {
    "any": "herhangi",
    "text": "metin",
    "number": "sayı",
    "bool": "doğru/yanlış",
    "path": "yol",
    "directory": "klasör",
    "file": "dosya",
    "image": "resim",
    "audio": "ses",
    "video": "video",
}


class TypeError_(Exception):
    """Tip uyuşmazlığı."""


def elem(t: str) -> str | None:
    """list<resim> -> 'resim'; liste değilse None. Yalın 'list' = list<any>."""
    if t == "list":
        return "any"
    m = LIST_RE.match(t or "")
    return m.group(1) if m else None


def listof(t: str) -> str:
    """resim -> 'list<resim>'. Döngü bir adımı T'den list<T>'ye yükseltir."""
    return f"list<{t}>"


def is_list(t: str) -> bool:
    return elem(t) is not None


def is_known(t: str) -> bool:
    e = elem(t)
    if e is not None:
        return is_known(e)
    return t in PARENTS


def ancestry(t: str) -> list[str]:
    """t ve tüm üst tipleri."""
    chain, cur = [], t
    while cur is not None:
        chain.append(cur)
        cur = PARENTS.get(cur)
    return chain


def is_assignable(src: str, dst: str) -> bool:
    """src tipindeki bir değer, dst bekleyen bir porta takılabilir mi?"""
    if dst == "any" or src == "any":
        return True
    se, de = elem(src), elem(dst)
    if se is not None and de is not None:
        return is_assignable(se, de)
    if se is not None or de is not None:
        return False        # liste ile tekil karışmaz; arada döngü vardır
    return dst in ancestry(src)


def label(t: str) -> str:
    e = elem(t)
    if e is not None:
        return f"{label(e)} listesi"
    return LABELS.get(t, t)


def coerce(value, t: str):
    """Kullanıcının yazdığı düz değeri porta uygun hale getirir."""
    e = elem(t)
    if e is not None:
        if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
            raise TypeError_(f"{label(t)} beklendi, {value!r} geldi")
        return [coerce(v, e) for v in value]
    if t == "number":
        if isinstance(value, bool):
            raise TypeError_("doğru/yanlış değeri sayı portuna verilemez")
        if isinstance(value, (int, float)):
            return value
        try:
            s = str(value).strip()
            return int(s) if s.lstrip("-").isdigit() else float(s)
        except ValueError as e:
            raise TypeError_(f"sayı beklendi, {value!r} geldi") from e
    if t == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "evet", "1", "yes", "on"):
            return True
        if s in ("false", "hayır", "hayir", "0", "no", "off"):
            return False
        raise TypeError_(f"doğru/yanlış beklendi, {value!r} geldi")
    if t in ("text",):
        return str(value)
    if t in ("path", "file", "directory", "image", "audio", "video"):
        return str(value)
    return value
