"""Özellik paneli: seçili düğümün portlarını tipine uygun denetimlerle gösterir.

Bir port ya bir kabloyla beslenir ya da burada elle doldurulur; panel ikisini
tek listede gösterir, çünkü akış dosyasında da ikisi aynı alandır.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from ..flow import TRIGGERS
from .. import cond as C
from .. import types as T
from .. import dbus as dbuslib
from .. import isolate as iso
from ..manifest import perm_label
from .model import TRIGGER, Doc

TRIGGER_FIELD_LABELS = {
    "path": "İzlenecek klasör",
    "match": "Dosya deseni",
    "produces": "Klasördeki içerik",
    "events": "Olaylar",
    "settle_ms": "Yazma bitişini bekle (ms)",
    "ignore_existing": "Mevcut dosyaları atla",
    "on_calendar": "Zaman (systemd OnCalendar)",
    "persistent": "Kaçırılan çalıştırmayı telafi et",
    "connection": "Bağlantı adı",
    "state": "Olay",
    "file": "Dosya",
    "bus": "Bus",
    "service": "Servis adı",
    "object": "Nesne yolu",
    "interface": "Arayüz",
    "member": "Sinyal adı",
    "property": "Özellik adı",
    "args": "Sinyal argümanları",
}
BUS_CHOICES = ["session", "system"]

#: Aynı anahtar tetikleyiciye göre başka şey anlatır: klasörde 'produces'
#: dosyanın türü, D-Bus'ta özelliğin tipi.
TRIGGER_LABEL_OVERRIDES = {
    "dbus": {
        "produces": "Özelliğin tipi",
        "settle_ms": "Olayları birleştir (ms)",
    },
}
PROPERTY_TYPES = ["text", "number", "bool"]
PRODUCES_CHOICES = ["file", "image", "audio", "video"]


def esc(text: str) -> str:
    """Adw satır başlıkları Pango biçimlemesi okur; '<' içeren koşul metni
    oraya ham girerse satır sessizce boş kalır."""
    return GLib.markup_escape_text(str(text))


def _literal(text: str):
    """Panelde yazılanı YAML'ın göreceği türe çevirir: 20 sayı, evet doğru."""
    t = text.strip()
    if t.lower() in ("evet", "true", "doğru"):
        return True
    if t.lower() in ("hayır", "hayir", "false", "yanlış"):
        return False
    try:
        return int(t) if t.lstrip("-").isdigit() else float(t)
    except ValueError:
        return text
STATE_CHOICES = ["up", "down"]


class Inspector(Gtk.Box):
    __gtype_name__ = "AkisInspector"
    __gsignals__ = {
        "doc-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "notice": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "focus-node": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, doc: Doc):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.doc = doc
        self.node_id: str | None = None
        self._building = False
        self.set_size_request(330, -1)
        self.page = Adw.PreferencesPage()
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.page)
        self.append(scroller)
        self._groups = []
        self.set_node(None)

    # ------------------------------------------------------------- yardımcı

    def _clear(self) -> None:
        for grp in list(getattr(self, "_groups", [])):
            self.page.remove(grp)
        self._groups = []

    def _group(self, title: str, desc: str = "") -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=title, description=desc)
        self.page.add(g)
        self._groups.append(g)
        return g

    def _changed(self) -> None:
        self.emit("doc-changed")

    # ------------------------------------------------------------------ ana

    def set_node(self, node_id: str | None) -> None:
        self._building = True
        self.node_id = node_id
        self._clear()
        if node_id == TRIGGER:
            self._show_trigger()
        elif node_id and self.doc.step(node_id):
            self._show_step(node_id)
        else:
            self._show_flow()
        self._building = False

    # ------------------------------------------------------------ akış geneli

    def _show_flow(self) -> None:
        g = self._group("Akış", "Düğüm seçilmedi")
        name = Adw.EntryRow(title="Ad")
        name.set_text(self.doc.name)
        name.connect("changed", lambda w: self._set_name(w.get_text()))
        g.add(name)

        info = Adw.ActionRow(title="Adım sayısı", subtitle=str(len(self.doc.steps())))
        g.add(info)
        path = Adw.ActionRow(title="Dosya",
                             subtitle=str(self.doc.path or "henüz kaydedilmedi"))
        path.set_subtitle_lines(2)
        g.add(path)

        perms = self.doc.flow.permissions(self.doc.reg)
        if perms:
            pg = self._group("Bu akış şunları yapabilir")
            for p, steps in perms.items():
                pg.add(Adw.ActionRow(title=perm_label(p),
                                     subtitle=f"{p} · {', '.join(steps)}"))

    def _set_name(self, value: str) -> None:
        if self._building or value == self.doc.name:
            return
        self.doc.raw["name"] = value
        self.doc.dirty = True
        self._changed()

    # ------------------------------------------------------------ tetikleyici

    def _show_trigger(self) -> None:
        ttype = self.doc.trigger_type
        spec = TRIGGERS[ttype]
        g = self._group("Tetikleyici", spec["label"])

        combo = Adw.ComboRow(title="Tür")
        model = Gtk.StringList()
        keys = list(TRIGGERS)
        for k in keys:
            model.append(f"{TRIGGERS[k]['label']}  ({k})")
        combo.set_model(model)
        combo.set_selected(keys.index(ttype))
        combo.connect("notify::selected",
                      lambda w, _: self._set_trigger_type(keys[w.get_selected()]))
        g.add(combo)

        fields = list(spec["config"]) + list(spec["optional"])
        conf = {k: v for k, v in (self.doc.raw.get("trigger") or {}).items() if k != "type"}
        for key in fields:
            required = key in spec["config"]
            label = TRIGGER_LABEL_OVERRIDES.get(ttype, {}).get(
                key, TRIGGER_FIELD_LABELS.get(key, key))
            value = conf.get(key)
            if key == "produces":
                choices = PROPERTY_TYPES if ttype == "dbus" else PRODUCES_CHOICES
                note = ("sinyalden gelen değerin tipi" if ttype == "dbus"
                        else "tip sözü; çalışma anında doğrulanır")
                g.add(self._choice_row(label, choices, value or choices[0],
                                       lambda v: self.doc.set_trigger_field("produces", v),
                                       subtitle=note))
            elif key == "bus":
                g.add(self._choice_row(label, BUS_CHOICES, value or "session",
                                       lambda v: self.doc.set_trigger_field("bus", v)))
            elif key == "args":
                # ad -> tip eşlemesi; tek satırlık bir alana sığmaz,
                # YAML görünümünden düzenlenir
                row = Adw.ActionRow(
                    title=label,
                    subtitle=(", ".join(f"{k}: {v}" for k, v in value.items())
                              if isinstance(value, dict) and value
                              else "yok — Ctrl+Y ile YAML'dan ekleyin"))
                row.set_subtitle_lines(2)
                g.add(row)
            elif key == "state" and ttype == "network":
                g.add(self._choice_row(label, STATE_CHOICES, value or "up",
                                       lambda v: self.doc.set_trigger_field("state", v)))
            elif key in ("ignore_existing", "persistent"):
                row = Adw.SwitchRow(title=label)
                row.set_active(bool(value) if value is not None else True)
                row.connect("notify::active",
                            lambda w, _, k=key: self._trigger_field(k, w.get_active()))
                g.add(row)
            elif key == "settle_ms":
                row = Adw.SpinRow.new_with_range(0, 5000, 50)
                row.set_title(label)
                row.set_value(float(value if value is not None
                                    else (0 if ttype == "dbus" else 300)))
                row.connect("notify::value",
                            lambda w, _, k=key: self._trigger_field(k, int(w.get_value())))
                g.add(row)
            else:
                row = Adw.EntryRow(title=label + ("" if required else "  (isteğe bağlı)"))
                row.set_text("" if value is None else str(value))
                row.connect("changed",
                            lambda w, k=key: self._trigger_field(k, w.get_text()))
                if key == "path":
                    row.add_suffix(self._browse_button(row, folder=True))
                g.add(row)

        # D-Bus tetikleyicisinin çıktıları sabit değil: ne dinlediğine bağlı.
        og = self._group("Akışa verdiği değerler")
        outs = self.doc.flow.trigger_outputs()
        if not outs:
            og.add(Adw.ActionRow(title="yok", subtitle="bu tetikleyici değer vermiyor"))
        for name, t in outs.items():
            actual = self.doc.flow.port_types.get((TRIGGER, name), t)
            og.add(Adw.ActionRow(title=name, subtitle=T.label(actual)))

    def _set_trigger_type(self, ttype: str) -> None:
        if self._building or ttype == self.doc.trigger_type:
            return
        self.doc.set_trigger(ttype)
        self._changed()
        self.set_node(TRIGGER)

    def _trigger_field(self, key: str, value) -> None:
        if self._building:
            return
        self.doc.set_trigger_field(key, value)
        self._changed()

    # ------------------------------------------------------------------ adım

    def _show_step(self, node_id: str) -> None:
        step = self.doc.step(node_id)
        action_id = str(step.get("action", ""))
        act = self.doc.reg.actions.get(action_id)
        g = self._group(act.name if act else action_id, act.summary if act else "")

        ident = Adw.EntryRow(title="Adım kimliği")
        ident.set_text(node_id)
        ident.connect("apply", lambda w: self._rename(node_id, w.get_text()))
        ident.set_show_apply_button(True)
        g.add(ident)

        if not act:
            g.add(Adw.ActionRow(title="Bilinmeyen eylem", subtitle=action_id))
            return

        ig = self._group("Girdiler")
        for key, port in act.inputs.items():
            ig.add(self._input_row(node_id, key, port))

        self._loop_group(node_id)
        self._cond_group(node_id)
        self._error_group(node_id)

        og = self._group("Çıktılar", "bağlamak için tuvalde sağdaki noktayı sürükle")
        for name, port in act.outputs.items():
            og.add(Adw.ActionRow(title=port.label or name, subtitle=f"{name} · {port.type}"))

        ag = self._group("Eylem")
        if act.impl_kind == "dbus":
            # Bu blok bir sistem servisiyle konuşuyor; adresi ve servisin
            # şu an bus'ta olup olmadığı burada görünür.
            call = act.impl
            ready = dbuslib.present(call.pattern, call.bus)
            state = {True: "● hazır", False: "○ servis şu an yok",
                     None: "? bus'a bakılamadı"}[ready]
            row = Adw.ActionRow(
                title=f"D-Bus · {dbuslib.BUS_LABEL[call.bus]} bus",
                subtitle=f"{call.label()}\n{state}")
            row.set_subtitle_lines(3)
            ag.add(row)
        if act.permissions:
            ag.add(Adw.ActionRow(
                title="İzinler",
                subtitle=", ".join(perm_label(p) for p in act.permissions)))

        # İzinler bir sözleşme; bu anahtar onu çekirdeğe onaylatıyor.
        good, why = iso.available()
        sw = Adw.SwitchRow(
            title="Çekirdek düzeyinde yalıt",
            subtitle=("adım ayrı bir süreçte, yalnızca beyan ettiği yollara "
                      f"erişerek çalışır · {why}") if good else
                     f"bu çekirdekte kurulamaz · {why}")
        sw.set_subtitle_lines(3)
        sw.set_sensitive(good)
        sw.set_active(self.doc.isolated(node_id))
        sw.connect("notify::active", lambda w, _p: self._set_isolated(
            node_id, w.get_active()))
        ag.add(sw)
        src = Adw.ActionRow(title="Manifest", subtitle=str(act.source or "—"))
        src.set_subtitle_lines(3)
        ag.add(src)

        remove = Gtk.Button(label="Adımı sil")
        remove.add_css_class("destructive-action")
        remove.set_margin_top(6)
        remove.connect("clicked", lambda *_: self._remove(node_id))
        box = Adw.PreferencesGroup()
        box.add(remove)
        self.page.add(box)
        self._groups.append(box)

    # ------------------------------------------------------------------ döngü

    def _loop_group(self, node_id: str) -> None:
        """Döngü kaynağı ve bir öge patlarsa ne olacağı."""
        each = self.doc.each_of(node_id)
        g = self._group("Döngü", "boşsa adım bir kez çalışır")
        if each is None:
            hint = Adw.ActionRow(
                title="Döngü yok",
                subtitle="bir liste çıktısını 'her öge' portuna sürükleyin")
            hint.set_subtitle_lines(2)
            g.add(hint)
            return

        src = (f"{len(each)} sabit öge" if isinstance(each, list)
               else esc(str(each).strip("{} ")))
        et = self.doc.elem_type(node_id)
        row = Adw.ActionRow(title="Kaynak", subtitle=src)
        row.set_subtitle_lines(2)
        g.add(row)
        if et:
            g.add(Adw.ActionRow(
                title="Öge tipi",
                subtitle=f"{T.label(et)} — adım içinde {{{{ item.value }}}}"))

        secenekler = [1, 2, 4, 8, "auto"]
        etiketler = ["tek tek (sırayla)", "2 paralel", "4 paralel", "8 paralel",
                     "çekirdek sayısı kadar"]
        cur = self.doc.workers(node_id)
        combo = Adw.ComboRow(title="Aynı anda kaç öge",
                             subtitle="paralellik sırayı bozmaz; çıktı listesi "
                                      "girdi sırasını korur",
                             model=Gtk.StringList.new(etiketler))
        combo.set_subtitle_lines(2)
        combo.set_selected(secenekler.index(cur) if cur in secenekler else 0)
        combo.connect("notify::selected", lambda w, _p: self._set_workers(
            node_id, secenekler[w.get_selected()]))
        g.add(combo)

        row = Adw.ActionRow(title="Döngüyü kaldır",
                            subtitle="adım tek seferlik çalışır")
        btn = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.connect("clicked", lambda *_: self._clear_each(node_id))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        g.add(row)

    def _error_group(self, node_id: str) -> None:
        """Adım patlarsa ne olacak? Her adımın sorusu, yalnızca döngünün değil."""
        loops = self.doc.each_of(node_id) is not None
        pols = ["dur", "devam"] + (["atla"] if loops else [])
        labels = ["akışı durdur", "hatayı yakala, akış sürsün"]
        if loops:
            labels.append("o ögeyi atla, döngü sürsün")

        g = self._group("Hata olursa")
        cur = self.doc.on_error(node_id)
        combo = Adw.ComboRow(title="Davranış",
                             model=Gtk.StringList.new(labels))
        combo.set_selected(pols.index(cur) if cur in pols else 0)
        combo.connect("notify::selected", lambda w, _p: self._set_policy(
            node_id, pols[w.get_selected()]))
        g.add(combo)

        if cur == "devam":
            row = Adw.ActionRow(
                title="Kazanılan çıktılar",
                subtitle="tamam (doğru/yanlış) ve hata (metin) — "
                         "bunları okuyan adım hata dalını kurar")
            row.set_subtitle_lines(3)
            g.add(row)
        elif cur == "atla":
            row = Adw.ActionRow(
                title="Not",
                subtitle="patlayan öge listeden düşer; adımın kendisi başarılı sayılır")
            row.set_subtitle_lines(2)
            g.add(row)

    def _set_isolated(self, node_id: str, value: bool) -> None:
        if self._building:
            return
        self.doc.set_isolated(node_id, value)
        self._changed()

    def _set_workers(self, node_id: str, value) -> None:
        if self._building:
            return
        self.doc.set_workers(node_id, value)
        self._changed()
        self.set_node(node_id)

    def _set_policy(self, node_id: str, value: str) -> None:
        if self._building:
            return
        self.doc.set_on_error(node_id, value)
        self._changed()
        self.set_node(node_id)        # portlar değişmiş olabilir

    def _clear_each(self, node_id: str) -> None:
        self.doc.clear_each(node_id)
        self._changed()
        self.set_node(node_id)

    # ------------------------------------------------------------------ koşul

    def _cond_group(self, node_id: str) -> None:
        """Adımın çalışma koşulu. Sol taraf kablodan gelir, test burada seçilir."""
        guard = self.doc.guard(node_id)
        g = self._group("Koşul", "boşsa adım her çalıştırmada yürür")

        if guard is None:
            hint = Adw.ActionRow(
                title="Koşul yok",
                subtitle="bir çıktıyı düğümün 'koşul' portuna sürükleyin")
            hint.set_subtitle_lines(2)
            g.add(hint)
            return

        cond = self.doc.single_cond(node_id)
        if cond is None:
            # elle yazılmış çok koşullu guard: bozmamak için salt okunur
            row = Adw.ActionRow(
                title=esc(C.describe(guard)),
                subtitle="çok koşullu — YAML görünümünden (Ctrl+Y) düzenleyin")
            row.set_subtitle_lines(2)
            g.add(row)
            g.add(self._cond_remove(node_id))
            return

        g.add(Adw.ActionRow(title="Sol taraf", subtitle=esc(C.show(cond.left))))

        ops = list(C.OPS)
        combo = Adw.ComboRow(title="Test",
                             model=Gtk.StringList.new(
                                 [esc(o if o == C.OPS[o]["label"]
                                      else f"{o}  ({C.OPS[o]['label']})")
                                  for o in ops]))
        combo.set_selected(ops.index(cond.op))
        combo.connect("notify::selected", lambda w, _p: self._set_cond(
            node_id, op=ops[w.get_selected()]))
        g.add(combo)

        if not cond.unary:
            entry = Adw.EntryRow(title="Karşılaştırılan değer")
            entry.set_text("" if cond.right is None else str(cond.right))
            entry.set_show_apply_button(True)
            entry.connect("apply", lambda w: self._set_cond(
                node_id, right=_literal(w.get_text())))
            g.add(entry)

        g.add(self._cond_remove(node_id))

    def _cond_remove(self, node_id: str) -> Gtk.Widget:
        row = Adw.ActionRow(title="Koşulu kaldır",
                            subtitle="adım koşulsuz çalışır")
        btn = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.connect("clicked", lambda *_: self._clear_cond(node_id))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        return row

    def _set_cond(self, node_id: str, **kw) -> None:
        if self._building:
            return
        err = self.doc.set_cond(node_id, **kw)
        if err:
            self.emit("notice", err)
            return
        self._changed()
        self.set_node(node_id)

    def _clear_cond(self, node_id: str) -> None:
        self.doc.clear_guard(node_id)
        self._changed()
        self.set_node(node_id)

    def _rename(self, node_id: str, new_id: str) -> None:
        err = self.doc.rename_step(node_id, new_id)
        if err:
            self.emit("notice", err)
            return
        self._changed()
        self.emit("focus-node", self.doc.step(new_id) and new_id or "")

    def _remove(self, node_id: str) -> None:
        self.doc.remove_step(node_id)
        self._changed()
        self.emit("focus-node", "")

    # ----------------------------------------------------------- girdi satırı

    def _input_row(self, node_id: str, key: str, port) -> Gtk.Widget:
        conn = self.doc.connection_of(node_id, key)
        if conn:
            src, out = conn
            row = Adw.ActionRow(title=port.label or key,
                                subtitle=f"⟵  {src}.{out}  ({port.type})")
            row.add_css_class("property")
            btn = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
            btn.set_tooltip_text("kabloyu kopar")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: self._disconnect(node_id, key))
            row.add_suffix(btn)
            return row

        args = step_args = (self.doc.step(node_id) or {}).get("with") or {}
        value = args.get(key, port.default)
        title = (port.label or key) + ("  *" if port.required else "")

        if port.type == "bool":
            row = Adw.SwitchRow(title=title)
            row.set_active(bool(value))
            row.connect("notify::active",
                        lambda w, _: self._set(node_id, key, w.get_active()))
            return row
        if port.choices:
            return self._choice_row(
                title, [str(c) for c in port.choices],
                str(value if value is not None else port.choices[0]),
                lambda v: self._set(node_id, key, v))
        if port.type == "number":
            try:
                number = float(value if value is not None else 0)
            except (TypeError, ValueError):
                # sayıya dönüşmeyen değeri gizlemek yerine ham haliyle göster
                row = Adw.EntryRow(title=title + "  ⛔ sayı değil")
                row.set_text(str(value))
                row.add_css_class("error")
                row.connect("changed", lambda w: self._set(node_id, key, w.get_text()))
                return row
            row = Adw.SpinRow.new_with_range(-1e6, 1e6, 1)
            row.set_title(title)
            row.set_value(number)
            row.connect("notify::value",
                        lambda w, _: self._set(node_id, key, int(w.get_value())))
            return row

        row = Adw.EntryRow(title=title)
        row.set_text("" if value is None else str(value))
        row.connect("changed", lambda w: self._set(node_id, key, w.get_text()))
        if port.type in ("directory", "file", "image", "path"):
            row.add_suffix(self._browse_button(row, folder=port.type == "directory"))
        return row

    def _choice_row(self, title: str, choices: list[str], current: str,
                    on_change, subtitle: str = "") -> Adw.ComboRow:
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        model = Gtk.StringList()
        for c in choices:
            model.append(c)
        row.set_model(model)
        row.set_selected(choices.index(current) if current in choices else 0)

        def changed(w, _):
            if not self._building:
                on_change(choices[w.get_selected()])
                self._changed()
        row.connect("notify::selected", changed)
        return row

    def _browse_button(self, row: Adw.EntryRow, folder: bool) -> Gtk.Button:
        btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.set_tooltip_text("gözat")

        def pick(*_):
            dialog = Gtk.FileDialog()
            window = self.get_root()

            def done(dlg, res):
                try:
                    f = (dlg.select_folder_finish(res) if folder
                         else dlg.open_finish(res))
                except Exception:
                    return
                if f:
                    row.set_text(f.get_path() or "")
            start = row.get_text().strip()
            if start:
                try:
                    dialog.set_initial_folder(
                        Gio.File.new_for_path(str(Path(start).expanduser().parent)))
                except Exception:
                    pass
            if folder:
                dialog.select_folder(window, None, done)
            else:
                dialog.open(window, None, done)

        btn.connect("clicked", pick)
        return btn

    def _set(self, node_id: str, key: str, value) -> None:
        if self._building:
            return
        self.doc.set_value(node_id, key, value)
        self._changed()

    def _disconnect(self, node_id: str, key: str) -> None:
        self.doc.disconnect(node_id, key)
        self._changed()
        self.set_node(node_id)
