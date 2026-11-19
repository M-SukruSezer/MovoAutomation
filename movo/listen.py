"""D-Bus sinyallerini tetikleyiciye çevirir.

Klasör tetikleyicisi inotify'ı, ağ tetikleyicisi NetworkManager
dispatcher'ını kullanıyordu; bu da bus'a abone oluyor. Üçünün de ortak
yanı uzun ömürlü bir süreç olması -- sinyal "şu an" gelir, cron gibi
sorulup öğrenilmez.

İki kip var ve ikisi de ``kind: dbus`` eylemlerinin dilbilgisini
tekrarlıyor, böylece aynı kafada kalıyorsun:

    trigger:                        trigger:
      type: dbus                      type: dbus
      interface: org.x                bus: system
      member: BirSinyal               object: /org/x
      args: {kim: text}               interface: org.x
                                      property: Yuzde
                                      produces: number

Sinyal kipinde argümanlar sırayla adlandırılır ve tip alır; özellik
kipinde PropertiesChanged dinlenir ve yalnızca *değişen* değer akışa
girer -- aynı değeri ikinci kez bildiren servis akışı boşuna çalıştırmaz.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from . import dbus as dbuslib
from . import types as T

PROPS_IFACE = "org.freedesktop.DBus.Properties"
PROPS_SIGNAL = "PropertiesChanged"


@dataclass
class Spec:
    bus: str = "session"
    service: str = ""          # gönderen süzgeci (boşsa herkes)
    object: str = ""           # nesne yolu süzgeci
    interface: str = ""
    member: str = ""           # sinyal kipi
    prop: str = ""             # özellik kipi
    produces: str = "text"
    args: dict = field(default_factory=dict)   # ad -> tip, sırayla
    settle_ms: int = 0

    @property
    def watching_property(self) -> bool:
        return bool(self.prop)

    def label(self) -> str:
        where = self.object or "*"
        what = self.member or f"{self.prop} (özellik)"
        return f"{dbuslib.BUS_LABEL[self.bus]} · {self.interface}.{what} @ {where}"


def spec_from(conf: dict) -> Spec:
    args = conf.get("args")
    return Spec(
        bus=str(conf.get("bus", "session")),
        service=str(conf.get("service", "") or ""),
        object=str(conf.get("object", "") or ""),
        interface=str(conf.get("interface", "") or ""),
        member=str(conf.get("member", "") or ""),
        prop=str(conf.get("property", "") or ""),
        produces=str(conf.get("produces", "text") or "text"),
        args={str(k): str(v) for k, v in (args or {}).items()}
        if isinstance(args, dict) else {},
        settle_ms=int(conf.get("settle_ms", 0) or 0),
    )


def _cast(value, t: str):
    """Tetikleyici de tip sözü veriyor; tutmazsa ham değer geçer."""
    try:
        return T.coerce(value, t)
    except T.TypeError_:
        return value


def read_property(spec: Spec):
    """Özellik kipinde başlangıç değeri: tekrar bildirimini ayıklamak için."""
    Gio, GLib = dbuslib._gi()
    if not spec.service:
        return None
    try:
        reply = dbuslib.connection(spec.bus).call_sync(
            spec.service, spec.object, PROPS_IFACE, "Get",
            GLib.Variant("(ss)", (spec.interface, spec.prop)),
            None, Gio.DBusCallFlags.NONE, 4000, None)
        return reply.unpack()[0]
    except (GLib.Error, RuntimeError):
        return None


def listen(conf: dict, on_event, once: bool = False, quiet: bool = False) -> int:
    """Sinyale abone olur ve her gelişinde on_event(values) çağırır."""
    Gio, GLib = dbuslib._gi()
    spec = spec_from(conf)
    conn = dbuslib.connection(spec.bus)

    if spec.watching_property:
        sub_iface, sub_member = PROPS_IFACE, PROPS_SIGNAL
    else:
        sub_iface, sub_member = spec.interface, spec.member

    state = {"last": None, "pending": None, "timer": 0, "count": 0}
    if spec.watching_property:
        state["last"] = read_property(spec)
        if not quiet and state["last"] is not None:
            print(f"movo: başlangıç değeri {state['last']!r}", flush=True)

    loop = GLib.MainLoop()

    def dispatch(values: dict) -> None:
        state["count"] += 1
        try:
            on_event(values)
        except Exception as e:                       # bir olay akışı öldürmesin
            print(f"movo: olay işlenemedi: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
        if once:
            loop.quit()

    def flush() -> bool:
        state["timer"] = 0
        values, state["pending"] = state["pending"], None
        if values is not None:
            dispatch(values)
        return False

    def emit(values: dict) -> None:
        if spec.settle_ms <= 0:
            dispatch(values)
            return
        # Hızlı peş peşe gelen sinyalleri tek olaya indirger: son değer kazanır.
        state["pending"] = values
        if state["timer"]:
            GLib.source_remove(state["timer"])
        state["timer"] = GLib.timeout_add(spec.settle_ms, flush)

    def on_signal(_conn, sender, path, iface, member, params):
        items = list(params.unpack()) if params is not None else []
        if spec.watching_property:
            if len(items) < 2 or str(items[0]) != spec.interface:
                return
            changed = items[1] or {}
            invalid = items[2] if len(items) > 2 else []
            if spec.prop in changed:
                value = changed[spec.prop]
            elif spec.prop in (invalid or []):
                value = read_property(spec)       # servis "değişti" dedi, değeri vermedi
            else:
                return
            if value == state["last"]:
                return                            # aynı değer, akışı yorma
            state["last"] = value
            emit({
                "value": _cast(value, spec.produces),
                "property": spec.prop,
                "interface": spec.interface,
                "object": path,
            })
            return

        values: dict = {"member": member, "object": path}
        for i, (name, t) in enumerate(spec.args.items()):
            values[name] = _cast(items[i], t) if i < len(items) else ""
        emit(values)

    sub = conn.signal_subscribe(
        spec.service or None, sub_iface, sub_member, spec.object or None,
        None, Gio.DBusSignalFlags.NONE, on_signal)
    if not quiet:
        print(f"movo: dinleniyor — {spec.label()}", flush=True)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        conn.signal_unsubscribe(sub)
        if state["timer"]:
            GLib.source_remove(state["timer"])
    return state["count"]
