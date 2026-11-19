"""Düğüm tuvali: GTK4'ün kendi render katmanı (Gsk) ile çizim.

Tuvalin tek bir fikri var: **tipleri gösterir**. Her port tipine göre renkli,
kablo çekerken yalnız takılabilecek portlar aydınlanır, uymayan bağlantı
çizilmeden reddedilir. Blokların birbirine takılmasını sağlayan görünmez
katman burada görünür hale gelir.

Çizim pycairo yerine Gsk kullanır: ek bağımlılık yok (python3-gi-cairo
gerekmez) ve metin GTK'nın kendi Pango dizgisiyle çizilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import cond as C
from .. import types as T
from ..flowref import sole_ref
from ..manifest import Port
from .model import EACH, TRIGGER, WHEN, Doc, Node

WHEN_PORT = Port(WHEN, "any", label="koşul")
EACH_PORT = Port(EACH, "list", label="her öge")

ROW = 24.0
HEADER = 38.0
PAD = 9.0
GAP = 8.0
PORT_R = 5.5
GRAB_R = 12.0
NODE_R = 10.0

TYPE_COLORS = {
    "text": (0.62, 0.66, 0.73),
    "number": (0.35, 0.78, 0.55),
    "bool": (0.71, 0.49, 0.90),
    "path": (0.36, 0.62, 0.94),
    "file": (0.36, 0.62, 0.94),
    "directory": (0.22, 0.73, 0.68),
    "image": (0.91, 0.64, 0.24),
    "audio": (0.93, 0.45, 0.55),
    "video": (0.93, 0.45, 0.55),
    "any": (0.55, 0.57, 0.62),
}

DARK = {
    "bg": (0.086, 0.094, 0.110), "grid": (0.137, 0.149, 0.172),
    "node": (0.145, 0.157, 0.180), "node_hdr": (0.200, 0.220, 0.250),
    "trigger_hdr": (0.196, 0.298, 0.420), "text": (0.900, 0.910, 0.930),
    "dim": (0.560, 0.585, 0.640), "sel": (0.380, 0.650, 1.000),
    "err": (0.930, 0.360, 0.360), "ok": (0.330, 0.780, 0.520),
    "edge": (0.240, 0.260, 0.300),
}
LIGHT = {
    "bg": (0.976, 0.976, 0.980), "grid": (0.912, 0.914, 0.925),
    "node": (1.0, 1.0, 1.0), "node_hdr": (0.930, 0.940, 0.960),
    "trigger_hdr": (0.820, 0.880, 0.970), "text": (0.120, 0.130, 0.150),
    "dim": (0.420, 0.440, 0.480), "sel": (0.130, 0.450, 0.900),
    "err": (0.800, 0.180, 0.180), "ok": (0.150, 0.550, 0.300),
    "edge": (0.850, 0.860, 0.880),
}


def port_glyph(snap, px, py, color, pal, listed: bool, filled=True,
               alpha=1.0, highlight=False):
    """Port noktası. Liste portları çift halkalı: 'burada çok şey var'."""
    if highlight:
        circle(snap, px, py, PORT_R + 5, pal["ok"], 0.30)
    if listed:
        circle(snap, px, py, PORT_R + 3.2, color, alpha, filled=False, width=1.6)
        circle(snap, px, py, PORT_R - 1.4, color, alpha, filled=filled)
        if not filled:
            circle(snap, px, py, PORT_R - 1.4, color, alpha, filled=False, width=1.6)
        return
    circle(snap, px, py, PORT_R, color, alpha, filled=filled)
    if not filled:
        circle(snap, px, py, PORT_R, color, alpha, filled=False, width=1.8)


def type_color(t: str | None):
    """Liste, ögesinin rengini alır; çokluğu renk değil halka anlatır."""
    e = T.elem(t or "any")
    return TYPE_COLORS.get(e or t or "any", TYPE_COLORS["any"])


def rgba(c, alpha: float = 1.0) -> Gdk.RGBA:
    col = Gdk.RGBA()
    col.red, col.green, col.blue, col.alpha = float(c[0]), float(c[1]), float(c[2]), alpha
    return col


def rect(x, y, w, h) -> Graphene.Rect:
    return Graphene.Rect().init(float(x), float(y), float(w), float(h))


# ------------------------------------------------------------------ geometri

def prepare(nodes: dict, view: "View") -> dict:
    """Düğümlere koşul portunu ekler ve yüksekliklerini hesaplar.

    'koşul' gerçek bir eylem girdisi değil, adımın kendi özelliği. Yine de
    porta çevirmek işe yarıyor: aynı geometri, aynı kablo çekme, aynı
    uyumluluk denetimi. Sürekli görünmesi tuvali kalabalıklaştırdığı için
    yalnızca koşulu olan düğümlerde -- ve kablo sürüklenirken herkeste --
    çiziliyor, ki hedef olarak bulunabilsin.
    """
    show_all = bool(view.drag_wire)
    for n in nodes.values():
        if n.kind != "step":
            continue
        extra = {}
        if n.each is not None or show_all:
            extra[EACH] = EACH_PORT
        if n.guard is not None or show_all:
            extra[WHEN] = WHEN_PORT
        if extra:
            n.inputs = {**extra, **n.inputs}
    for n in nodes.values():
        n.h = node_height(n)
    return nodes


def node_height(n: Node) -> float:
    rows = len(n.inputs) + len(n.outputs)
    sep = GAP if (n.inputs and n.outputs) else 0.0
    return HEADER + PAD + rows * ROW + sep + PAD


def in_port_xy(n: Node, name: str) -> tuple[float, float]:
    i = list(n.inputs).index(name)
    return n.x, n.y + HEADER + PAD + i * ROW + ROW / 2


def out_port_xy(n: Node, name: str) -> tuple[float, float]:
    j = list(n.outputs).index(name)
    base = n.y + HEADER + PAD + len(n.inputs) * ROW + (GAP if n.inputs else 0.0)
    return n.x + n.w, base + j * ROW + ROW / 2


@dataclass
class View:
    zoom: float = 1.0
    ox: float = 0.0
    oy: float = 0.0
    selected: str | None = None
    hover_port: tuple | None = None       # (node_id, port, "in"|"out")
    drag_wire: tuple | None = None        # ((node_id, port, dir), (x, y))
    dark: bool = True
    error_nodes: set = field(default_factory=set)

    def to_world(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.ox) / self.zoom, (y - self.oy) / self.zoom


# -------------------------------------------------------------- çizim yardımı

def fill_rounded(snap, x, y, w, h, r, color, alpha=1.0):
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect(x, y, w, h), r)
    snap.push_rounded_clip(rr)
    snap.append_color(rgba(color, alpha), rect(x, y, w, h))
    snap.pop()


def stroke_rounded(snap, x, y, w, h, r, color, width=1.0, alpha=1.0):
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect(x, y, w, h), r)
    snap.append_border(rr, [width] * 4, [rgba(color, alpha)] * 4)


def circle(snap, cx, cy, r, color, alpha=1.0, filled=True, width=1.8):
    pb = Gsk.PathBuilder.new()
    pb.add_circle(Graphene.Point().init(float(cx), float(cy)), float(r))
    path = pb.to_path()
    if filled:
        snap.append_fill(path, Gsk.FillRule.WINDING, rgba(color, alpha))
    else:
        snap.append_stroke(path, Gsk.Stroke.new(width), rgba(color, alpha))


def wire(snap, x1, y1, x2, y2, color, width=2.0, dashed=False, alpha=0.95):
    dx = max(40.0, abs(x2 - x1) * 0.5)
    pb = Gsk.PathBuilder.new()
    pb.move_to(x1, y1)
    pb.cubic_to(x1 + dx, y1, x2 - dx, y2, x2, y2)
    stroke = Gsk.Stroke.new(width)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    if dashed:
        stroke.set_dash([7.0, 5.0])
    snap.append_stroke(pb.to_path(), stroke, rgba(color, alpha))


def lines(snap, segments, color, width=1.0, alpha=1.0):
    if not segments:
        return
    pb = Gsk.PathBuilder.new()
    for x1, y1, x2, y2 in segments:
        pb.move_to(x1, y1)
        pb.line_to(x2, y2)
    snap.append_stroke(pb.to_path(), Gsk.Stroke.new(width), rgba(color, alpha))


class Text:
    """Pango dizgisi; layout'ları widget bağlamından üretir."""

    def __init__(self, widget: Gtk.Widget):
        self.widget = widget

    def draw(self, snap, x, y, text, size=11.5, color=(1, 1, 1), bold=False,
             width=None, align_right=False, alpha=1.0):
        layout = self.widget.create_pango_layout(str(text))
        desc = Pango.FontDescription()
        desc.set_family("Cantarell, Sans")
        desc.set_absolute_size(size * Pango.SCALE)
        if bold:
            desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(desc)
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            if align_right:
                layout.set_alignment(Pango.Alignment.RIGHT)
        _, logical = layout.get_pixel_extents()
        snap.save()
        snap.translate(Graphene.Point().init(float(x), float(y - logical.height / 2)))
        snap.append_layout(layout, rgba(color, alpha))
        snap.restore()


def _each_label(doc: Doc, node: Node) -> str:
    each = node.each
    if each is None:
        return "yok"
    if isinstance(each, list):
        return f"{len(each)} sabit öge"
    pol = doc.on_error(node.id)
    return ("hata olursa atla" if pol == "atla"
            else (T.label(node.elem_type) if node.elem_type else "liste"))


def _when_label(doc: Doc, node: Node) -> str:
    """Koşul satırının sağında duran metin: kablonun ucundaki test."""
    c = doc.single_cond(node.id)
    if c is None:
        if node.guard is None:
            return "yok"
        n = len(node.guard.conds)
        # çok koşullu metin bu sütuna sığmaz; sayısını söyleyip ayrıntıyı
        # panele bırakmak kırpılmış bir cümleden dürüst
        return f"{n} koşul" + (" (biri)" if node.guard.mode == "any" else "")
    if c.unary:
        return C.OPS[c.op]["label"]
    right = C.show(c.right)
    return f"{c.op} {right}" if sole_ref(c.left) else C.describe(node.guard)


def _value_label(doc: Doc, node: Node, key: str) -> str:
    if key == WHEN:
        return _when_label(doc, node)
    if key == EACH:
        return _each_label(doc, node)
    step = doc.step(node.id)
    args = (step or {}).get("with") or {}
    port = node.inputs[key]
    if key in args:
        v = args[key]
        if isinstance(v, bool):
            return "açık" if v else "kapalı"
        return str(v) if str(v) else "—"
    if port.default is not None:
        if isinstance(port.default, bool):
            return "açık" if port.default else "kapalı"
        return str(port.default) if str(port.default) else "—"
    return "zorunlu" if port.required else "—"


# -------------------------------------------------------------------- sahne

def draw_scene(snap, width: float, height: float, doc: Doc, view: View, text: Text) -> None:
    pal = DARK if view.dark else LIGHT
    snap.append_color(rgba(pal["bg"]), rect(0, 0, width, height))

    snap.save()
    snap.translate(Graphene.Point().init(view.ox, view.oy))
    snap.scale(view.zoom, view.zoom)

    # ızgara
    step = 28.0
    x0, y0 = view.to_world(0, 0)
    x1, y1 = view.to_world(width, height)
    segs = []
    gx = (x0 // step) * step
    while gx < x1:
        segs.append((gx, y0, gx, y1))
        gx += step
    gy = (y0 // step) * step
    while gy < y1:
        segs.append((x0, gy, x1, gy))
        gy += step
    lines(snap, segs, pal["grid"], 1.0 / view.zoom)

    nodes = prepare({n.id: n for n in doc.nodes()}, view)

    # kablolar
    for src, out, dst, key in doc.wires():
        sn, dn = nodes.get(src), nodes.get(dst)
        if not sn or not dn or out not in sn.outputs or key not in dn.inputs:
            continue
        sx, sy = out_port_xy(sn, out)
        dx_, dy_ = in_port_xy(dn, key)
        hot = view.selected in (src, dst)
        # koşul kablosu kesikli: veri taşımıyor, karar taşıyor
        wire(snap, sx, sy, dx_, dy_, type_color(sn.outputs[out].type),
             3.0 if hot else 2.0, dashed=(key in (WHEN, EACH)))

    # sürüklenen kablo + uyumlu portlar
    compatible: set = set()
    if view.drag_wire:
        (src_id, port, direction), (mx, my) = view.drag_wire
        sn = nodes.get(src_id)
        if sn:
            if direction == "out":
                sx, sy = out_port_xy(sn, port)
                st = sn.outputs[port].type
                for n in nodes.values():
                    for key in n.inputs:
                        if doc.can_connect(src_id, port, n.id, key)[0]:
                            compatible.add((n.id, key))
            else:
                sx, sy = in_port_xy(sn, port)
                st = sn.inputs[port].type
                for n in nodes.values():
                    for o in n.outputs:
                        if doc.can_connect(n.id, o, src_id, port)[0]:
                            compatible.add((n.id, o))
            wire(snap, sx, sy, mx, my, type_color(st), 2.4, dashed=True)

    # düğümler
    for n in nodes.values():
        is_sel = view.selected == n.id
        has_err = n.id in view.error_nodes
        fill_rounded(snap, n.x, n.y, n.w, n.h, NODE_R, pal["node"])
        fill_rounded(snap, n.x, n.y, n.w, HEADER,
                     NODE_R, pal["trigger_hdr"] if n.kind == "trigger" else pal["node_hdr"])
        snap.append_color(rgba(pal["trigger_hdr"] if n.kind == "trigger" else pal["node_hdr"]),
                          rect(n.x, n.y + HEADER - NODE_R, n.w, NODE_R))
        stroke_rounded(snap, n.x, n.y, n.w, n.h, NODE_R,
                       pal["err"] if has_err else pal["sel"] if is_sel else pal["edge"],
                       2.2 if (is_sel or has_err) else 1.0)

        icon = "⚡" if n.kind == "trigger" else ("⟳" if n.each is not None else "▸")
        text.draw(snap, n.x + 11, n.y + 14, f"{icon} {n.title}", 11.5, pal["text"],
                  bold=True, width=n.w - 22)
        text.draw(snap, n.x + 11, n.y + 28, n.subtitle or n.id, 9.0, pal["dim"],
                  width=n.w - 22)
        if n.isolated:
            # Yalıtılmış adım: çekirdek kısıtlaması altında çalışacak
            text.draw(snap, n.x + n.w - 34, n.y + 14, "▣", 12.0, pal["ok"],
                      width=24)

        for key, port in n.inputs.items():
            px, py = in_port_xy(n, key)
            col = type_color(port.type)
            conn = doc.connection_of(n.id, key)
            dim = bool(view.drag_wire) and (n.id, key) not in compatible
            port_glyph(snap, px, py, col, pal, T.is_list(port.type),
                       filled=bool(conn or port.required),
                       alpha=0.25 if dim else 1.0,
                       highlight=(n.id, key) in compatible)
            text.draw(snap, px + 12, py, port.label or key, 10.5, pal["text"],
                      width=n.w * 0.50, alpha=0.5 if dim else 1.0)
            if not conn or key in (WHEN, EACH):
                text.draw(snap, n.x + n.w * 0.54, py, _value_label(doc, n, key), 10.0,
                          pal["dim"], width=n.w * 0.46 - 16, align_right=True)

        for o, port in n.outputs.items():
            px, py = out_port_xy(n, o)
            col = type_color(port.type)
            dim = (bool(view.drag_wire) and (n.id, o) not in compatible
                   and view.drag_wire[0][2] == "in")
            port_glyph(snap, px, py, col, pal, T.is_list(port.type),
                       alpha=0.25 if dim else 1.0,
                       highlight=(n.id, o) in compatible)
            text.draw(snap, n.x + 10, py, f"{port.label or o} ·{T.label(port.type)}",
                      10.5, pal["dim"], width=n.w - 24, align_right=True)

    # port ipucu
    if view.hover_port:
        hid, hport, hdir = view.hover_port
        n = nodes.get(hid)
        if n and (hport in (n.inputs if hdir == "in" else n.outputs)):
            hx, hy = (in_port_xy(n, hport) if hdir == "in" else out_port_xy(n, hport))
            ports = n.inputs if hdir == "in" else n.outputs
            circle(snap, hx, hy, PORT_R + 3.5, pal["text"], 1.0, filled=False, width=1.6)
            tip = f"{hport} · {T.label(ports[hport].type)}"
            tw = 7.2 * len(tip) + 16
            ty = hy - 26
            tx = hx - (tw if hdir == "in" else 0)
            fill_rounded(snap, tx, ty - 11, tw, 22, 6, pal["node_hdr"], 0.98)
            stroke_rounded(snap, tx, ty - 11, tw, 22, 6, pal["edge"], 1.0)
            text.draw(snap, tx + 8, ty, tip, 10.0, pal["text"])

    snap.restore()

    # tip göstergesi
    lx = width - 14
    for t in reversed(["list<file>", "image", "file", "directory", "text",
                       "number", "bool"]):
        name = "liste" if T.is_list(t) else T.label(t)
        w_ = 7.6 * len(name) + 26
        lx -= w_
        port_glyph(snap, lx + 8, height - 18, type_color(t), pal, T.is_list(t))
        text.draw(snap, lx + 18, height - 18, name, 10.0, pal["dim"])


# -------------------------------------------------------------------- widget

class NodeCanvas(Gtk.Widget):
    __gtype_name__ = "AkisNodeCanvas"
    __gsignals__ = {
        "doc-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "notice": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, doc: Doc):
        super().__init__()
        self.doc = doc
        self.view = View()
        self.text = Text(self)
        self._drag_node: str | None = None
        self._drag_offset = (0.0, 0.0)
        self._drag_from: tuple | None = None
        self._pan_base: tuple | None = None
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_can_focus(True)

        click = Gtk.GestureClick(button=1)
        click.connect("pressed", self._on_press)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        pan = Gtk.GestureDrag(button=2)
        pan.connect("drag-begin", lambda *_: self._begin_pan())
        pan.connect("drag-update", self._on_pan)
        self.add_controller(pan)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ---------------------------------------------------------------- çizim

    def do_snapshot(self, snapshot):
        w = float(self.get_width() or 1200)
        h = float(self.get_height() or 700)
        self.view.dark = _is_dark()
        # özel widget kendi alanını kırpmazsa komşu panellerin üstüne taşar
        snapshot.push_clip(rect(0, 0, w, h))
        draw_scene(snapshot, w, h, self.doc, self.view, self.text)
        snapshot.pop()

    def refresh(self) -> None:
        self.view.error_nodes = {
            p.where.split(".")[0] for p in self.doc.flow.problems if p.level == "hata"
        }
        self.queue_draw()

    def _changed(self) -> None:
        self.refresh()
        self.emit("doc-changed")

    # ----------------------------------------------------------- hit testing

    def _nodes(self) -> dict[str, Node]:
        return prepare({n.id: n for n in self.doc.nodes()}, self.view)

    def _port_at(self, wx: float, wy: float):
        for n in self._nodes().values():
            for key in n.inputs:
                px, py = in_port_xy(n, key)
                if (px - wx) ** 2 + (py - wy) ** 2 <= GRAB_R ** 2:
                    return (n.id, key, "in")
            for o in n.outputs:
                px, py = out_port_xy(n, o)
                if (px - wx) ** 2 + (py - wy) ** 2 <= GRAB_R ** 2:
                    return (n.id, o, "out")
        return None

    def _node_at(self, wx: float, wy: float):
        for n in reversed(list(self._nodes().values())):
            if n.x <= wx <= n.x + n.w and n.y <= wy <= n.y + n.h:
                return n
        return None

    def select(self, node_id: str | None) -> None:
        self.view.selected = node_id
        self.emit("selection-changed", node_id or "")
        self.queue_draw()

    # --------------------------------------------------------------- olaylar

    def _on_press(self, gesture, n_press, x, y):
        self.grab_focus()
        wx, wy = self.view.to_world(x, y)
        port = self._port_at(wx, wy)
        if port:
            node_id, pname, direction = port
            if direction == "in" and self.doc.connection_of(node_id, pname):
                self.doc.disconnect(node_id, pname)
                self.emit("notice", f"{node_id}.{pname} kablosu koparıldı")
                self._changed()
                return
            self._drag_from = port
            self.view.drag_wire = (port, (wx, wy))
            self.queue_draw()
            return
        node = self._node_at(wx, wy)
        if node:
            self.select(node.id)
            self._drag_node = node.id
            self._drag_offset = (wx - node.x, wy - node.y)
        else:
            self.select(None)
            self._begin_pan()

    def _begin_pan(self):
        self._pan_base = (self.view.ox, self.view.oy)

    def _on_drag(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        wx, wy = self.view.to_world(sx + dx, sy + dy)
        if self._drag_from:
            self.view.drag_wire = (self._drag_from, (wx, wy))
            self.queue_draw()
        elif self._drag_node:
            ox, oy = self._drag_offset
            self.doc.set_pos(self._drag_node, wx - ox, wy - oy)
            self.queue_draw()
        elif self._pan_base:
            self.view.ox = self._pan_base[0] + dx
            self.view.oy = self._pan_base[1] + dy
            self.queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if self._drag_from and ok:
            wx, wy = self.view.to_world(sx + dx, sy + dy)
            self._finish_wire(self._port_at(wx, wy) or self._implicit_port(wx, wy))
        if self._drag_node:
            self.emit("doc-changed")
        self._drag_from = None
        self._drag_node = None
        self._pan_base = None
        self.view.drag_wire = None
        self.queue_draw()

    def _implicit_port(self, wx, wy):
        """Düğüm gövdesine bırakılan kablo uyan ilk boş porta takılır."""
        node = self._node_at(wx, wy)
        if not node or not self._drag_from:
            return None
        src_id, sport, direction = self._drag_from
        if direction == "out":
            for key in node.inputs:
                if key in (WHEN, EACH):
                    continue      # koşul/döngü kazara kurulmamalı, porta bırak
                if (not self.doc.connection_of(node.id, key)
                        and self.doc.can_connect(src_id, sport, node.id, key)[0]):
                    return (node.id, key, "in")
        else:
            for o in node.outputs:
                if self.doc.can_connect(node.id, o, src_id, sport)[0]:
                    return (node.id, o, "out")
        return None

    def _finish_wire(self, target):
        if not target or not self._drag_from:
            return
        a, b = self._drag_from, target
        if a[2] == b[2]:
            self.emit("notice", "aynı yönde iki port birbirine bağlanmaz")
            return
        src, dst = (a, b) if a[2] == "out" else (b, a)
        err = self.doc.connect(src[0], src[1], dst[0], dst[1])
        if err:
            self.emit("notice", err)
        else:
            self._changed()

    def _on_pan(self, gesture, dx, dy):
        if self._pan_base is None:
            self._begin_pan()
        self.view.ox = self._pan_base[0] + dx
        self.view.oy = self._pan_base[1] + dy
        self.queue_draw()

    def _on_motion(self, controller, x, y):
        wx, wy = self.view.to_world(x, y)
        port = self._port_at(wx, wy)
        if port != self.view.hover_port:
            self.view.hover_port = port
            self.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.view.zoom = max(0.35, min(2.5, self.view.zoom * (0.9 if dy > 0 else 1.1)))
        else:
            self.view.ox -= dx * 40
            self.view.oy -= dy * 40
        self.queue_draw()
        return True

    def _on_key(self, controller, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if name in ("Delete", "BackSpace") and self.view.selected not in (None, "", TRIGGER):
            removed = self.view.selected
            self.doc.remove_step(removed)
            self.select(None)
            self.emit("notice", f"{removed} silindi")
            self._changed()
            return True
        if ctrl and name == "z":
            if self.doc.undo():
                self.refresh()
                self.emit("doc-changed")
            return True
        if ctrl and name in ("0", "KP_0"):
            self.view.zoom, self.view.ox, self.view.oy = 1.0, 0.0, 0.0
            self.queue_draw()
            return True
        return False

    def fit(self, width: float = 0, height: float = 0) -> None:
        nodes = list(self._nodes().values())
        if not nodes:
            return
        width = width or (self.get_width() or 1200)
        height = height or (self.get_height() or 700)
        x0 = min(n.x for n in nodes) - 30
        y0 = min(n.y for n in nodes) - 30
        x1 = max(n.x + n.w for n in nodes) + 30
        y1 = max(n.y + n.h for n in nodes) + 50
        self.view.zoom = max(0.62, min(1.0, min(width / (x1 - x0), height / (y1 - y0))))
        self.view.ox = -x0 * self.view.zoom + max(
            0.0, (width - (x1 - x0) * self.view.zoom) / 2)
        self.view.oy = -y0 * self.view.zoom + max(
            0.0, (height - (y1 - y0) * self.view.zoom) / 2)
        self.queue_draw()


def _is_dark() -> bool:
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        return Adw.StyleManager.get_default().get_dark()
    except Exception:
        return True
