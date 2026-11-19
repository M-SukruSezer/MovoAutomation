"""Eylem paleti: kayıtlı eylem sözlüğünün gezilebilir hali.

Palet, manifest arama yolundan gelir. Bir uygulama kendi
``*.action.yaml`` dosyasını kurduğu anda burada belirir -- editörde onun için
yazılmış tek satır kod yoktur.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from .. import dbus as dbuslib
from ..manifest import perm_label
from ..registry import Registry
from .canvas import type_color


def _dot(color) -> Gtk.DrawingArea:
    w = Gtk.Label(label="●")
    w.add_css_class("dim-label")
    r, g, b = color
    w.set_attributes(None)
    css = Gtk.CssProvider()
    css.load_from_string(
        f"label {{ color: rgb({int(r*255)},{int(g*255)},{int(b*255)}); }}")
    w.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return w


class Palette(Gtk.Box):
    __gtype_name__ = "AkisPalette"
    __gsignals__ = {
        "add-action": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, reg: Registry):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.reg = reg
        self.set_size_request(258, -1)

        self.search = Gtk.SearchEntry(placeholder_text="eylem ara")
        self.search.set_margin_top(8)
        self.search.set_margin_start(8)
        self.search.set_margin_end(8)
        self.search.set_margin_bottom(8)
        self.search.connect("search-changed", lambda *_: self._fill())
        self.append(self.search)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._activated)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.list)
        self.append(scroller)

        hint = Gtk.Label(
            label="eylemi eklemek için çift tıkla\nportları sürükleyerek bağla",
            justify=Gtk.Justification.CENTER)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        hint.set_margin_top(6)
        hint.set_margin_bottom(8)
        self.append(hint)

        self._fill()

    def _fill(self) -> None:
        query = self.search.get_text().strip().lower()
        self.list.remove_all()
        for cat, actions in self.reg.by_category().items():
            shown = [a for a in actions
                     if not query or query in a.id.lower() or query in a.name.lower()
                     or query in a.summary.lower()]
            if not shown:
                continue
            header = Gtk.Label(label=cat.upper(), xalign=0)
            header.add_css_class("caption-heading")
            header.add_css_class("dim-label")
            header.set_margin_top(10)
            header.set_margin_start(12)
            header.set_margin_bottom(2)
            row = Gtk.ListBoxRow(activatable=False, selectable=False, child=header)
            self.list.append(row)
            for a in shown:
                self.list.append(self._row(a))

    def _row(self, action) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_margin_start(12)
        box.set_margin_end(8)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=action.name, xalign=0, hexpand=True)
        title.set_ellipsize(3)
        top.append(title)
        # Servisi bus'ta olmayan D-Bus blokları soluk: palete bakan kişi
        # bu makinede neyin çalışacağını eylemi sürüklemeden görsün.
        absent = (action.impl_kind == "dbus"
                  and dbuslib.present(action.impl.pattern, action.impl.bus) is False)
        if absent:
            title.add_css_class("dim-label")
            sign = Gtk.Label(label="○")
            sign.add_css_class("caption")
            sign.add_css_class("dim-label")
            top.append(sign)
        for name, port in list(action.outputs.items())[:3]:
            top.append(_dot(type_color(port.type)))
        box.append(top)

        sub = Gtk.Label(label=action.id, xalign=0)
        sub.add_css_class("caption")
        sub.add_css_class("dim-label")
        box.append(sub)

        row = Gtk.ListBoxRow(child=box)
        row.action_id = action.id
        tip = action.summary or action.name
        if absent:
            tip += (f"\n\n○ {action.impl.pattern} şu an "
                    f"{dbuslib.BUS_LABEL[action.impl.bus]} bus'ında yok")
        if action.permissions:
            tip += "\n\nizinler: " + ", ".join(perm_label(p) for p in action.permissions)
        row.set_tooltip_text(tip)
        return row

    def _activated(self, listbox, row) -> None:
        if getattr(row, "action_id", None):
            self.emit("add-action", row.action_id)
