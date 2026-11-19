"""D-Bus eylemleri: kod yazmadan sistem servisleriyle konuşmak.

Buraya kadar bir eylemin gövdesi ya Python'du ya da bir komut satırıydı.
İkisi de aynı şeyi yapıyordu: bir alt süreç doğurup çıktısını ayrıştırmak.
Oysa Linux masaüstünün gerçek arayüzü D-Bus'tır -- bildirim sunucusu, güç
profili yöneticisi, ekran kilidi, medya oynatıcıları hepsi orada durur ve
introspection sayesinde kendi imzalarını söyler.

Bu yüzden ``impl.kind: dbus`` bir gövde değil, bir *adres*tir:

    impl:
      kind: dbus
      bus: session
      service: org.freedesktop.Notifications
      object: /org/freedesktop/Notifications
      interface: org.freedesktop.Notifications
      member: Notify
      signature: "(susssasa{sv}i)"
      args: [...]
      returns: {id: {index: 0}}

Tek satır Python yok. Argümanlar manifestteki girdilerden beslenir, dönen
demetin alanları çıktı portlarına bağlanır; motorun geri kalanı farkı
görmez. İzin modeli de burada keskinleşiyor: "dbus" diye bir yetki yok,
``dbus:org.freedesktop.Notifications`` var. Hangi servisle konuştuğu
manifestin okunur yüzünde durur ve çalışma anında o desene uymayan bir
çağrı reddedilir.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

TIMEOUT = 8000
BUS_LABEL = {"session": "oturum", "system": "sistem"}
PERM_PREFIX = "dbus:"

# D-Bus'ın kendi standart arayüzleri; introspection çıktısında gürültü.
STOCK = {
    "org.freedesktop.DBus.Properties",
    "org.freedesktop.DBus.Introspectable",
    "org.freedesktop.DBus.Peer",
    "org.freedesktop.DBus.ObjectManager",
}


class DBusSpecError(Exception):
    """Manifestteki dbus bölümü hatalı."""


# ----------------------------------------------------------------- gi köprüsü

_gi_cache: dict = {}


def _gi():
    if not _gi_cache:
        try:
            import gi
            from gi.repository import Gio, GLib
        except ImportError as e:  # pragma: no cover - sistemde gi her zaman var
            raise RuntimeError(
                "D-Bus eylemleri python3-gi gerektiriyor "
                "(apt install python3-gi)"
            ) from e
        _gi_cache["gi"] = gi
        _gi_cache["Gio"] = Gio
        _gi_cache["GLib"] = GLib
    return _gi_cache["Gio"], _gi_cache["GLib"]


_conns: dict = {}


def connection(which: str):
    """Oturum veya sistem bus'ına bağlantı; süreç başına bir kez kurulur."""
    if which in _conns:
        return _conns[which]
    Gio, GLib = _gi()
    bt = Gio.BusType.SYSTEM if which == "system" else Gio.BusType.SESSION
    try:
        conn = Gio.bus_get_sync(bt, None)
    except GLib.Error as e:
        raise RuntimeError(
            f"{BUS_LABEL.get(which, which)} bus'ına bağlanılamadı: {e.message}. "
            f"Masaüstü oturumu dışında (ör. ssh) DBUS_SESSION_BUS_ADDRESS gerekir."
        ) from None
    _conns[which] = conn
    return conn


# -------------------------------------------------------------------- imzalar

_SCALAR = "ybnqiuxtdhsogv"
_INTS = set("ynqiuxth")


def _one(sig: str, i: int) -> int:
    """sig[i] konumundaki tek tipin bittiği indisi döndürür."""
    c = sig[i]
    if c in _SCALAR:
        return i + 1
    if c in "am":
        if i + 1 >= len(sig):
            raise DBusSpecError(f"imza yarım kaldı: '{sig}'")
        return _one(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        depth = 0
        for j in range(i, len(sig)):
            if sig[j] in "({":
                depth += 1
            elif sig[j] in ")}":
                depth -= 1
                if depth == 0:
                    if sig[j] != close:
                        raise DBusSpecError(f"imzada eşleşmeyen parantez: '{sig}'")
                    return j + 1
        raise DBusSpecError(f"imzada kapanmamış parantez: '{sig}'")
    raise DBusSpecError(f"imzada tanınmayan tip kodu '{c}': '{sig}'")


def split_sig(sig: str) -> list[str]:
    """'(susa{sv}i)' → ['s', 'u', 's', 'a{sv}', 'i']"""
    s = sig.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(s):
        j = _one(s, i)
        out.append(s[i:j])
        i = j
    return out


def _cast(value, vtype: str):
    """Python değerini GVariant tip koduna göre uygun sınıfa çeker."""
    if not vtype or value is None:
        return value
    c = vtype[0]
    if c in _INTS:
        if isinstance(value, bool):
            return int(value)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            raise DBusSpecError(f"'{value}' bir sayıya dönüşmüyor (tip {vtype})") from None
    if c == "d":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise DBusSpecError(f"'{value}' bir ondalığa dönüşmüyor") from None
    if c == "b":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "evet", "yes", "on", "açık")
        return bool(value)
    if c in "sog":
        return str(value)
    if vtype.startswith("a{"):
        return dict(value or {})
    if c == "a":
        return list(value or [])
    return value


# ------------------------------------------------------------------ argümanlar

_ARG_KEYS = {"const", "input", "dict"}
_ARG_OPTS = {"map", "scale", "type"}


def _is_spec(x) -> bool:
    return isinstance(x, dict) and bool(_ARG_KEYS & set(x))


def _check_arg(spec, inputs: dict, where: str) -> None:
    if not _is_spec(spec):
        if isinstance(spec, dict):
            raise DBusSpecError(
                f"{where}: argüman sözlüğü 'const', 'input' veya 'dict' "
                f"anahtarlarından birini taşımalı (geleni: {', '.join(spec) or 'boş'})")
        return
    extra = set(spec) - _ARG_KEYS - _ARG_OPTS
    if extra:
        raise DBusSpecError(f"{where}: argümanda tanınmayan anahtar: {', '.join(sorted(extra))}")
    if "dict" in spec:
        if not isinstance(spec["dict"], dict):
            raise DBusSpecError(f"{where}: 'dict' bir sözlük olmalı")
        for k, sub in spec["dict"].items():
            if isinstance(sub, dict) and not sub.get("type"):
                raise DBusSpecError(
                    f"{where}: sözlük girdisi '{k}' için 'type' zorunlu "
                    f"(a{{sv}} değerleri tipli variant olmak zorunda)")
            _check_arg(sub, inputs, f"{where}.{k}")
        return
    if "input" in spec:
        name = str(spec["input"])
        if name not in inputs:
            raise DBusSpecError(
                f"{where}: '{name}' diye bir girdi yok "
                f"(manifestteki girdiler: {', '.join(inputs) or 'yok'})")
    if "map" in spec and not isinstance(spec["map"], dict):
        raise DBusSpecError(f"{where}: 'map' bir sözlük olmalı")


def _eval_arg(spec, values: dict, vtype: str):
    if not _is_spec(spec):
        return _cast(spec, vtype)
    if "dict" in spec:
        _, GLib = _gi()
        out = {}
        for k, sub in spec["dict"].items():
            t = str(sub["type"]) if isinstance(sub, dict) and sub.get("type") else "s"
            out[k] = GLib.Variant(t, _eval_arg(sub, values, t))
        return out
    if "const" in spec:
        v = spec["const"]
    else:
        name = str(spec["input"])
        if name not in values:
            raise DBusSpecError(f"'{name}' girdisi çalışma anında gelmedi")
        v = values[name]
    if "map" in spec:
        table = spec["map"]
        key = str(v)
        if key not in table:
            raise DBusSpecError(
                f"'{key}' karşılıksız; beklenen değerler: {', '.join(map(str, table))}")
        v = table[key]
    if spec.get("scale") is not None:
        v = float(v) * float(spec["scale"])
    return _cast(v, str(spec.get("type") or vtype))


# ------------------------------------------------------------------ çağrı tanımı

@dataclass(frozen=True)
class Call:
    bus: str
    object: str
    interface: str
    service: str = ""          # sabit servis adı
    service_input: str = ""    # ya da çalışma anında bir girdiden
    service_prefix: str = ""
    member: str = ""
    signature: str = ""
    args: tuple = ()
    prop: str = ""
    access: str = "get"
    value: object = None
    value_type: str = ""
    returns: dict = field(default_factory=dict)
    timeout: int = TIMEOUT

    @property
    def dynamic(self) -> bool:
        return bool(self.service_input)

    @property
    def pattern(self) -> str:
        """İzin deseni: sabit ad, ya da ön ekli joker."""
        return self.service or (self.service_prefix + "*")

    def label(self) -> str:
        what = self.member or f"{self.access}:{self.prop}"
        return f"{self.pattern} {self.interface}.{what}"

    def resolve_service(self, values: dict) -> str:
        if not self.service_input:
            return self.service
        raw = str(values.get(self.service_input, "")).strip()
        if not raw:
            raise DBusSpecError(f"'{self.service_input}' girdisi boş; servis adı kurulamadı")
        if self.service_prefix and raw.startswith(self.service_prefix):
            return raw
        return self.service_prefix + raw


def parse(raw: dict, inputs: dict, outputs: dict, where: str) -> Call:
    """Manifestin impl bölümünü doğrular ve Call'a çevirir."""
    bus = str(raw.get("bus", "session"))
    if bus not in BUS_LABEL:
        raise DBusSpecError(f"{where}: impl.bus 'session' veya 'system' olmalı ('{bus}' geldi)")

    svc = raw.get("service")
    service = service_input = service_prefix = ""
    if isinstance(svc, dict):
        service_input = str(svc.get("input", ""))
        service_prefix = str(svc.get("prefix", ""))
        if not service_input:
            raise DBusSpecError(f"{where}: impl.service sözlüğü 'input' taşımalı")
        if service_input not in inputs:
            raise DBusSpecError(
                f"{where}: impl.service '{service_input}' girdisini istiyor ama öyle bir girdi yok")
        if not service_prefix:
            raise DBusSpecError(
                f"{where}: dinamik servis için 'prefix' zorunlu "
                f"(izin deseni ondan türüyor; ör. org.mpris.MediaPlayer2.)")
    elif isinstance(svc, str) and svc:
        service = svc
    else:
        raise DBusSpecError(f"{where}: impl.service zorunlu (metin ya da {{input, prefix}})")

    for key in ("object", "interface"):
        if not raw.get(key):
            raise DBusSpecError(f"{where}: impl.{key} zorunlu")
    obj = str(raw["object"])
    if not obj.startswith("/"):
        raise DBusSpecError(f"{where}: impl.object '/' ile başlamalı ('{obj}' geldi)")

    member = str(raw.get("member", "") or "")
    prop = str(raw.get("property", "") or "")
    if bool(member) == bool(prop):
        raise DBusSpecError(f"{where}: impl 'member' (yöntem) ya da 'property' (özellik) "
                            f"taşımalı; ikisi birden ya da hiçbiri olmaz")

    sig = str(raw.get("signature", "") or "")
    args = tuple(raw.get("args", ()) or ())
    access = str(raw.get("access", "get"))
    value = raw.get("value")
    value_type = str(raw.get("value_type", "") or "")

    if member:
        if sig:
            kids = split_sig(sig)
            if len(kids) != len(args):
                raise DBusSpecError(
                    f"{where}: imza {len(kids)} argüman istiyor, args {len(args)} tane veriyor "
                    f"({sig} → {', '.join(kids)})")
        elif args:
            raise DBusSpecError(f"{where}: args verilmiş ama impl.signature yok")
        for i, a in enumerate(args):
            _check_arg(a, inputs, f"{where}: args[{i}]")
    else:
        if access not in ("get", "set"):
            raise DBusSpecError(f"{where}: impl.access 'get' veya 'set' olmalı")
        if access == "set":
            if value is None:
                raise DBusSpecError(f"{where}: access 'set' için impl.value zorunlu")
            if not value_type:
                raise DBusSpecError(
                    f"{where}: access 'set' için impl.value_type zorunlu "
                    f"(özelliğin GVariant tipi, ör. s veya i)")
            _check_arg(value, inputs, f"{where}: value")

    rets = raw.get("returns", {}) or {}
    if not isinstance(rets, dict):
        raise DBusSpecError(f"{where}: impl.returns bir sözlük olmalı")
    for name, cfg in rets.items():
        if name not in outputs:
            raise DBusSpecError(
                f"{where}: returns '{name}' diye bir çıktı portuna bağlanıyor ama o port yok "
                f"(çıktılar: {', '.join(outputs) or 'yok'})")
        if cfg is not None and not isinstance(cfg, dict):
            raise DBusSpecError(f"{where}: returns.{name} bir sözlük (ya da boş) olmalı")
    for name in outputs:
        if name not in rets:
            raise DBusSpecError(
                f"{where}: '{name}' çıktısı beyan edilmiş ama returns içinde karşılığı yok")

    return Call(bus=bus, service=service, service_input=service_input,
                service_prefix=service_prefix, object=obj,
                interface=str(raw["interface"]), member=member, signature=sig, args=args,
                prop=prop, access=access, value=value, value_type=value_type,
                returns={k: dict(v or {}) for k, v in rets.items()},
                timeout=int(raw.get("timeout", TIMEOUT)))


def covered(pattern: str, granted) -> bool:
    """granted izinleri arasında pattern'i kapsayan bir dbus: izni var mı?"""
    for p in granted:
        if not p.startswith(PERM_PREFIX):
            continue
        allow = p[len(PERM_PREFIX):]
        if fnmatch.fnmatch(pattern, allow) or fnmatch.fnmatch(allow, pattern):
            return True
    return False


# ------------------------------------------------------------------- çalıştırma

_ERR_PREFIX = re.compile(r"^GDBus\.Error:[A-Za-z0-9_.]+:\s*")


def _friendly(err, service: str, call: Call) -> str:
    msg = getattr(err, "message", str(err)) or str(err)
    short = _ERR_PREFIX.sub("", msg).strip()
    if "ServiceUnknown" in msg or "not provided by any .service" in msg:
        return (f"'{service}' servisi {BUS_LABEL[call.bus]} bus'ında yok. "
                f"İlgili bileşen çalışmıyor olabilir ('movo incele' ile bakın).")
    if "UnknownMethod" in msg or "UnknownInterface" in msg:
        return (f"{service} üzerinde {call.interface}.{call.member or call.prop} yok. "
                f"'movo incele {service} {call.object}' arayüzün gerçek hâlini basar.")
    if "UnknownProperty" in msg:
        return f"{call.interface} arayüzünde '{call.prop}' özelliği yok."
    if "InvalidArgs" in msg:
        return f"argümanlar arayüzün beklediğine uymadı: {short}"
    if "AccessDenied" in msg or "not authorized" in msg.lower():
        return (f"{service} çağrıyı yetki yokluğundan reddetti (polkit). "
                f"Sistem bus'ındaki ayarlar için oturumun yetkisi olmalı.")
    if "NotSupported" in msg:
        return (f"{service} bu çağrıyı desteklemiyor: {short} "
                f"(masaüstüne özgü bir karşılığı olabilir)")
    if "NoReply" in msg or "Timeout" in msg:
        return f"{service} {call.timeout} ms içinde yanıt vermedi"
    return short


def _outputs(call: Call, items: list) -> dict:
    out = {}
    for name, cfg in call.returns.items():
        idx = int(cfg.get("index", 0))
        if idx >= len(items):
            raise DBusSpecError(
                f"'{name}' çıktısı yanıtın {idx}. alanını istiyor ama yanıt {len(items)} alanlı")
        v = items[idx]
        if cfg.get("scale") is not None:
            v = float(v) * float(cfg["scale"])
        out[name] = v
    return out


def invoke(ctx, call: Call, values: dict) -> dict:
    """Bir dbus eylemini çalıştırır. Motor bunu python gövdesinden ayırt etmez."""
    Gio, GLib = _gi()
    service = call.resolve_service(values)
    ctx.require_bus(service)
    conn = connection(call.bus)
    flags = Gio.DBusCallFlags.NONE

    try:
        if call.prop:
            if call.access == "get":
                reply = conn.call_sync(
                    service, call.object, "org.freedesktop.DBus.Properties", "Get",
                    GLib.Variant("(ss)", (call.interface, call.prop)),
                    None, flags, call.timeout, None)
                value = reply.unpack()[0]
                ctx.log(f"{service} {call.prop} = {value!r}")
                return _outputs(call, [value])
            new = _eval_arg(call.value, values, call.value_type)
            conn.call_sync(
                service, call.object, "org.freedesktop.DBus.Properties", "Set",
                GLib.Variant("(ssv)", (call.interface, call.prop,
                                       GLib.Variant(call.value_type, new))),
                None, flags, call.timeout, None)
            ctx.log(f"{service} {call.prop} ← {new!r}")
            return _outputs(call, [new])

        params = None
        if call.signature:
            kids = split_sig(call.signature)
            vals = [_eval_arg(a, values, kids[i]) for i, a in enumerate(call.args)]
            params = GLib.Variant(call.signature, tuple(vals))
        reply = conn.call_sync(service, call.object, call.interface, call.member,
                               params, None, flags, call.timeout, None)
        items = list(reply.unpack()) if reply is not None else []
        ctx.log(f"{service} {call.interface}.{call.member} → "
                + (", ".join(repr(i) for i in items) if items else "yanıt yok"))
        return _outputs(call, items)
    except GLib.Error as e:
        raise RuntimeError(_friendly(e, service, call)) from None


# ----------------------------------------------------------------- keşif

_names: dict = {}


def names(bus: str = "session", refresh: bool = False) -> set | None:
    """Bus'taki adlandırılmış servisler (çalışan + etkinleştirilebilir).

    Bus'a bağlanılamıyorsa None döner -- "yok" ile "bilinmiyor" aynı şey değil.
    """
    if not refresh and bus in _names:
        return _names[bus]
    Gio, GLib = _gi()
    try:
        conn = connection(bus)
        got = set()
        for m in ("ListNames", "ListActivatableNames"):
            reply = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                   "org.freedesktop.DBus", m, None, None,
                                   Gio.DBusCallFlags.NONE, 3000, None)
            got |= {n for n in reply.unpack()[0] if not n.startswith(":")}
    except (GLib.Error, RuntimeError):
        got = None
    _names[bus] = got
    return got


def present(pattern: str, bus: str = "session") -> bool | None:
    """Desene uyan bir servis bus'ta var mı? Bilinmiyorsa None."""
    have = names(bus)
    if have is None:
        return None
    if any(ch in pattern for ch in "*?["):
        return any(fnmatch.fnmatch(n, pattern) for n in have)
    return pattern in have


def introspect(bus: str, service: str, path: str) -> str:
    Gio, GLib = _gi()
    try:
        reply = connection(bus).call_sync(
            service, path, "org.freedesktop.DBus.Introspectable", "Introspect",
            None, None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as e:
        raise RuntimeError(getattr(e, "message", str(e))) from None
    return reply.unpack()[0]


def _node(xml: str) -> dict:
    """Introspection XML'ini arayüz/üye sözlüğüne çevirir."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    ifaces = {}
    for iface in root.findall("interface"):
        name = iface.get("name", "")
        methods = []
        for m in iface.findall("method"):
            ins, outs = [], []
            for a in m.findall("arg"):
                item = (a.get("name") or "", a.get("type") or "")
                (outs if a.get("direction") == "out" else ins).append(item)
            methods.append({"name": m.get("name", ""), "in": ins, "out": outs})
        props = [{"name": p.get("name", ""), "type": p.get("type", ""),
                  "access": p.get("access", "read")} for p in iface.findall("property")]
        signals = []
        for sg in iface.findall("signal"):
            signals.append({
                "name": sg.get("name", ""),
                "args": [(a.get("name") or f"arg{i}", a.get("type") or "")
                         for i, a in enumerate(sg.findall("arg"))],
            })
        ifaces[name] = {"methods": methods, "properties": props, "signals": signals}
    children = [c.get("name", "") for c in root.findall("node") if c.get("name")]
    return {"interfaces": ifaces, "children": children}


def walk(bus: str, service: str, path: str = "/", depth: int = 5) -> dict:
    """Nesne ağacını gezer, kendi arayüzü olan yolları döndürür."""
    found: dict = {}
    seen: set = set()

    def go(p: str, left: int):
        if left < 0 or p in seen:
            return
        seen.add(p)
        try:
            node = _node(introspect(bus, service, p))
        except Exception:
            return
        own = {k: v for k, v in node["interfaces"].items() if k not in STOCK}
        if own:
            found[p] = own
        for c in node["children"]:
            go(("" if p == "/" else p) + "/" + c, left - 1)

    go(path, depth)
    return found


def port_type(vtype: str) -> str:
    """GVariant tip kodunun akış tip sistemindeki en yakın karşılığı."""
    if not vtype:
        return "text"
    c = vtype[0]
    if c in _INTS or c == "d":
        return "number"
    if c == "b":
        return "bool"
    return "text"


def skeleton(bus: str, service: str, path: str, interface: str, member: dict) -> str:
    """İncelenen bir yöntem için hazır manifest iskeleti üretir."""
    name = member["name"]
    ins = member.get("in", [])
    outs = member.get("out", [])
    sig = "(" + "".join(t for _, t in ins) + ")" if ins else ""
    short = service.split(".")[-1].lower()
    lines = [
        f"id: {short}.{name.lower()}",
        f"name: {name}",
        f"summary: {service} üzerindeki {interface}.{name} çağrısı.",
        "category: masaüstü",
        f"permissions: [dbus:{service}]",
    ]
    if ins:
        lines.append("inputs:")
        for i, (an, at) in enumerate(ins):
            label = an or f"arg{i}"
            lines.append(
                f"  {label}: {{type: {port_type(at)}, label: {label}, required: true}}")
    if outs:
        lines.append("outputs:")
        for i, (an, at) in enumerate(outs):
            label = an or f"out{i}"
            lines.append(f"  {label}: {{type: {port_type(at)}, label: {label}}}")
    lines += [
        "impl:",
        "  kind: dbus",
        f"  bus: {bus}",
        f"  service: {service}",
        f"  object: {path}",
        f"  interface: {interface}",
        f"  member: {name}",
    ]
    if sig:
        lines.append(f'  signature: "{sig}"')
        lines.append("  args:")
        for i, (an, _t) in enumerate(ins):
            lines.append(f"    - {{input: {an or f'arg{i}'}}}")
    if outs:
        lines.append("  returns:")
        for i, (an, _t) in enumerate(outs):
            lines.append(f"    {an or f'out{i}'}: {{index: {i}}}")
    return "\n".join(lines) + "\n"


def skeleton_property(bus: str, service: str, path: str, interface: str,
                      prop: dict) -> str:
    """İncelenen bir özellik için okuma/yazma manifest iskeleti."""
    name = prop["name"]
    writable = "write" in prop.get("access", "read")
    t = prop.get("type", "s")
    pt = port_type(t)
    short = service.split(".")[-1].lower()
    lines = [
        f"id: {short}.{name.lower()}",
        f"name: {name}",
        f"summary: {interface} özelliği {name} ({'okunur/yazılır' if writable else 'okunur'}).",
        "category: masaüstü",
        f"permissions: [dbus:{service}]",
    ]
    if writable:
        lines += ["inputs:",
                  f"  value: {{type: {pt}, label: {name}, required: true}}"]
    lines += ["outputs:", f"  value: {{type: {pt}, label: {name}}}",
              "impl:", "  kind: dbus", f"  bus: {bus}",
              f"  service: {service}", f"  object: {path}",
              f"  interface: {interface}", f"  property: {name}",
              f"  access: {'set' if writable else 'get'}"]
    if writable:
        lines += ["  value: {input: value}", f'  value_type: "{t}"']
    lines += ["  returns:", "    value: {index: 0}"]
    return "\n".join(lines) + "\n"


def find_member(tree: dict, name: str):
    """walk() ağacında bir yöntem, özellik ya da sinyal arar."""
    low = name.lower()
    for path, ifaces in tree.items():
        for iname, info in ifaces.items():
            for m in info["methods"]:
                if m["name"].lower() == low:
                    return ("method", path, iname, m)
            for p in info["properties"]:
                if p["name"].lower() == low:
                    return ("property", path, iname, p)
            for sg in info["signals"]:
                if sg["name"].lower() == low:
                    return ("signal", path, iname, sg)
    return None


def trigger_skeleton(bus: str, service: str, path: str, interface: str,
                     signal: dict) -> str:
    """Bir sinyal için hazır tetikleyici bloğu."""
    args = ", ".join(f"{n}: {port_type(t)}" for n, t in signal.get("args", []))
    lines = [
        "trigger:",
        "  type: dbus",
        f"  bus: {bus}",
        f"  service: {service}",
        f"  object: {path}",
        f"  interface: {interface}",
        f"  member: {signal['name']}",
    ]
    if args:
        lines.append(f"  args: {{{args}}}")
    refs = " ".join("{{ trigger." + n + " }}" for n, _ in signal.get("args", []))
    lines.append(f"  # adımlarda: {refs}" if refs else "  # argümansız sinyal")
    return "\n".join(lines) + "\n"


def property_trigger_skeleton(bus: str, service: str, path: str,
                              interface: str, prop: dict) -> str:
    """Bir özelliğin değişimini dinleyen tetikleyici bloğu."""
    return "\n".join([
        "trigger:",
        "  type: dbus",
        f"  bus: {bus}",
        f"  service: {service}",
        f"  object: {path}",
        f"  interface: {interface}",
        f"  property: {prop['name']}",
        f"  produces: {port_type(prop.get('type', 's'))}",
        "  # adımlarda: {{ trigger.value }}",
    ]) + "\n"


def sig_text(member: dict) -> str:
    """Yöntemi insan okunur imzayla yazar: Notify(s, u, ...) → u"""
    ins = ", ".join(f"{t} {n}".strip() for n, t in member.get("in", []))
    outs = ", ".join(f"{t} {n}".strip() for n, t in member.get("out", []))
    return f"{member['name']}({ins})" + (f" → {outs}" if outs else "")
