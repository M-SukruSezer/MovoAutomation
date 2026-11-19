"""Hakkında penceresi.

Kurulumu sunulmasından ayrı tutuluyor: içeriği ekran olmadan da
denetlenebilsin diye ``build_about`` bir pencere döndürüyor, onu
göstermek çağıranın işi.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import (APP_ID, APP_NAME, COPYRIGHT, DEVELOPER, ISSUE_URL, PROJECT_URL,
                __version__)

RELEASE_NOTES = """
<p>İlk yayın.</p>
<ul>
  <li>Eylem manifest standardı ve 23 yerleşik eylem</li>
  <li>Tip sistemi: alt tipleme, liste tipleri, statik doğrulama</li>
  <li>Düğüm editörü; akış dosyası ile editör aynı şeyin iki yüzü</li>
  <li>Tetikleyiciler: klasör, zaman, ağ, D-Bus sinyali ve özelliği</li>
  <li>Koşul, dallanma, döngü, hata dalı ve paralel çalıştırma</li>
  <li>Üç katmanlı izin modeli: manifest, onay, Landlock yalıtımı</li>
  <li>Tek dosyalık akış paylaşımı</li>
</ul>
"""


def tani_metni(reg=None) -> str:
    """Hata bildirimine eklenecek ortam özeti."""
    from .. import dbus as dbuslib
    from .. import isolate as iso
    from ..registry import Registry

    reg = reg or Registry.discover()
    satir = [
        f"{APP_NAME} {__version__}",
        f"Python {platform.python_version()} ({sys.executable})",
        f"Paket {Path(__file__).resolve().parents[1]}",
        f"Sistem {platform.platform()}",
        "",
    ]
    for mod, etiket in (("yaml", "PyYAML"), ("PIL", "Pillow"),
                        ("gi", "PyGObject")):
        try:
            m = __import__(mod)
            satir.append(f"{etiket}: {getattr(m, '__version__', 'var')}")
        except Exception:
            satir.append(f"{etiket}: yok")
    try:
        satir.append(f"GTK: {Gtk.get_major_version()}.{Gtk.get_minor_version()}"
                     f" · libadwaita: {Adw.get_major_version()}."
                     f"{Adw.get_minor_version()}")
    except Exception:
        pass
    satir.append("")
    n_dbus = sum(1 for a in reg.actions.values() if a.impl_kind == "dbus")
    satir.append(f"Eylemler: {len(reg.actions)} ({n_dbus} D-Bus), "
                 f"{len(reg.problems)} manifest sorunu")
    for bus in ("session", "system"):
        adlar = dbuslib.names(bus)
        satir.append(f"{dbuslib.BUS_LABEL[bus]} bus: "
                     + (f"{len(adlar)} servis" if adlar else "ulaşılamadı"))
    satir.append(f"Yalıtım: {iso.available()[1]}")
    return "\n".join(satir) + "\n"


def build_about(reg=None) -> Adw.AboutDialog:
    about = Adw.AboutDialog(
        application_name=APP_NAME,
        application_icon=APP_ID,
        version=__version__,
        developer_name=DEVELOPER,
        comments=(
            "Linux için görsel otomasyon motoru.\n\n"
            "Dosya işlemlerini ve sistem olaylarını kod yazmadan, "
            "birbirine takılan bloklarla otomatikleştirir. Blokların "
            "uyumunu bir tip sistemi güvence altına alır, akışlar "
            "systemd birimlerine derlenir ve masaüstü uygulamalarıyla "
            "D-Bus üzerinden konuşulur."
        ),
        website=PROJECT_URL,
        issue_url=ISSUE_URL,
        license_type=Gtk.License.MIT_X11,
        copyright=COPYRIGHT,
        release_notes=RELEASE_NOTES,
        release_notes_version=__version__,
        debug_info=tani_metni(reg),
        debug_info_filename="movo-tani.txt",
        developers=[DEVELOPER],
        designers=[DEVELOPER],
    )
    about.add_link("Kaynak kodu", PROJECT_URL)
    about.add_link("Sürümler", f"{PROJECT_URL}/releases")
    about.add_acknowledgement_section(
        "Üzerine kuruldu",
        [
            "GTK — https://www.gtk.org",
            "libadwaita — https://gitlab.gnome.org/GNOME/libadwaita",
            "PyGObject — https://pygobject.gnome.org",
            "Landlock — https://landlock.io",
        ],
    )
    about.add_acknowledgement_section(
        "Çalışma ilkeleri",
        [
            "Akış çalışmadan önce statik olarak doğrulanır",
            "Her eylem ne yapacağını manifestinde beyan eder",
            "Arka planda çalışacak akış önce onaylanır",
            "Ara dosyalar geçici klasörde durur, son adımda taşınır",
        ],
    )
    about.add_legal_section(
        "Simge",
        "Uygulama simgesi projenin parçasıdır ve aynı lisansla "
        "yayımlanır. GNOME simge kalıbını izler ve GNOME paletini kullanır.",
        Gtk.License.MIT_X11,
        None,
    )
    return about
