"""Referans sözdizimi: ``{{ adim.cikti }}``.

Kendi dosyasında, çünkü hem akış modeli hem koşullar hem de editör aynı
sözdizimini kullanıyor; flow.py içinde kalsaydı cond.py ile döngüsel içe
aktarma olurdu.
"""

from __future__ import annotations

import re

REF = re.compile(r"\{\{\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*\}\}")


def refs_in(value) -> list[str]:
    """Metnin içindeki tüm referanslar (düz değerlerde boş liste)."""
    return REF.findall(value) if isinstance(value, str) else []


def sole_ref(value):
    """Değer tek başına bir referanssa adını, değilse None döner."""
    if not isinstance(value, str):
        return None
    m = REF.fullmatch(value.strip())
    return m.group(1) if m else None
