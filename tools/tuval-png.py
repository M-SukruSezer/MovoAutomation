#!/usr/bin/env python3
"""Tuvali pencere açmadan PNG'ye basar (geliştirme/doğrulama aracı).

Kullanım: tools/tuval-png.py movo/ornekler/resim-webp.yaml cikti.png [genislik yukseklik]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from movo.gui.canvas import NodeCanvas, Text, View, draw_scene  # noqa: E402
from movo.gui.model import Doc  # noqa: E402
from movo.registry import Registry  # noqa: E402


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
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 760
    reg = Registry.discover()
    doc = Doc.open(reg, flow)
    print("yazıldı:", render(doc, out, w, h))
