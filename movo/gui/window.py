"""Editör penceresi: palet | tuval | özellikler.

Altta akışın doğrulama durumu canlı durur -- editörde yapılan her değişiklik
aynı statik denetimden geçer; "kaydedip CLI'da kontrol etme" diye bir adım
yoktur.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

import gi
import yaml

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import units
from .. import consent
from ..manifest import perm_label
from ..registry import Registry
from ..runner import run as run_flow
from .canvas import NodeCanvas
from .inspector import Inspector
from .model import TRIGGER, Doc
from .palette import Palette


def flows_dir() -> Path:
    import os
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "movo" / "flows"


class EditorWindow(Adw.ApplicationWindow):
    __gtype_name__ = "AkisEditorWindow"

    def __init__(self, app, reg: Registry, doc: Doc):
        super().__init__(application=app, title="Akış")
        self.reg = reg
        self.doc = doc
        self.set_default_size(1380, 840)

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # --------------------------------------------------------- başlık
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=doc.name, subtitle="")
        header.set_title_widget(self.title)

        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Aç")
        open_btn.connect("clicked", lambda *_: self.open_dialog())
        header.pack_start(open_btn)
        save_btn = Gtk.Button(icon_name="document-save-symbolic",
                              tooltip_text="Kaydet (Ctrl+S)")
        save_btn.connect("clicked", lambda *_: self.save())
        header.pack_start(save_btn)

        run_btn = Gtk.Button(label="Çalıştır")
        run_btn.add_css_class("suggested-action")
        run_btn.connect("clicked", lambda *_: self.run(dry=False))
        header.pack_end(run_btn)
        dry_btn = Gtk.Button(label="Prova")
        dry_btn.set_tooltip_text("hiçbir şey yazmadan planı göster")
        dry_btn.connect("clicked", lambda *_: self.run(dry=True))
        header.pack_end(dry_btn)

        menu = Gio.Menu()
        menu.append("YAML görünümü", "win.yaml")
        menu.append("Tuvali sığdır", "win.fit")
        menu.append("Kur…", "win.install")
        menu.append("Yeni akış", "win.new")
        menu.append("Movo Automation Hakkında", "win.about")
        mbtn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(mbtn)
        view.add_top_bar(header)

        # ------------------------------------------------------ ana bölmeler
        self.canvas = NodeCanvas(doc)
        self.canvas.connect("doc-changed", lambda *_: self.on_doc_changed())
        self.canvas.connect("selection-changed", self.on_select)
        self.canvas.connect("notice", lambda _w, msg: self.toast(msg))

        self.palette = Palette(reg)
        self.palette.connect("add-action", self.on_add_action)

        self.inspector = Inspector(doc)
        self.inspector.connect("doc-changed", lambda *_: self.on_doc_changed())
        self.inspector.connect("notice", lambda _w, msg: self.toast(msg))
        self.inspector.connect("focus-node",
                               lambda _w, nid: self.canvas.select(nid or None))

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        center.append(self.canvas)
        center.append(self._status_bar())
        center.append(self._output_pane())

        inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          resize_start_child=True, shrink_start_child=False,
                          resize_end_child=False, shrink_end_child=False)
        inner.set_start_child(center)
        inner.set_end_child(self.inspector)
        self._inner = inner

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          shrink_start_child=False, resize_start_child=False)
        outer.set_start_child(self.palette)
        outer.set_end_child(inner)
        outer.set_position(258)
        view.set_content(outer)

        self._actions()
        self.on_doc_changed()

        def first_layout():
            width = self.get_width() or 1380
            inner.set_position(max(480, width - 258 - 348))
            self.canvas.fit()
            return False
        GLib.idle_add(first_layout)

    # ------------------------------------------------------------ alt çubuk

    def _status_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("toolbar")
        bar.set_margin_start(10)
        bar.set_margin_end(10)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        self.status = Gtk.Label(xalign=0, hexpand=True)
        self.status.set_ellipsize(3)
        bar.append(self.status)

        self.problem_btn = Gtk.MenuButton(label="sorunlar")
        self.problem_btn.add_css_class("flat")
        self.problem_pop = Gtk.Popover()
        self.problem_btn.set_popover(self.problem_pop)
        bar.append(self.problem_btn)

        out_btn = Gtk.ToggleButton(icon_name="utilities-terminal-symbolic",
                                   tooltip_text="çıktı")
        out_btn.add_css_class("flat")
        out_btn.connect("toggled", lambda w: self.output_revealer.set_reveal_child(
            w.get_active()))
        bar.append(out_btn)
        self.out_toggle = out_btn
        return bar

    def _output_pane(self) -> Gtk.Widget:
        self.output = Gtk.TextView(editable=False, monospace=True,
                                   wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.output.set_bottom_margin(8)
        self.output.set_top_margin(8)
        self.output.set_left_margin(10)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.output)
        scroller.set_size_request(-1, 190)
        self.output_revealer = Gtk.Revealer(child=scroller)
        self.output_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        return self.output_revealer

    def log(self, text: str) -> None:
        buf = self.output.get_buffer()
        buf.insert(buf.get_end_iter(), text + "\n")
        self.output.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0, 0)

    def toast(self, message: str) -> None:
        if message:
            self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    # ------------------------------------------------------------- eylemler

    def show_about(self, *_args) -> None:
        from .about import build_about

        build_about(self.reg).present(self)

    def _actions(self) -> None:
        for name, fn, accel in (
            ("yaml", self.show_yaml, "<Ctrl>y"),
            ("fit", lambda *_: self.canvas.fit(), "<Ctrl>0"),
            ("install", self.install, None),
            ("new", self.new_flow, "<Ctrl>n"),
            ("save", lambda *_: self.save(), "<Ctrl>s"),
            ("run", lambda *_: self.run(False), "<Ctrl>Return"),
            ("dry", lambda *_: self.run(True), "<Ctrl><Shift>Return"),
            ("undo", self.undo, "<Ctrl>z"),
            ("about", self.show_about, None),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *a, fn=fn: fn())
            self.add_action(act)
            if accel:
                self.get_application().set_accels_for_action(f"win.{name}", [accel])

    def undo(self, *_):
        if self.doc.undo():
            self.on_doc_changed()
            self.toast("geri alındı")

    # ------------------------------------------------------------ durum akışı

    def on_doc_changed(self) -> None:
        flow = self.doc.validate()
        self.canvas.refresh()
        errors = [p for p in flow.problems if p.level == "hata"]
        warns = [p for p in flow.problems if p.level == "uyarı"]
        if errors:
            self.status.set_markup(
                f"<span foreground='#e05a5a'>✗ {len(errors)} hata</span>"
                f" — {errors[0].where}: {GLib.markup_escape_text(errors[0].message)}")
        else:
            perms = ", ".join(flow.permissions(self.reg)) or "izin istemiyor"
            # Düzenlemek onayı bozabilir; bunu kaydetmeden önce görmek
            # işe yarıyor, çünkü arka planda koşan akış durur.
            onay = {"var": "<span foreground='#4caf6d'>onaylı</span>",
                    "bayat": "<span foreground='#d8a657'>onay bayat</span>",
                    "yok": "<span foreground='#8a8f98'>onaysız</span>"}[
                consent.check(consent.ask_for(flow, self.reg))[0]]
            self.status.set_markup(
                f"<span foreground='#4caf6d'>✓ geçerli</span> — "
                f"{len(flow.steps)} adım · {perms} · {onay}")
        self.problem_btn.set_visible(bool(flow.problems))
        self.problem_btn.set_label(f"{len(errors)} hata, {len(warns)} uyarı")
        self._fill_problems(flow.problems)
        self.title.set_title(self.doc.name)
        path = self.doc.path
        self.title.set_subtitle(
            ("• " if self.doc.dirty else "") + (path.name if path else "kaydedilmedi"))
        self.title.set_tooltip_text(str(path) if path else "")
        if self.inspector.node_id and not (
                self.inspector.node_id == TRIGGER
                or self.doc.step(self.inspector.node_id)):
            self.inspector.set_node(None)

    def _fill_problems(self, problems) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        for p in problems[:12]:
            btn = Gtk.Button(has_frame=False)
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(
                f"<b>{'⛔' if p.level == 'hata' else '⚠'} {GLib.markup_escape_text(p.where)}</b>  "
                f"{GLib.markup_escape_text(p.message)}")
            lbl.set_wrap(True)
            lbl.set_max_width_chars(60)
            btn.set_child(lbl)
            node = p.where.split(".")[0]
            btn.connect("clicked", lambda _b, n=node: self.canvas.select(n))
            box.append(btn)
        if not problems:
            box.append(Gtk.Label(label="sorun yok"))
        self.problem_pop.set_child(box)

    def on_select(self, _canvas, node_id: str) -> None:
        self.inspector.set_node(node_id or None)

    def on_add_action(self, _palette, action_id: str) -> None:
        v = self.canvas.view
        w = self.canvas.get_width() or 900
        h = self.canvas.get_height() or 600
        x, y = v.to_world(w * 0.45, h * 0.35)
        sid = self.doc.add_step(action_id, x, y)
        self.on_doc_changed()
        self.canvas.select(sid)
        self.toast(f"{sid} eklendi")

    # ------------------------------------------------------------ dosya

    def open_dialog(self) -> None:
        dialog = Gtk.FileDialog(title="Akış aç")
        filt = Gtk.FileFilter()
        filt.set_name("Akış dosyaları")
        filt.add_pattern("*.yaml")
        filt.add_pattern("*.yml")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)
        d = flows_dir()
        if d.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(d)))

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.load(Path(f.get_path()))
        dialog.open(self, None, done)

    def load(self, path: Path) -> None:
        try:
            doc = Doc.open(self.reg, path)
        except Exception as e:
            self.toast(f"açılamadı: {e}")
            return
        self.doc = doc
        self.canvas.doc = doc
        self.inspector.doc = doc
        self.inspector.set_node(None)
        self.on_doc_changed()
        self.canvas.fit()
        self.toast(f"{path.name} açıldı")

    def new_flow(self, *_):
        self.doc = Doc(self.reg)
        self.canvas.doc = self.doc
        self.inspector.doc = self.doc
        self.inspector.set_node(None)
        self.on_doc_changed()
        self.toast("yeni akış")

    def save(self) -> None:
        if self.doc.path:
            self.doc.save()
            self.on_doc_changed()
            self.toast(f"kaydedildi: {self.doc.path}")
            return
        dialog = Gtk.FileDialog(title="Akışı kaydet")
        from ..flow import slugify
        dialog.set_initial_name(f"{slugify(self.doc.name)}.yaml")
        d = flows_dir()
        d.mkdir(parents=True, exist_ok=True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(d)))

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.doc.save(Path(f.get_path()))
                self.on_doc_changed()
                self.toast(f"kaydedildi: {self.doc.path}")
        dialog.save(self, None, done)

    # -------------------------------------------------------------- çalıştır

    def run(self, dry: bool) -> None:
        flow = self.doc.validate()
        if not flow.ok:
            self.toast("önce hataları düzelt")
            self.problem_btn.popup()
            return
        if "file" in flow.trigger_outputs():
            self._pick_input(dry)
        else:
            self._run_now(dry, {})

    def _pick_input(self, dry: bool) -> None:
        dialog = Gtk.FileDialog(title="Hangi dosyayla denensin?")
        path = (self.doc.raw.get("trigger") or {}).get("path")
        if path:
            p = Path(str(path)).expanduser()
            if p.is_dir():
                dialog.set_initial_folder(Gio.File.new_for_path(str(p)))

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            p = Path(f.get_path())
            self._run_now(dry, {"file": str(p), "filename": p.name,
                                "directory": str(p.parent)})
        dialog.open(self, None, done)

    def _run_now(self, dry: bool, trigger: dict) -> None:
        self.out_toggle.set_active(True)
        self.log(("— prova: " if dry else "— çalıştır: ") + self.doc.name)
        flow = self.doc.flow

        def work():
            try:
                res = run_flow(flow, self.reg, trigger, dry_run=dry, quiet=True)
            except Exception:
                GLib.idle_add(self.log, traceback.format_exc())
                return
            GLib.idle_add(self._show_result, res, dry)

        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, res, dry: bool) -> None:
        for s in res.steps:
            mark = {"tamam": "✓", "plan": "·", "hata": "✗", "atlandı": "–"}[s.status]
            self.log(f"  {mark} {s.id} ({s.action})")
            for line in s.logs:
                self.log(f"      {line}")
            if s.error:
                self.log(f"      {s.error}")
        self.log(("✓ " if res.ok else "✗ ") + res.summary())
        self.toast(("prova tamam" if dry else "akış tamamlandı") if res.ok
                   else "akış hatayla durdu")
        return False

    # ------------------------------------------------------------------ kur

    def install(self, *_):
        flow = self.doc.validate()
        if not flow.ok:
            self.toast("önce hataları düzelt")
            return
        if not self.doc.path:
            self.toast("önce akışı kaydet")
            return
        perms = flow.permissions(self.reg)
        body = "\n".join(f"• {perm_label(p)}  ({', '.join(s)})" for p, s in perms.items())
        rendered = units.render(flow)
        if flow.trigger_type == "network":
            body += ("\n\nAğ tetikleyicisi root yetkisi ister; dosya hazırlanacak "
                     "ve kurulum komutu çıktı bölmesine yazılacak.")
        else:
            body += "\n\nYazılacak birimler:\n" + "\n".join(f"• {n}" for n in rendered)

        dialog = Adw.AlertDialog(heading=f"'{flow.name}' kurulsun mu?", body=body)
        dialog.add_response("cancel", "Vazgeç")
        dialog.add_response("install", "Kur")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def answered(dlg, response):
            if response != "install":
                return
            self.out_toggle.set_active(True)
            if flow.trigger_type == "network":
                staged = units.state_dir() / units.dispatcher_path(flow).name
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_text(units.dispatcher_script(flow), encoding="utf-8")
                staged.chmod(0o755)
                self.log(f"  hazırlandı: {staged}")
                self.log(f"  sudo install -m 755 {staged} {units.dispatcher_path(flow)}")
                self.toast("dispatcher script'i hazır, kurulum komutu çıktıda")
                return
            try:
                written = units.install(flow)
            except Exception as e:
                self.log(f"  kurulum hatası: {e}")
                self.toast("kurulamadı")
                return
            for p in written:
                self.log(f"  yazıldı: {p}")
            for name, st in units.status(flow).items():
                self.log(f"  {name}: {st}")
            self.toast("kuruldu")
        dialog.connect("response", answered)
        dialog.present(self)

    # ----------------------------------------------------------- YAML görünümü

    def show_yaml(self, *_):
        dialog = Adw.Dialog(title="YAML", content_width=760, content_height=640)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        apply_btn = Gtk.Button(label="Uygula")
        apply_btn.add_css_class("suggested-action")
        header.pack_end(apply_btn)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="kopyala")
        header.pack_start(copy_btn)
        view.add_top_bar(header)

        text = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        text.set_left_margin(10)
        text.set_top_margin(8)
        text.get_buffer().set_text(self.doc.to_yaml())
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(text)
        view.set_content(scroller)
        dialog.set_child(view)

        def copy(*_):
            buf = text.get_buffer()
            body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            self.get_clipboard().set(body)
            self.toast("panoya kopyalandı")
        copy_btn.connect("clicked", copy)

        def apply(*_):
            buf = text.get_buffer()
            body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            try:
                raw = yaml.safe_load(body) or {}
                if not isinstance(raw, dict):
                    raise ValueError("akış bir sözlük olmalı")
            except Exception as e:
                self.toast(f"YAML okunamadı: {e}")
                return
            self.doc.snapshot()
            layout = self.doc.raw.get("layout")
            self.doc.raw = raw
            if layout and "layout" not in raw:
                self.doc.raw["layout"] = layout
            self.doc.dirty = True
            self.on_doc_changed()
            self.canvas.queue_draw()
            dialog.close()
            self.toast("YAML uygulandı")
        apply_btn.connect("clicked", apply)
        dialog.present(self)
