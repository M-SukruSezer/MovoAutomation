"""Akış modeli: deklaratif biçim + statik doğrulama.

Akış dosyası görsel editörden bağımsızdır: diff'lenebilir, paylaşılabilir,
git'e girer. Editör ile metin hali aynı şeyin iki görünümüdür.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import cond as C
from . import types as T
from .flowref import REF, refs_in, sole_ref       # noqa: F401  (yeniden ihraç)
from .manifest import ManifestError, Port
from .registry import Registry

# Tetikleyiciler: her biri akışa hangi değerleri verdiğini beyan eder.
TRIGGERS: dict[str, dict] = {
    "manual": {
        "label": "Elle çalıştır",
        "config": {},
        "optional": {"file": "file", "produces": "text"},
        "outputs": {"file": "file", "filename": "text"},
    },
    "folder": {
        "label": "Klasör izle",
        "config": {"path": "directory"},
        "optional": {"match": "text", "events": "text", "settle_ms": "number",
                     "ignore_existing": "bool", "produces": "text"},
        "outputs": {"file": "file", "filename": "text", "directory": "directory"},
    },
    "schedule": {
        "label": "Zamanlanmış",
        "config": {"on_calendar": "text"},
        "optional": {"persistent": "bool"},
        "outputs": {"time": "text"},
    },
    "network": {
        "label": "Ağ olayı",
        "config": {"connection": "text"},
        "optional": {"state": "text"},
        "outputs": {"connection": "text", "state": "text", "interface": "text"},
    },
    "dbus": {
        "label": "D-Bus sinyali",
        "config": {"interface": "text"},
        "optional": {"bus": "text", "service": "text", "object": "text",
                     "member": "text", "property": "text", "args": "text",
                     "produces": "text", "settle_ms": "number"},
        # Çıktılar sabit değil: hangi sinyali dinlediğine bağlı. Akış
        # argümanlara ad ve tip verir, motor da onları port yapar.
        "outputs": {},
        "dynamic": lambda conf: dbus_trigger_outputs(conf),
        "check": lambda conf, problems: _check_dbus_trigger(conf, problems),
    },
}


def dbus_trigger_outputs(conf: dict) -> dict[str, str]:
    """D-Bus tetikleyicisinin akışa verdiği değerler."""
    if conf.get("property"):
        return {
            "value": str(conf.get("produces") or "text"),
            "property": "text",
            "interface": "text",
            "object": "text",
        }
    out: dict[str, str] = {}
    args = conf.get("args")
    if isinstance(args, dict):
        for name, t in args.items():
            out[str(name)] = str(t)
    out.setdefault("member", "text")
    out.setdefault("object", "text")
    return out


def _check_dbus_trigger(conf: dict, problems: list) -> None:
    bus = str(conf.get("bus", "session"))
    if bus not in ("session", "system"):
        problems.append(Problem("hata", "trigger.bus",
                                f"'session' veya 'system' olmalı ('{bus}' geldi)"))
    member, prop = conf.get("member"), conf.get("property")
    if bool(member) == bool(prop):
        problems.append(Problem(
            "hata", "trigger",
            "'member' (sinyal) ya da 'property' (özellik değişimi) "
            "taşımalı; ikisi birden ya da hiçbiri olmaz"))
    if prop:
        if not conf.get("object"):
            problems.append(Problem(
                "hata", "trigger.object",
                "özellik dinlemek için nesne yolu zorunlu"))
        pt = str(conf.get("produces") or "text")
        if not T.is_known(pt):
            problems.append(Problem("hata", "trigger.produces",
                                    f"bilinmeyen tip '{pt}'"))
        if conf.get("args"):
            problems.append(Problem("uyarı", "trigger.args",
                                    "özellik modunda kullanılmaz"))
        return
    if "produces" in conf:
        problems.append(Problem("uyarı", "trigger.produces",
                                "yalnızca özellik modunda anlamlı"))
    args = conf.get("args")
    if args is None:
        return
    if not isinstance(args, dict):
        problems.append(Problem(
            "hata", "trigger.args",
            "sinyal argümanlarını sırayla adlandıran bir eşleme olmalı, "
            "örn. {id: number, reason: number}"))
        return
    for name, t in args.items():
        if not T.is_known(str(t)):
            problems.append(Problem("hata", f"trigger.args.{name}",
                                    f"bilinmeyen tip '{t}'"))
        if str(name) in ("member", "object"):
            problems.append(Problem(
                "uyarı", f"trigger.args.{name}",
                "bu ad tetikleyicinin kendi çıktısını gölgeliyor"))


@dataclass
class Problem:
    level: str  # "hata" | "uyarı"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():5} {self.where}: {self.message}"


ITEM = "item"          # döngü değişkeni: {{ item.value }}, {{ item.index }}

#: Bir adım patlayınca ne olacak?
#:   dur   — akış durur (varsayılan)
#:   devam — hata yakalanır, akış sürer; adım 'tamam' ve 'hata' portları verir
#:   atla  — yalnızca döngüde: patlayan öge atlanır, adım başarılı sayılır
ON_ERROR = ("dur", "devam", "atla")

#: Yakalayan adımın kazandığı portlar. Adları motorun kendi durum
#: sözlüğünden ('tamam', 'hata', 'atlandı') geliyor ve hiçbir eylemin
#: çıktısıyla çakışmıyor.
CATCH_PORTS = {"tamam": "bool", "hata": "text"}


@dataclass
class Step:
    id: str
    action: str
    args: dict
    title: str = ""
    guard: "C.Guard | None" = None      # when: — adımın çalışma koşulu
    each: object = None                 # for_each: — döngü kaynağı
    elem_type: str = ""                 # döngü ögesinin tipi
    on_error: str = "dur"               # döngüde bir öge patlarsa
    isolated: bool = False              # isolate: çekirdek düzeyinde kısıtla
    workers: object = 1                 # döngüde kaç öge aynı anda

    @property
    def conditional(self) -> bool:
        return self.guard is not None

    @property
    def loops(self) -> bool:
        return self.each is not None

    @property
    def isolate_asked(self) -> bool:
        return self.isolated

    @property
    def parallel(self) -> bool:
        return self.workers != 1

    @property
    def catches(self) -> bool:
        """Hatasını yakalayıp akışa devam ettiriyor mu?"""
        return self.on_error == "devam"


@dataclass
class Flow:
    name: str
    slug: str
    trigger_type: str
    trigger: dict
    steps: list[Step]
    description: str = ""
    enabled: bool = True
    isolate: bool = False
    source: Path | None = None
    problems: list[Problem] = field(default_factory=list)
    # statik çözümleme sonucu: (step_id, port) -> tip
    port_types: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(p.level == "hata" for p in self.problems)

    def trigger_outputs(self) -> dict[str, str]:
        spec = TRIGGERS.get(self.trigger_type)
        if not spec:
            return {}
        if "dynamic" in spec:
            return spec["dynamic"](self.trigger)
        return dict(spec["outputs"])

    def permissions(self, reg: Registry) -> dict[str, list[str]]:
        """izin -> onu isteyen eylemler."""
        out: dict[str, list[str]] = {}
        for s in self.steps:
            if s.action in reg:
                for p in reg.get(s.action).permissions:
                    out.setdefault(p, []).append(s.id)
        return dict(sorted(out.items()))


def slugify(name: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = name.translate(tr).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "movo"


def load(path: Path, reg: Registry) -> Flow:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flow = _build(raw, reg, source=path)
    return flow


def _build(raw: dict, reg: Registry, source: Path | None = None) -> Flow:
    problems: list[Problem] = []
    name = str(raw.get("name") or (source.stem if source else "adsız akış"))
    if not raw.get("name"):
        problems.append(Problem("uyarı", "akış", "'name' yok, dosya adı kullanıldı"))

    trg = raw.get("trigger") or {}
    if not isinstance(trg, dict):
        problems.append(Problem("hata", "trigger", "bir sözlük olmalı"))
        trg = {}
    ttype = str(trg.get("type", "manual"))
    if ttype not in TRIGGERS:
        problems.append(Problem(
            "hata", "trigger",
            f"bilinmeyen tetikleyici '{ttype}' (seçenekler: {', '.join(TRIGGERS)})"))
        ttype = "manual"
    spec = TRIGGERS[ttype]
    tconf = {k: v for k, v in trg.items() if k != "type"}
    for key in spec["config"]:
        if key not in tconf:
            problems.append(Problem("hata", f"trigger.{key}", "zorunlu alan eksik"))
    for key in tconf:
        if key not in spec["config"] and key not in spec["optional"]:
            problems.append(Problem(
                "uyarı", f"trigger.{key}", f"'{ttype}' tetikleyicisinde böyle bir alan yok"))

    if "check" in spec:
        spec["check"](tconf, problems)

    steps: list[Step] = []
    port_types: dict[tuple[str, str], str] = {}
    outs = spec["dynamic"](tconf) if "dynamic" in spec else spec["outputs"]
    for oname, otype in outs.items():
        port_types[("trigger", oname)] = otype if T.is_known(str(otype)) else "text"

    produces = tconf.get("produces")
    if produces is not None and "file" in spec["outputs"]:
        pt = str(produces)
        if not T.is_known(pt):
            problems.append(Problem("hata", "trigger.produces", f"bilinmeyen tip '{pt}'"))
        elif not T.is_assignable(pt, "file"):
            problems.append(Problem(
                "hata", "trigger.produces",
                f"'{pt}' bir dosya tipi değil (örn. image, audio, video)"))
        else:
            port_types[("trigger", "file")] = pt

    raw_steps = raw.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        problems.append(Problem("hata", "steps", "en az bir adım gerekli"))
        raw_steps = []

    seen: set[str] = set()
    for i, rs in enumerate(raw_steps, 1):
        where = f"steps[{i}]"
        if not isinstance(rs, dict):
            problems.append(Problem("hata", where, "bir sözlük olmalı"))
            continue
        sid = str(rs.get("id") or f"adim{i}")
        if sid == "trigger":
            problems.append(Problem("hata", where, "'trigger' ayrılmış bir ad"))
        if sid in seen:
            problems.append(Problem("hata", where, f"'{sid}' adımı iki kez tanımlı"))
        seen.add(sid)
        action_id = str(rs.get("action", ""))
        args = rs.get("with") or {}
        if not isinstance(args, dict):
            problems.append(Problem("hata", f"{where}.with", "bir sözlük olmalı"))
            args = {}
        step = Step(id=sid, action=action_id, args=args, title=str(rs.get("title", "")))
        steps.append(step)

        # Koşulun *biçimi* eylemden bağımsız; tipleri aşağıda, döngü ögesinin
        # tipi bilindikten sonra denetlenecek.
        guard, errs = C.parse(rs.get("when"))
        for e in errs:
            problems.append(Problem("hata", f"{sid}.when", e))
        step.guard = guard

        # Yalıtım adımda açıkça ya da akış genelinde istenebilir.
        step.isolated = bool(rs.get("isolate", raw.get("isolate", False)))

        step.on_error = str(rs.get("on_error", "dur"))
        if step.on_error not in ON_ERROR:
            problems.append(Problem(
                "hata", f"{sid}.on_error",
                f"'{step.on_error}' geçerli değil (seçenekler: {', '.join(ON_ERROR)})"))
            step.on_error = "dur"

        # paralellik: yalnızca döngüde anlamlı
        if "workers" in rs:
            w = rs["workers"]
            if isinstance(w, str) and w.strip().lower() == "auto":
                step.workers = "auto"
            else:
                try:
                    n = int(w)
                except (TypeError, ValueError):
                    n = 0
                if n < 1:
                    problems.append(Problem(
                        "hata", f"{sid}.workers",
                        f"1 veya daha büyük bir sayı ya da 'auto' olmalı "
                        f"('{w}' geldi)"))
                else:
                    step.workers = n

        # döngü kaynağı
        step.each = rs.get("for_each")
        if step.each is not None:
            step.elem_type = _each_type(step.each, port_types,
                                        f"{sid}.for_each", problems, sid)
        elif step.workers != 1:
            problems.append(Problem(
                "hata", f"{sid}.workers",
                "paralellik ögeler arasındadır; for_each olmayan adımda "
                "karşılığı yok"))
            step.workers = 1
        if step.each is not None and step.workers != 1 and step.on_error == "dur":
            problems.append(Problem(
                "uyarı", f"{sid}.workers",
                "paralel çalışırken 'on_error: dur' hatadan önce başlamış "
                "ögeleri durduramaz; tamamlanırlar"))
        if step.each is None and step.on_error == "atla":
            problems.append(Problem(
                "hata", f"{sid}.on_error",
                "'atla' bir ögeyi atlamak demek; döngüsüz adımda karşılığı yok "
                "(hatayı yakalayıp devam etmek için 'devam' kullanın)"))
            step.on_error = "dur"

        if action_id not in reg:
            try:
                reg.get(action_id)
            except ManifestError as e:
                problems.append(Problem("hata", f"{where} ({sid})", str(e)))
            continue
        act = reg.get(action_id)

        # Döngü adımı T -> U eylemini list<T> -> list<U> haline getirir;
        # çıktı tipleri burada yükseltiliyor ve tüm akış bunu görüyor.
        for oname, port in act.outputs.items():
            base = _narrow(port, act, args, sid, oname, problems)
            port_types[(sid, oname)] = (T.listof(base) if step.loops else base)

        # Hatayı yakalayan adım iki port kazanıyor. Yalnızca yakalayanda
        # var: 'dur' ilkesindeki bir adımın 'tamam'ı her zaman doğru
        # olurdu, yani yanıltıcı bir port olurdu.
        if step.catches:
            for pname, ptype in CATCH_PORTS.items():
                if pname in act.outputs:
                    problems.append(Problem(
                        "hata", f"{sid}.on_error",
                        f"'{act.id}' zaten '{pname}' adlı bir çıktı veriyor; "
                        f"bu eylem hata yakalayamaz"))
                    continue
                port_types[(sid, pname)] = ptype

        # döngü değişkeni yalnızca bu adımın kapsamında görünür
        if step.loops and step.elem_type:
            port_types[(ITEM, "value")] = step.elem_type
            port_types[(ITEM, "index")] = "number"

        # girdi doğrulaması
        for key, value in args.items():
            if key not in act.inputs:
                problems.append(Problem(
                    "hata", f"{sid}.{key}",
                    f"'{act.id}' eyleminin böyle bir girdisi yok "
                    f"(mevcut: {', '.join(act.inputs) or 'yok'})"))
                continue
            port = act.inputs[key]
            _check_value(value, port, sid, key, port_types, problems)

        for key, port in act.inputs.items():
            if port.required and key not in args:
                problems.append(Problem(
                    "hata", f"{sid}.{key}",
                    f"zorunlu girdi eksik ({T.label(port.type)})"))

        if guard is not None:
            def type_of(operand, _sid=sid):
                return _operand_type(operand, port_types, f"{_sid}.when",
                                     problems, _sid)
            for msg in C.check(guard, type_of):
                problems.append(Problem("hata", f"{sid}.when", msg))

        port_types.pop((ITEM, "value"), None)
        port_types.pop((ITEM, "index"), None)

    if any(st.isolated for st in steps):
        from . import isolate as _iso
        good, why = _iso.available()
        if not good:
            problems.append(Problem(
                "uyarı", "isolate",
                f"yalıtım isteniyor ama bu çekirdekte kurulamaz ({why}); "
                f"adımlar yalıtımsız çalışır"))

    # Yakalanan ama kimsenin bakmadığı hata sessizce yutulur: bunu
    # söylemek, hatanın kendisini bulmaktan ucuz.
    watched: set[str] = set()
    for st in steps:
        for ref in _all_refs(st):
            src, _, port = ref.partition(".")
            if port in CATCH_PORTS:
                watched.add(src)
    for st in steps:
        if st.catches and st.id not in watched:
            problems.append(Problem(
                "uyarı", f"{st.id}.on_error",
                f"hata yakalanıyor ama kimse bakmıyor; bir adım "
                f"{{{{ {st.id}.tamam }}}} ya da {{{{ {st.id}.hata }}}} okumalı"))

    flow = Flow(
        name=name,
        slug=slugify(str(raw.get("slug") or name)),
        trigger_type=ttype,
        trigger=tconf,
        steps=steps,
        description=str(raw.get("description", "")),
        enabled=bool(raw.get("enabled", True)),
        isolate=bool(raw.get("isolate", False)),
        source=source,
        problems=problems,
        port_types=port_types,
    )
    return flow


def _all_refs(step: Step) -> list[str]:
    """Adımın dayandığı bütün referanslar: girdiler, koşul, döngü kaynağı."""
    out: list[str] = []
    for raw in step.args.values():
        out += refs_in(raw)
    out += C.refs(step.guard)
    if not isinstance(step.each, list):
        out += refs_in(step.each)
    return out


def _narrow(port: Port, act, args: dict, sid: str, oname: str,
            problems: list[Problem]) -> str:
    """Çıktı tipini, manifestin işaret ettiği girdiyle daraltır.

    Daraltma bir *iddia*dır, ölçüm değil: "bu klasörde resim var" diyen
    kullanıcıdır. İddia yanlışsa tüketen eylem patlar; döngüde
    ``on_error: atla`` ile tek bozuk dosya akışı durdurmaz.
    """
    if not port.narrowed_by:
        return port.type
    src = act.inputs.get(port.narrowed_by)
    raw = args.get(port.narrowed_by, src.default if src else None)
    if raw is None or refs_in(raw):
        return port.type                  # kabloyla besleniyorsa daraltma yok
    nt = str(raw)
    inner = T.elem(port.type) or port.type
    where = f"{sid}.{port.narrowed_by}"
    if not T.is_known(nt):
        problems.append(Problem("hata", where, f"bilinmeyen tip '{nt}'"))
        return port.type
    if not T.is_assignable(nt, inner):
        problems.append(Problem(
            "hata", where,
            f"'{nt}' bir {T.label(inner)} değil; '{oname}' çıktısı daraltılamaz"))
        return port.type
    return T.listof(nt) if T.is_list(port.type) else nt


def _each_type(each, port_types: dict, where: str,
               problems: list[Problem], self_id: str) -> str:
    """for_each kaynağının öge tipi. Liste değilse hata."""
    if isinstance(each, list):
        if not each:
            problems.append(Problem("hata", where, "döngü listesi boş"))
            return ""
        kinds = {C.literal_type(v) for v in each}
        return kinds.pop() if len(kinds) == 1 else "text"
    ref = sole_ref(each)
    if not ref:
        problems.append(Problem(
            "hata", where,
            "tek bir {{ adim.cikti }} ya da düz bir liste olmalı"))
        return ""
    t = _ref_type(ref, port_types, where, problems, self_id)
    if t is None:
        return ""
    e = T.elem(t)
    if e is None:
        problems.append(Problem(
            "hata", where,
            f"döngü kaynağı bir liste olmalı; {{{{ {ref} }}}} bir "
            f"{T.label(t)} veriyor"))
        return ""
    return e


def _check_value(value, port: Port, sid: str, key: str,
                 port_types: dict, problems: list[Problem]) -> None:
    where = f"{sid}.{key}"
    ref = sole_ref(value)
    if ref:
        src_type = _ref_type(ref, port_types, where, problems, sid)
        if src_type and not T.is_assignable(src_type, port.type):
            hint = ""
            if T.is_list(src_type) and T.is_assignable(T.elem(src_type), port.type):
                hint = ("; her öge için ayrı çalıştırmak istiyorsanız adıma "
                        f"for_each: \"{{{{ {ref} }}}}\" ekleyip burada "
                        "{{ item.value }} kullanın")
            problems.append(Problem(
                "hata", where,
                f"{{{{ {ref} }}}} bir {T.label(src_type)} veriyor, "
                f"bu port {T.label(port.type)} bekliyor{hint}"))
        return

    found = refs_in(value)
    if found:
        for r in found:
            _ref_type(r, port_types, where, problems, sid)
        if not T.is_assignable("text", port.type):
            problems.append(Problem(
                "hata", where,
                f"metin içine gömülü referans {T.label(port.type)} portuna verilemez; "
                f"tek başına {{{{ {found[0]} }}}} yazın"))
        return

    try:
        coerced = T.coerce(value, port.type)
    except T.TypeError_ as e:
        problems.append(Problem("hata", where, str(e)))
        return
    if port.choices and str(coerced) not in [str(c) for c in port.choices]:
        problems.append(Problem(
            "hata", where,
            f"'{coerced}' geçerli değil (seçenekler: {', '.join(map(str, port.choices))})"))


def _operand_type(operand, port_types: dict, where: str,
                  problems: list[Problem], self_id: str) -> str | None:
    """Koşul operandının tipi: referans, gömülü referanslı metin, ya da düz değer."""
    ref = sole_ref(operand)
    if ref:
        return _ref_type(ref, port_types, where, problems, self_id)
    found = refs_in(operand)
    if found:
        for r in found:
            _ref_type(r, port_types, where, problems, self_id)
        return "text"
    return C.literal_type(operand)


def _ref_type(ref: str, port_types: dict, where: str, problems: list[Problem],
              self_id: str = "") -> str | None:
    if "." not in ref:
        problems.append(Problem(
            "hata", where,
            f"'{{{{ {ref} }}}}' eksik; 'adim.cikti' biçiminde olmalı"))
        return None
    src, _, out = ref.partition(".")
    if src == self_id:
        problems.append(Problem(
            "hata", where, f"'{src}' kendi çıktısına dayanamaz"))
        return None
    known_steps = {s for s, _ in port_types}
    if src not in known_steps:
        if src == ITEM:
            problems.append(Problem(
                "hata", where,
                "{{ item.* }} yalnızca for_each kullanan adımın içinde geçerli"))
            return None
        problems.append(Problem(
            "hata", where,
            f"'{src}' diye bir önceki adım yok (tanımlı: {', '.join(sorted(known_steps))})"))
        return None
    if (src, out) not in port_types:
        outs = sorted(o for s, o in port_types if s == src)
        problems.append(Problem(
            "hata", where,
            f"'{src}' adımının '{out}' çıktısı yok (çıktıları: {', '.join(outs)})"))
        return None
    return port_types[(src, out)]
