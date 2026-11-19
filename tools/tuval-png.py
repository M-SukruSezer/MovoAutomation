#!/usr/bin/env python3
"""Tuvali pencere açmadan PNG'ye basar (geliştirme/doğrulama aracı).

Kullanım: tools/tuval-png.py <akış> <cikti.png> [genislik yukseklik]

Boyut verilmezse akışın kapladığı alana göre hesaplanır; böylece
görüntüde boş kenar kalmaz.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from movo.gui.canvas import (NodeCanvas, Text, View, draw_scene,  # noqa: E402
                             node_height)
from movo.gui.model import Doc  # noqa: E402
from movo.registry import Registry  # noqa: E402


def icerik_boyutu(doc: Doc, azami: int = 1200, kenar: int = 44) -> tuple[int, int]:
    """Akışın doğal boyutuna oturan çerçeve ölçüsü.

    fit() en-boy oranını koruyup yakınlaştırmayı sınırladığı için sabit
    bir çerçevede dar akışlar yanlardan boşluklu çıkıyordu. Çerçeveyi
    içeriğin kendi ölçüsünden türetince boşluk kalmıyor.
    """
    nodes = doc.nodes()
    if not nodes:
        return azami, 320
    x0 = min(n.x for n in nodes)
    y0 = min(n.y for n in nodes)
    x1 = max(n.x + n.w for n in nodes)
    y1 = max(n.y + node_height(n) for n in nodes)
    en = (x1 - x0) + 2 * kenar
    boy = (y1 - y0) + 2 * kenar
    if en > azami:                       # çok genişse orantılı küçült
        boy, en = boy * azami / en, azami
    return round(en), max(200, round(boy))


def render(doc: Doc, out: Path, w: int, h: int, dark: bool = True,
           view: View | None = None) -> Path:
    canvas = NodeCanvas(doc)
    v = view or View()
    v.dark = dark
    v.error_nodes = {p.where.split(".")[0] for p in doc.flow.problems if p.level == "hata"}
    canvas.view = v
    canvas.fit(w, h)
    snap = Gtk.Snapshot()
    draw_scene(snap, w, h, doc, canvas.view, Text(canvas))
    node = snap.to_node()
    renderer = Gsk.CairoRenderer()
    try:
        renderer.realize(None)
    except Exception:
        renderer.realize_for_display(Gdk.Display.get_default())
    tex = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(str(out))
    renderer.unrealize()
    return out


if __name__ == "__main__":
    Gtk.init()
    flow = Path(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "tuval.png")
    reg = Registry.discover()
    doc = Doc.open(reg, flow)
    if len(sys.argv) > 4:
        w, h = int(sys.argv[3]), int(sys.argv[4])
    else:
        w, h = icerik_boyutu(doc, int(sys.argv[3]) if len(sys.argv) > 3 else 1200)
    print("yazıldı:", render(doc, out, w, h))
