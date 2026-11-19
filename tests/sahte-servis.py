#!/usr/bin/env python3
"""Testler için küçük bir D-Bus servisi.

Sinyal tetikleyicisini gerçek bir bus üzerinde denemek gerekiyor, ama
kullanıcının sistem ayarlarını (güç profili, parlaklık) kurcalamadan.
GNOME'un bildirim sunucusu da CloseNotification çağrısında
NotificationClosed yaymıyor, yani hazır bir sinyal kaynağı yok. Çözüm:
kendi servisimizi yayınlamak. Oturum bus'ında ``dev.movo.Test`` adını
alır, istenince sinyal yayar ve bir özelliği değiştirir.

    tests/sahte-servis.py            # servisi çalıştırır
    tests/sahte-servis.py tetikle merhaba 7
    tests/sahte-servis.py ayarla 42
    tests/sahte-servis.py dur
"""
from __future__ import annotations

import sys
import warnings

import gi

from gi.repository import Gio, GLib  # noqa: E402

# register_object yerine önerilen API dağıtımlarda henüz her yerde yok
warnings.filterwarnings("ignore", category=DeprecationWarning)

NAME = "dev.movo.Test"
PATH = "/dev/movo/Test"
IFACE = "dev.movo.Test"

XML = f"""
<node>
  <interface name='{IFACE}'>
    <method name='Tetikle'>
      <arg type='s' name='mesaj' direction='in'/>
      <arg type='u' name='sayi' direction='in'/>
    </method>
    <method name='Ayarla'>
      <arg type='i' name='deger' direction='in'/>
    </method>
    <method name='Dur'/>
    <signal name='Olay'>
      <arg type='s' name='mesaj'/>
      <arg type='u' name='sayi'/>
    </signal>
    <property name='Deger' type='i' access='read'/>
  </interface>
</node>
"""


class Service:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.deger = 0
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.conn.register_object(
            PATH, node.interfaces[0], self.on_call, self.on_get, None)
        Gio.bus_own_name_on_connection(
            self.conn, NAME, Gio.BusNameOwnerFlags.REPLACE,
            lambda *_: print("sahte servis hazır", flush=True), None)

    def on_get(self, _conn, _sender, _path, _iface, prop, _err):
        return GLib.Variant("i", self.deger) if prop == "Deger" else None

    def on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "Tetikle":
            mesaj, sayi = params.unpack()
            self.conn.emit_signal(None, PATH, IFACE, "Olay",
                                  GLib.Variant("(su)", (mesaj, sayi)))
            invocation.return_value(None)
        elif method == "Ayarla":
            (self.deger,) = params.unpack()
            self.conn.emit_signal(
                None, PATH, "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)",
                             (IFACE, {"Deger": GLib.Variant("i", self.deger)}, [])))
            invocation.return_value(None)
        elif method == "Dur":
            invocation.return_value(None)
            GLib.timeout_add(80, self.loop.quit)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod",
                                         method)


def client(argv: list[str]) -> int:
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    cmd = argv[0]
    if cmd == "tetikle":
        params = GLib.Variant("(su)", (argv[1], int(argv[2])))
        method = "Tetikle"
    elif cmd == "ayarla":
        params = GLib.Variant("(i)", (int(argv[1]),))
        method = "Ayarla"
    elif cmd == "dur":
        params, method = None, "Dur"
    else:
        print(f"bilinmeyen komut: {cmd}", file=sys.stderr)
        return 2
    try:
        conn.call_sync(NAME, PATH, IFACE, method, params, None,
                       Gio.DBusCallFlags.NONE, 4000, None)
    except GLib.Error as e:
        print(f"çağrı başarısız: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(client(sys.argv[1:]))
    Service().loop.run()
