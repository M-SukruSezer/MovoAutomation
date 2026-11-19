#!/usr/bin/env python3
"""Editör penceresini açar, ekranını PNG'ye basar ve kapanır (doğrulama aracı).

Kullanım: tools/pencere-png.py movo/ornekler/resim-webp.yaml cikti.png [ms]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from movo.gui.model import Doc  # noqa: E402
from movo.gui.window import EditorWindow  # noqa: E402
from movo.registry import Registry  # noqa: E402


def shoot(window: Gtk.Window, out: Path) -> bool:
    w, h = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        print("pencere henüz çizilmedi")
        return False
    renderer = Gsk.CairoRenderer()
    try:
        renderer.realize(None)
    except Exception:
        renderer.realize_for_display(Gdk.Display.get_default())
    tex = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(str(out))
    renderer.unrealize()
    print(f"yazıldı: {out} ({w}x{h})")
    return True


class Shot(Adw.Application):
    def __init__(self, flow: Path | None, out: Path, delay: int, script=None,
                 size: tuple[int, int] | None = None):
        super().__init__(application_id="dev.movo.Shot",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.flow, self.out, self.delay, self.script = flow, out, delay, script
        self.size = size

    def do_activate(self):
        reg = Registry.discover()
        doc = Doc.open(reg, self.flow) if self.flow else Doc(reg)
        win = EditorWindow(self, reg, doc)
        if self.size:
            win.set_default_size(*self.size)
        win.present()

        self._tries = 0
        self._scripted = False

        def fire():
            if self.script and not self._scripted:
                # betik yalnız bir kez çalışır: her çağrıda yeniden çizim
                # kuyruğa alınırsa WidgetPaintable hiç sabitlenemez
                self.script(win)
                self._scripted = True
                self._tries += 1
                return True
            if shoot(win, self.out) or self._tries > 8:
                win.close()
                self.quit()
                return False
            self._tries += 1
            return True                      # yeniden dene
        GLib.timeout_add(self.delay, fire)


if __name__ == "__main__":
    flow = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "pencere.png")
    delay = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    sys.exit(Shot(flow, out, delay).run([]))
