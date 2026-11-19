"""Editörün belge modeli.

Tek doğruluk kaynağı akış sözlüğünün kendisidir; tuvaldeki her işlem onu
düzenler ve akış anında yeniden doğrulanır. Böylece "editör görünümü" ile
"YAML görünümü" aynı şeyin iki yüzü olur, biri diğerinin ihracı değil.

Yerleşim (düğüm koordinatları) ayrı bir ``layout:`` anahtarında durur; motor
onu görmezden gelir, yani elle yazılmış bir akış editörde de açılır.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .. import cond as C
from .. import types as T
from ..flow import (CATCH_PORTS, REF, TRIGGERS, Flow, _build, refs_in, slugify,
                    sole_ref)
from ..registry import Registry

TRIGGER = "trigger"
WHEN = "when"          # koşul portunun adı; gerçek bir eylem girdisi değil
EACH = "for_each"      # döngü kaynağı portu


def raw_refs(value) -> list[str]:
    """İç içe bir YAML değerindeki tüm {{ referans }}'ları toplar.

    ``when:`` birkaç biçimde yazılabildiği için (kısayol, sözlük, liste,
    any/all) yapıyı tanımak yerine içindeki metinleri tarıyoruz.
    """
    if isinstance(value, str):
        return refs_in(value)
    if isinstance(value, dict):
        return [r for v in value.values() for r in raw_refs(v)]
    if isinstance(value, (list, tuple)):
        return [r for v in value for r in raw_refs(v)]
    return []


def rename_refs(value, old: str, new: str):
    """Yapıyı bozmadan referanslardaki adım adını değiştirir."""
    if isinstance(value, str):
        return REF.sub(
            lambda m: "{{ " + (new + m.group(1)[len(old):]
                               if m.group(1).partition(".")[0] == old
                               else m.group(1)) + " }}",
            value)
    if isinstance(value, dict):
        return {k: rename_refs(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_refs(v, old, new) for v in value]
    return value


@dataclass
class Node:
    id: str
    kind: str                      # "trigger" | "step"
    action: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 232.0
    h: float = 0.0                 # çizim sırasında hesaplanır
    inputs: dict = field(default_factory=dict)    # ad -> Port
    outputs: dict = field(default_factory=dict)   # ad -> Port
    title: str = ""
    subtitle: str = ""
    guard: object = None           # cond.Guard | None
    each: object = None            # for_each kaynağı (ham)
    elem_type: str = ""            # döngü ögesinin tipi
    catches: bool = False          # on_error: devam
    isolated: bool = False         # isolate: çekirdek kısıtlaması


class Doc:
    """Düzenlenebilir akış belgesi."""

    def __init__(self, reg: Registry, raw: dict | None = None, path: Path | None = None):
        self.reg = reg
        self.path = path
        self.raw: dict = raw if raw is not None else self._blank()
        self.raw.setdefault("steps", [])
        self.dirty = False
        self._undo: list[str] = []
        self.flow: Flow = self.validate()

    # ------------------------------------------------------------ yükle/yaz

    @staticmethod
    def _blank() -> dict:
        return {
            "name": "Yeni akış",
            "trigger": {"type": "manual"},
            "steps": [],
        }

    @classmethod
    def open(cls, reg: Registry, path: Path) -> "Doc":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(reg, raw, Path(path))

    def to_yaml(self) -> str:
        out = {k: v for k, v in self.raw.items() if k != "layout"}
        buf = io.StringIO()
        yaml.safe_dump(out, buf, allow_unicode=True, sort_keys=False, width=100)
        if self.raw.get("layout"):
            # elle yazılır: blok biçimli belgeye akış biçimli bir eşleme
            # eklenemez, satır kaydırma da koordinatları bozar
            buf.write("\n# düğüm konumları; motor bu bölümü görmezden gelir\nlayout:\n")
            for node_id, (x, y) in sorted(self.raw["layout"].items()):
                buf.write(f"  {node_id}: [{x}, {y}]\n")
        return buf.getvalue()

    def save(self, path: Path | None = None) -> Path:
        if not self.raw.get("layout"):
            self.nodes()          # konumları üret, ki açıldığında aynı dursun
        target = Path(path or self.path or f"{slugify(self.name)}.yaml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_yaml(), encoding="utf-8")
        self.path = target
        self.dirty = False
        return target

    # ------------------------------------------------------------ doğrulama

    def validate(self) -> Flow:
        self.flow = _build(self.raw, self.reg, source=self.path)
        return self.flow

    def _touch(self) -> None:
        self.dirty = True
        self.validate()

    def snapshot(self) -> None:
        """Geri alma için belgenin halini saklar."""
        self._undo.append(yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=False))
        del self._undo[:-40]

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.raw = yaml.safe_load(self._undo.pop())
        self._touch()
        return True

    # ---------------------------------------------------------------- üstbilgi

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "Adsız akış")

    @name.setter
    def name(self, value: str) -> None:
        self.snapshot()
        self.raw["name"] = value
        self._touch()

    @property
    def trigger_type(self) -> str:
        return str((self.raw.get("trigger") or {}).get("type", "manual"))

    def set_trigger(self, ttype: str, conf: dict | None = None) -> None:
        self.snapshot()
        keep = {k: v for k, v in (self.raw.get("trigger") or {}).items() if k != "type"}
        spec = TRIGGERS.get(ttype, {"config": {}, "optional": {}})
        allowed = set(spec["config"]) | set(spec["optional"])
        new = {"type": ttype}
        new.update({k: v for k, v in keep.items() if k in allowed})
        new.update(conf or {})
        self.raw["trigger"] = new
        self._touch()

    def set_trigger_field(self, key: str, value) -> None:
        self.snapshot()
        trg = self.raw.setdefault("trigger", {"type": "manual"})
        if value in ("", None):
            trg.pop(key, None)
        else:
            trg[key] = value
        self._touch()

    # ----------------------------------------------------------------- adımlar

    def steps(self) -> list[dict]:
        return self.raw.setdefault("steps", [])

    def step(self, node_id: str) -> dict | None:
        return next((s for s in self.steps() if str(s.get("id")) == node_id), None)

    def index_of(self, node_id: str) -> int:
        for i, s in enumerate(self.steps()):
            if str(s.get("id")) == node_id:
                return i
        return -1

    def _fresh_id(self, action_id: str) -> str:
        base = slugify(action_id.split(".")[-1]) or "adim"
        used = {str(s.get("id")) for s in self.steps()} | {TRIGGER}
        if base not in used:
            return base
        i = 2
        while f"{base}{i}" in used:
            i += 1
        return f"{base}{i}"

    def add_step(self, action_id: str, x: float = 0, y: float = 0) -> str:
        act = self.reg.get(action_id)
        self.snapshot()
        sid = self._fresh_id(action_id)
        args: dict = {}
        for key, port in act.inputs.items():
            if port.default is not None:
                args[key] = port.default
            elif port.required and port.type in ("text", "number", "bool"):
                args[key] = "" if port.type == "text" else (0 if port.type == "number" else False)
        self.steps().append({"id": sid, "action": action_id, "with": args})
        self.set_pos(sid, x, y)
        self._touch()
        return sid

    def remove_step(self, node_id: str) -> None:
        if node_id == TRIGGER:
            return
        self.snapshot()
        self.raw["steps"] = [s for s in self.steps() if str(s.get("id")) != node_id]
        # bu adıma giden kabloları kopar -- koşul kabloları dahil
        for s in self.steps():
            for key, value in list((s.get("with") or {}).items()):
                if any(r.partition(".")[0] == node_id for r in refs_in(value)):
                    (s["with"]).pop(key, None)
            if any(r.partition(".")[0] == node_id for r in raw_refs(s.get(WHEN))):
                s.pop(WHEN, None)
            if any(r.partition(".")[0] == node_id for r in raw_refs(s.get(EACH))):
                s.pop(EACH, None)
                s.pop("on_error", None)
        (self.raw.get("layout") or {}).pop(node_id, None)
        self._touch()

    def rename_step(self, node_id: str, new_id: str) -> str | None:
        new_id = slugify(new_id)
        if not new_id or new_id == node_id:
            return None
        if new_id == TRIGGER or self.step(new_id):
            return f"'{new_id}' adı kullanılıyor"
        self.snapshot()
        self.step(node_id)["id"] = new_id
        for s in self.steps():
            for key, value in list((s.get("with") or {}).items()):
                if isinstance(value, str):
                    s["with"][key] = rename_refs(value, node_id, new_id)
            for key in (WHEN, EACH):
                if key in s:
                    s[key] = rename_refs(s[key], node_id, new_id)
        layout = self.raw.get("layout") or {}
        if node_id in layout:
            layout[new_id] = layout.pop(node_id)
        self._touch()
        return None

    # ------------------------------------------------------------- bağlantılar

    def set_value(self, node_id: str, key: str, value) -> None:
        step = self.step(node_id)
        if step is None:
            return
        self.snapshot()
        step.setdefault("with", {})[key] = value
        self._touch()

    def clear_value(self, node_id: str, key: str) -> None:
        step = self.step(node_id)
        if step is None:
            return
        if key == WHEN:
            return self.clear_guard(node_id)
        if key == EACH:
            return self.clear_each(node_id)
        self.snapshot()
        (step.get("with") or {}).pop(key, None)
        self._touch()

    # ------------------------------------------------------------------ koşul

    def guard(self, node_id: str):
        """Adımın doğrulanmış koşulu (cond.Guard) ya da None."""
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return st.guard if st else None

    def single_cond(self, node_id: str):
        """Panelden düzenlenebilen tek koşul; çok koşullu ise None."""
        g = self.guard(node_id)
        if g is None or len(g.conds) != 1:
            return None
        return g.conds[0]

    def set_guard(self, node_id: str, guard) -> None:
        step = self.step(node_id)
        if step is None:
            return
        self.snapshot()
        if guard is None:
            step.pop(WHEN, None)
        else:
            step[WHEN] = C.dump(guard)
        self._touch()

    def clear_guard(self, node_id: str) -> None:
        self.set_guard(node_id, None)

    # ------------------------------------------------------------------ döngü

    def each_of(self, node_id: str):
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return st.each if st else None

    def elem_type(self, node_id: str) -> str:
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return st.elem_type if st else ""

    def clear_each(self, node_id: str) -> None:
        step = self.step(node_id)
        if step is None or EACH not in step:
            return
        self.snapshot()
        step.pop(EACH, None)
        if step.get("on_error") == "atla":    # 'atla' döngüsüz anlamsız
            step.pop("on_error", None)
        self._touch()

    def catches(self, node_id: str) -> bool:
        return self.on_error(node_id) == "devam"

    # ----------------------------------------------------------------- yalıtım

    def isolated(self, node_id: str) -> bool:
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return bool(st.isolated) if st else False

    def set_isolated(self, node_id: str, value: bool) -> None:
        """Adıma yalıtım anahtarı yazar.

        Akış genelinde ``isolate: true`` varsa adımdaki açık ``false``
        onu geçersiz kılar -- tek bir adımı korumasız bırakmak istemek
        meşru bir ihtiyaç (ör. ağ gerektiren bir eylem).
        """
        step = self.step(node_id)
        if step is None:
            return
        self.snapshot()
        movo_geneli = bool(self.raw.get("isolate", False))
        if value == movo_geneli:
            step.pop("isolate", None)
        else:
            step["isolate"] = value
        self._touch()

    def workers(self, node_id: str):
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return st.workers if st else 1

    def set_workers(self, node_id: str, value) -> None:
        step = self.step(node_id)
        if step is None or EACH not in step:
            return
        self.snapshot()
        if value in (1, "1"):
            step.pop("workers", None)      # varsayılan yazılmaz
        else:
            step["workers"] = value
        self._touch()

    def on_error(self, node_id: str) -> str:
        st = next((s for s in self.flow.steps if s.id == node_id), None)
        return st.on_error if st else "dur"

    def set_on_error(self, node_id: str, value: str) -> None:
        step = self.step(node_id)
        if step is None:
            return
        self.snapshot()
        if value == "dur":
            step.pop("on_error", None)
        else:
            step["on_error"] = value
        self._touch()

    def set_cond(self, node_id: str, op: str | None = None, right=None,
                 left=None) -> str | None:
        """Tek koşulun bir parçasını değiştirir; diğerlerini korur."""
        cur = self.single_cond(node_id)
        if cur is None and left is None:
            return "düzenlenecek tek bir koşul yok"
        new_op = C.canonical(op if op is not None else (cur.op if cur else "doğru"))
        if new_op not in C.OPS:
            return f"bilinmeyen işleç '{op}'"
        new_left = left if left is not None else (cur.left if cur else "")
        if C.OPS[new_op]["arity"] == 1:
            cond = C.Cond(op=new_op, left=new_left)
        else:
            val = right if right is not None else (
                cur.right if (cur and not cur.unary) else "")
            if C.OPS[new_op]["accepts"] == "number" and not isinstance(
                    val, (int, float)) or isinstance(val, bool):
                try:
                    val = T.coerce(val, "number")
                except T.TypeError_:
                    val = 0 if C.OPS[new_op]["accepts"] == "number" else val
            cond = C.Cond(op=new_op, left=new_left, right=val)
        self.set_guard(node_id, C.Guard(mode="all", conds=(cond,)))
        return None

    def connection_of(self, node_id: str, key: str) -> tuple[str, str] | None:
        step = self.step(node_id)
        if not step:
            return None
        if key == WHEN:
            c = self.single_cond(node_id)
            ref = sole_ref(c.left) if c else None
            return tuple(ref.partition(".")[::2]) if ref else None
        if key == EACH:
            ref = sole_ref(step.get(EACH))
            return tuple(ref.partition(".")[::2]) if ref else None
        ref = sole_ref((step.get("with") or {}).get(key))
        if not ref:
            return None
        src, _, out = ref.partition(".")
        return (src, out)

    def port_type(self, node_id: str, port: str, direction: str) -> str | None:
        if direction == "out":
            return self.flow.port_types.get((node_id, port))
        if node_id == TRIGGER:
            return None
        if port == WHEN:
            return "any"          # koşul her tipi alır, testi sonra seçilir
        if port == EACH:
            return "list"         # döngü kaynağı yalnızca liste olabilir
        step = self.step(node_id)
        if not step or str(step.get("action")) not in self.reg:
            return None
        act = self.reg.get(str(step["action"]))
        p = act.inputs.get(port)
        return p.type if p else None

    def can_connect(self, src: str, out: str, dst: str, key: str) -> tuple[bool, str]:
        """Kabloyu çizmeden önce sorulan soru: bu bağlantı geçerli mi?"""
        if dst == TRIGGER:
            return False, "tetikleyiciye girdi verilemez"
        if src == dst:
            return False, "düğüm kendine bağlanamaz"
        st = self.port_type(src, out, "out")
        dt = self.port_type(dst, key, "in")
        if st is None or dt is None:
            return False, "port bulunamadı"
        if key == WHEN:
            if src != TRIGGER and self._creates_cycle(src, dst):
                return False, "döngü oluşur"
            return True, ""
        if key == EACH:
            if not T.is_list(st):
                return False, (f"döngü kaynağı liste olmalı; {out} bir "
                               f"{T.label(st)} veriyor")
            if src != TRIGGER and self._creates_cycle(src, dst):
                return False, "döngü oluşur"
            return True, ""
        if not T.is_assignable(st, dt):
            return False, (f"{T.label(st)} → {T.label(dt)} olmaz "
                           f"({out} bir {T.label(st)} veriyor, {key} {T.label(dt)} bekliyor)")
        if src != TRIGGER and self._creates_cycle(src, dst):
            return False, "döngü oluşur"
        return True, ""

    def connect(self, src: str, out: str, dst: str, key: str) -> str | None:
        ok, why = self.can_connect(src, out, dst, key)
        if not ok:
            return why
        ref = "{{ " + f"{src}.{out}" + " }}"
        if key == EACH:
            self.snapshot()
            self.step(dst)[EACH] = ref
            self._sort_steps()
            self._touch()
            return None
        if key == WHEN:
            # Varsayılan test kaynağın tipinden: doğru/yanlış ise "doğru mu",
            # sayı ise eşik, metin ise "dolu mu". Kullanıcı panelden değiştirir.
            stype = self.port_type(src, out, "out") or "any"
            if T.is_assignable(stype, "bool"):
                cond = C.Cond(op="doğru", left=ref)
            elif T.is_assignable(stype, "number"):
                cond = C.Cond(op=">", left=ref, right=0)
            else:
                cond = C.Cond(op="dolu", left=ref)
            self.set_guard(dst, C.Guard(mode="all", conds=(cond,)))
            self._sort_steps()
            self._touch()
            return None
        self.snapshot()
        self.step(dst).setdefault("with", {})[key] = ref
        self._sort_steps()
        self._touch()
        return None

    def disconnect(self, dst: str, key: str) -> None:
        self.clear_value(dst, key)

    # --------------------------------------------------- sıralama / döngüler

    def _deps(self) -> dict[str, set[str]]:
        deps: dict[str, set[str]] = {str(s.get("id")): set() for s in self.steps()}
        for s in self.steps():
            sid = str(s.get("id"))
            sources = [r for v in (s.get("with") or {}).values() for r in refs_in(v)]
            sources += raw_refs(s.get(WHEN)) + raw_refs(s.get(EACH))
            for ref in sources:
                src = ref.partition(".")[0]
                if src != TRIGGER and src in deps:
                    deps[sid].add(src)
        return deps

    def _creates_cycle(self, src: str, dst: str) -> bool:
        """dst -> src kablosu eklenirse src, dst'ye (dolaylı) bağımlı olur mu?"""
        deps = self._deps()
        seen, stack = set(), [src]
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(deps.get(cur, ()))
        return False

    def _sort_steps(self) -> None:
        """Adımları veri akışına göre sıralar (topolojik).

        Motor adımları sırayla çalıştırır; tuvalde serbestçe kablo çeken
        kullanıcıyı 'bu adım sonra çalışıyor' hatasıyla uğraştırmamak için
        sıra, kabloların kendisinden türetilir.
        """
        deps = self._deps()
        order, placed = [], set()
        pending = [str(s.get("id")) for s in self.steps()]
        while pending:
            ready = [sid for sid in pending if deps.get(sid, set()) <= placed]
            if not ready:                      # çözülemeyen döngü: sırayı bozma
                order.extend(pending)
                break
            for sid in ready:
                order.append(sid)
                placed.add(sid)
            pending = [s for s in pending if s not in placed]
        by_id = {str(s.get("id")): s for s in self.steps()}
        self.raw["steps"] = [by_id[sid] for sid in order if sid in by_id]

    # ------------------------------------------------------------- yerleşim

    def pos(self, node_id: str) -> tuple[float, float] | None:
        p = (self.raw.get("layout") or {}).get(node_id)
        if isinstance(p, (list, tuple)) and len(p) == 2:
            return float(p[0]), float(p[1])
        return None

    def set_pos(self, node_id: str, x: float, y: float) -> None:
        self.raw.setdefault("layout", {})[node_id] = [round(float(x), 1), round(float(y), 1)]
        self.dirty = True

    # ----------------------------------------------------------- düğüm listesi

    def nodes(self) -> list[Node]:
        """Tuvalin çizeceği düğümler; konumu olmayanlara otomatik yerleşim."""
        spec = TRIGGERS.get(self.trigger_type, TRIGGERS["manual"])
        conf = self.raw.get("trigger") or {}
        detail = ""
        if self.trigger_type == "dbus":
            what = conf.get("member") or conf.get("property") or ""
            detail = f"{conf.get('interface', '')}.{what}".strip(".")
        else:
            for key in ("path", "on_calendar", "connection"):
                if conf.get(key):
                    detail = str(conf[key])
                    break
        from ..manifest import Port
        trg = Node(
            id=TRIGGER, kind="trigger",
            title=spec["label"], subtitle=detail,
            # Dinamik tetikleyicilerde çıktılar yapılandırmadan doğar.
            outputs={n: Port(n, self.flow.port_types.get((TRIGGER, n), t), label=n)
                     for n, t in self.flow.trigger_outputs().items()},
        )
        out = [trg]
        info = {st.id: st for st in self.flow.steps}
        for s in self.steps():
            sid = str(s.get("id"))
            action_id = str(s.get("action", ""))
            act = self.reg.actions.get(action_id)
            node = Node(
                id=sid, kind="step", action=action_id,
                title=str(s.get("title") or (act.name if act else action_id)),
                subtitle=action_id,
                inputs=dict(act.inputs) if act else {},
                # Çıktı tipleri manifestten değil doğrulamadan geliyor:
                # döngü list<T>'ye yükseltir, narrowed_by daraltır. Tuval
                # bunları göstermezse kabloyla etiket birbirini tutmaz.
                outputs={
                    name: replace(port, type=self.flow.port_types.get(
                        (sid, name), port.type))
                    for name, port in (act.outputs.items() if act else ())
                },
                catches=bool(info[sid].catches) if sid in info else False,
                isolated=bool(info[sid].isolated) if sid in info else False,
                guard=info[sid].guard if sid in info else None,
                each=info[sid].each if sid in info else None,
                elem_type=info[sid].elem_type if sid in info else "",
            )
            # Hata yakalayan adım iki port kazanıyor; bunlar eylemde değil,
            # akışta doğuyor, o yüzden burada ekleniyor.
            if node.catches:
                for pname, ptype in CATCH_PORTS.items():
                    if pname not in node.outputs:
                        node.outputs[pname] = Port(pname, ptype, label=pname)
            out.append(node)

        col_gap, row_gap = 300.0, 40.0
        depth: dict[str, int] = {TRIGGER: 0}
        deps = self._deps()
        for s in self.steps():
            sid = str(s.get("id"))
            depth[sid] = 1 + max([depth.get(d, 0) for d in deps.get(sid, ())] or [0])
        used: dict[int, float] = {}
        for n in out:
            p = self.pos(n.id)
            if p:
                n.x, n.y = p
                continue
            d = depth.get(n.id, 1)
            n.x = 40 + d * col_gap
            n.y = 40 + used.get(d, 0.0)
            used[d] = used.get(d, 0.0) + 220 + row_gap
            self.set_pos(n.id, n.x, n.y)
        return out

    def wires(self) -> list[tuple[str, str, str, str]]:
        """(kaynak, çıktı, hedef, girdi) dörtlüleri; koşul kabloları dahil."""
        out = []
        for s in self.steps():
            dst = str(s.get("id"))
            for key in (WHEN, EACH):
                for ref in dict.fromkeys(raw_refs(s.get(key))):
                    src, _, o = ref.partition(".")
                    out.append((src, o, dst, key))
            for key, value in (s.get("with") or {}).items():
                ref = sole_ref(value)
                if ref:
                    src, _, o = ref.partition(".")
                    out.append((src, o, dst, key))
                else:
                    for r in refs_in(value):      # metne gömülü referanslar
                        src, _, o = r.partition(".")
                        out.append((src, o, dst, key))
        return out
