"""Editör uygulaması."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from .. import APP_ID
from ..registry import Registry
from .model import Doc
from .window import EditorWindow


class EditorApp(Adw.Application):
    def __init__(self, flow_path: Path | None = None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.flow_path = flow_path
        self.reg = Registry.discover()

    def do_activate(self):
        # Paketin kendi simge ağacını temaya ekle: kurulu olmayan bir
        # kopyadan çalışırken de uygulama simgesi görünsün.
        ekran = Gdk.Display.get_default()
        if ekran is not None:
            tema = Gtk.IconTheme.get_for_display(ekran)
            tema.add_search_path(
                str(Path(__file__).resolve().parents[1] / "veri" / "icons"))
        doc = (Doc.open(self.reg, self.flow_path) if self.flow_path
               else Doc(self.reg))
        win = EditorWindow(self, self.reg, doc)
        win.present()


def main(flow_path: str | None = None) -> int:
    path = Path(flow_path).expanduser() if flow_path else None
    if path and not path.is_file():
        print(f"akış bulunamadı: {path}", file=sys.stderr)
        return 1
    return EditorApp(path).run([])
