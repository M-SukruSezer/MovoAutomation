"""Koşullar: bir adımın çalışıp çalışmayacağına karar veren kapı.

Dallanmayı iki yoldan kurabilirdik. Biri tuvale ayrı bir "eğer" düğümü ve
veri kablolarından ayrı bir *denetim akışı* kablosu eklemek; diğeri her
adıma bir koşul takmak. İkincisini seçtim:

  * Veri akışı zaten adımların sırasını belirliyor (topolojik). İkinci bir
    kablo türü eklemek, aynı grafa ikinci bir anlam yüklerdi.
  * "Eğer" düğümünün iki çıkışı olurdu; bir çıkışa takılmamış dal sessizce
    ölürdü. Koşul adımın üstünde dururken, hangi adımın neden atlandığı
    doğrudan görünür.
  * Metin hâli diff'lenebilir kalıyor: ``when:`` tek satır.

``else`` diye bir şey yok: karşıt dal, karşıt koşulu yazarak kurulur. Bu
bir eksiklik değil, grafın tek anlamlı kalmasının bedeli -- ve ters koşul
her zaman yazılabilir (``<`` varsa ``>=`` de var).

Koşul biçimi::

    when: {left: "{{ pil.percent }}", op: "<", right: 20}
    when: "{{ dosya.var }}"                 # kısayol: doğru mu?
    when: [{...}, {...}]                    # hepsi birden
    when: {any: [{...}, {...}]}             # herhangi biri

Atlama zincirlemedir: atlanan bir adımın çıktısına dayanan adım da atlanır,
çünkü bağlanacağı değer hiç üretilmemiştir.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from . import types as T
from .flowref import refs_in, sole_ref

# --------------------------------------------------------------- işleçler


def _num(x, which: str):
    if isinstance(x, bool):
        raise ValueError(f"{which} tarafı doğru/yanlış; sayı karşılaştırması yapılamaz")
    try:
        return float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{which} tarafı sayıya dönüşmüyor: {x!r}") from None


def _bosmu(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (str, list, tuple, dict)):
        return len(v) == 0 or (isinstance(v, str) and not v.strip())
    return False


def _esit(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


#: işleç -> (etiket, operand sayısı, soldan beklenen tip, işlev)
OPS: dict[str, dict] = {
    "==": {"label": "eşit", "arity": 2, "accepts": "any",
           "fn": lambda a, b: _esit(a, b)},
    "!=": {"label": "farklı", "arity": 2, "accepts": "any",
           "fn": lambda a, b: not _esit(a, b)},
    "<": {"label": "küçük", "arity": 2, "accepts": "number",
          "fn": lambda a, b: _num(a, "sol") < _num(b, "sağ")},
    "<=": {"label": "küçük veya eşit", "arity": 2, "accepts": "number",
           "fn": lambda a, b: _num(a, "sol") <= _num(b, "sağ")},
    ">": {"label": "büyük", "arity": 2, "accepts": "number",
          "fn": lambda a, b: _num(a, "sol") > _num(b, "sağ")},
    ">=": {"label": "büyük veya eşit", "arity": 2, "accepts": "number",
           "fn": lambda a, b: _num(a, "sol") >= _num(b, "sağ")},
    "içerir": {"label": "içerir", "arity": 2, "accepts": "text",
               "fn": lambda a, b: str(b) in str(a)},
    "eşleşir": {"label": "desene uyar", "arity": 2, "accepts": "text",
                "fn": lambda a, b: fnmatch.fnmatch(str(a), str(b))},
    "boş": {"label": "boş", "arity": 1, "accepts": "any", "fn": _bosmu},
    "dolu": {"label": "dolu", "arity": 1, "accepts": "any",
             "fn": lambda a: not _bosmu(a)},
    "doğru": {"label": "doğru", "arity": 1, "accepts": "bool",
              "fn": lambda a: _as_bool(a)},
    "yanlış": {"label": "yanlış", "arity": 1, "accepts": "bool",
               "fn": lambda a: not _as_bool(a)},
}

#: elle yazarken İngilizce alışkanlığı da kabul edilir
ALIASES = {
    "eq": "==", "equals": "==", "ne": "!=", "lt": "<", "lte": "<=",
    "gt": ">", "gte": ">=", "contains": "içerir", "matches": "eşleşir",
    "empty": "boş", "not-empty": "dolu", "true": "doğru", "false": "yanlış",
    "dogru": "doğru", "yanlis": "yanlış", "bos": "boş", "icerir": "içerir",
    "eslesir": "eşleşir",
}


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    try:
        return bool(T.coerce(v, "bool"))
    except T.TypeError_:
        return bool(v)


def canonical(op: str) -> str:
    op = str(op).strip()
    return ALIASES.get(op.lower(), op)


# ------------------------------------------------------------------ model

@dataclass(frozen=True)
class Cond:
    op: str
    left: object
    right: object = None

    @property
    def unary(self) -> bool:
        return OPS[self.op]["arity"] == 1


@dataclass(frozen=True)
class Guard:
    mode: str                 # "all" | "any"
    conds: tuple[Cond, ...]


def _show(operand) -> str:
    """Operandı insan diline çevirir: '{{ a.b }}' → 'a.b'."""
    ref = sole_ref(operand)
    if ref:
        return ref
    if isinstance(operand, str):
        return f"'{operand}'" if operand else "''"
    return str(operand)


#: operandı insan diline çeviren genel ad (editör de kullanıyor)
show = _show


def describe(guard: Guard) -> str:
    parts = []
    for c in guard.conds:
        if c.unary:
            parts.append(f"{_show(c.left)} {OPS[c.op]['label']}")
        else:
            parts.append(f"{_show(c.left)} {c.op} {_show(c.right)}")
    joint = " ya da " if guard.mode == "any" else " ve "
    return joint.join(parts)


def refs(guard: Guard | None) -> list[str]:
    """Koşulun dayandığı {{ adim.cikti }} referansları."""
    if guard is None:
        return []
    out: list[str] = []
    for c in guard.conds:
        out += refs_in(c.left)
        if not c.unary:
            out += refs_in(c.right)
    return out


# ------------------------------------------------------------- ayrıştırma

def _mark(where: str, msg: str) -> str:
    return f"{where}: {msg}" if where else msg


def _one(raw, errors: list[str], where: str) -> Cond | None:
    # kısayol: tek referans ya da düz değer -> "doğru mu?"
    if not isinstance(raw, dict):
        if raw is None:
            errors.append(_mark(where, "koşul boş"))
            return None
        return Cond(op="doğru", left=raw)

    if "left" not in raw:
        errors.append(_mark(where, f"koşulda 'left' yok "
                                  f"(geleni: {', '.join(raw) or 'boş'})"))
        return None
    op = canonical(raw.get("op", "doğru"))
    if op not in OPS:
        errors.append(_mark(where, f"bilinmeyen işleç '{raw.get('op')}' "
                                  f"(seçenekler: {', '.join(OPS)})"))
        return None
    spec = OPS[op]
    has_right = "right" in raw
    if spec["arity"] == 1 and has_right:
        errors.append(_mark(where, f"'{op}' tek taraflı bir işleç, 'right' almaz"))
        return None
    if spec["arity"] == 2 and not has_right:
        errors.append(_mark(where, f"'{op}' iki taraflı; 'right' eksik"))
        return None
    extra = set(raw) - {"left", "op", "right"}
    if extra:
        errors.append(_mark(where, f"koşulda tanınmayan anahtar: "
                                  f"{', '.join(sorted(extra))}"))
        return None
    return Cond(op=op, left=raw["left"], right=raw.get("right"))


def parse(raw, where: str = "") -> tuple[Guard | None, list[str]]:
    """YAML'daki ``when:`` değerini Guard'a çevirir."""
    errors: list[str] = []
    if raw is None:
        return None, errors

    mode = "all"
    items = raw
    if isinstance(raw, dict) and ("any" in raw or "all" in raw):
        if set(raw) - {"any", "all"}:
            errors.append(_mark(where, "'any'/'all' tek başına kullanılır"))
            return None, errors
        if "any" in raw and "all" in raw:
            errors.append(_mark(where, "'any' ve 'all' birlikte yazılamaz"))
            return None, errors
        mode = "any" if "any" in raw else "all"
        items = raw.get("any", raw.get("all"))
        if not isinstance(items, list) or not items:
            errors.append(_mark(where, f"'{mode}' boş olmayan bir liste olmalı"))
            return None, errors
    if not isinstance(items, list):
        items = [items]

    conds = []
    for i, it in enumerate(items):
        w = where if len(items) == 1 else (
            f"{where}[{i + 1}]" if where else f"{i + 1}. koşul")
        c = _one(it, errors, w)
        if c is not None:
            conds.append(c)
    if errors or not conds:
        return None, errors
    return Guard(mode=mode, conds=tuple(conds)), errors


def dump(guard: Guard) -> object:
    """Guard'ı YAML'a geri yazılabilir en sade biçime çevirir."""
    def one(c: Cond):
        if c.unary and c.op == "doğru" and sole_ref(c.left):
            return c.left                       # kısayola geri dön
        d = {"left": c.left, "op": c.op}
        if not c.unary:
            d["right"] = c.right
        return d

    if guard.mode == "all" and len(guard.conds) == 1:
        return one(guard.conds[0])
    items = [one(c) for c in guard.conds]
    return items if guard.mode == "all" else {"any": items}


# ---------------------------------------------------------- statik denetim

def _literal_type(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return "text"


def check(guard: Guard | None, type_of) -> list[str]:
    """Tip denetimi. ``type_of(operand)`` operandın tipini ya da None döner."""
    if guard is None:
        return []
    out: list[str] = []
    for c in guard.conds:
        spec = OPS[c.op]
        lt = type_of(c.left)
        if lt is None:
            continue                      # referans hatası zaten raporlandı
        want = spec["accepts"]
        if want == "number" and not T.is_assignable(lt, "number"):
            out.append(f"'{c.op}' sayı karşılaştırması; sol taraf "
                       f"{T.label(lt)} ({_show(c.left)})")
            continue
        if want == "text" and not (T.is_assignable(lt, "text")
                                   or T.is_assignable(lt, "path")):
            out.append(f"'{c.op}' metin işleci; sol taraf {T.label(lt)} "
                       f"({_show(c.left)})")
            continue
        if want == "bool" and not T.is_assignable(lt, "bool"):
            out.append(f"'{c.op}' doğru/yanlış bekler; sol taraf "
                       f"{T.label(lt)} ({_show(c.left)}). "
                       f"Karşılaştırma için 'op: ==' kullanın.")
            continue
        if c.unary:
            continue
        rt = type_of(c.right)
        if rt is None:
            continue
        if want == "number" and not T.is_assignable(rt, "number"):
            out.append(f"'{c.op}' sağ tarafta sayı bekliyor, "
                       f"{T.label(rt)} geldi ({_show(c.right)})")
        elif want == "any" and not (T.is_assignable(lt, rt) or T.is_assignable(rt, lt)):
            out.append(f"{T.label(lt)} ile {T.label(rt)} karşılaştırılamaz "
                       f"({_show(c.left)} {c.op} {_show(c.right)})")
    return out


def literal_type(v) -> str:
    return _literal_type(v)


# ------------------------------------------------------------ değerlendirme

def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, str):
        return f"'{v}'" if len(v) <= 40 else f"'{v[:37]}…'"
    return str(v)


def _eval_one(c: Cond, resolve) -> tuple[bool, str]:
    spec = OPS[c.op]
    lv = resolve(c.left)
    shown = _show(c.left)
    seen = f"{shown} ({_fmt(lv)})" if sole_ref(c.left) else _fmt(lv)
    if c.unary:
        ok = bool(spec["fn"](lv))
        return ok, f"{seen} {spec['label']}" + ("" if ok else " değil")
    rv = resolve(c.right)
    right = f"{_show(c.right)} ({_fmt(rv)})" if sole_ref(c.right) else _fmt(rv)
    ok = bool(spec["fn"](lv, rv))
    return ok, f"{seen} {c.op} {right}" + ("" if ok else " değil")


def evaluate(guard: Guard, resolve) -> tuple[bool, str]:
    """(karar, insan okunur gerekçe). ``resolve(operand)`` değeri verir."""
    results = [_eval_one(c, resolve) for c in guard.conds]
    if guard.mode == "any":
        ok = any(r for r, _ in results)
        if ok:
            return True, next(w for r, w in results if r)
        return False, " ya da ".join(w for _, w in results)
    ok = all(r for r, _ in results)
    if ok:
        return True, " ve ".join(w for _, w in results)
    return False, next(w for r, w in results if not r)
