#!/usr/bin/env bash
# Editör duman testi: gerçek GTK penceresi açar, çizimi PNG'ye basar ve
# model işlemlerini (ekle/bağla/reddet/kaydet) başsız doğrular.
set -uo pipefail
cd "$(dirname "$0")/.."
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-gui-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

pass=0; fail=0
ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }

echo "— belge modeli (başsız)"
check "model akışı açıyor ve doğruluyor" "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/resim-webp.yaml'))
assert d.flow.ok and len(d.nodes()) == 5 and len(d.wires()) == 6
PY"
check "uyumsuz tip bağlantısı reddediliyor" "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/resim-webp.yaml'))
ok, why = d.can_connect('trigger','filename','kucult','image')
assert not ok and 'metin' in why, why
assert d.can_connect('trigger','file','kucult','image')[0]
PY"
check "döngü reddediliyor" "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/resim-webp.yaml'))
sid = d.add_step('file.copy', 1200, 60)
assert d.connect('webp','image',sid,'file') is None
assert d.connect(sid,'file','tasi','file') is None
ok, why = d.can_connect('tasi','file',sid,'file')
assert not ok and 'döngü' in why, why
PY"
check "kablo çekince adımlar topolojik sıralanıyor" "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/resim-webp.yaml'))
sid = d.add_step('file.rename', 1200, 400)
d.set_value(sid, 'pattern', '{name}-son.{ext}')
assert d.connect('webp','image',sid,'file') is None
ids = [s['id'] for s in d.steps()]
assert ids.index('webp') < ids.index(sid), ids
assert d.flow.ok, [str(p) for p in d.flow.problems]
PY"
check "YAML yuvarlak gidiş-gelişi koruyor" "python3 - <<'PY'
import sys, yaml; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
reg = Registry.discover()
d = Doc.open(reg, Path('movo/ornekler/resim-webp.yaml'))
out = Path('$SANDBOX/kayit.yaml'); d.save(out)
again = Doc.open(reg, out)
assert again.flow.ok
assert [s['id'] for s in again.steps()] == [s['id'] for s in d.steps()]
assert again.pos('kucult') == d.pos('kucult')          # yerleşim korundu
raw = yaml.safe_load(out.read_text())
assert 'layout' in raw and raw['steps'][0]['action'] == 'image.resize'
PY"
check "elle yazılmış, yerleşimsiz akış otomatik yerleşiyor" "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/gece-rapor.yaml'))
assert 'layout' not in d.raw
ns = d.nodes()
assert all(n.x > 0 for n in ns) and len({n.x for n in ns}) > 1
PY"

echo "— Hakkında"
check "hakkında penceresi kuruluyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk
from movo import APP_NAME, APP_ID, __version__
from movo.gui.about import build_about, tani_metni
d = build_about()
assert d.get_application_name() == APP_NAME
assert d.get_application_icon() == APP_ID
assert d.get_version() == __version__
assert d.get_license_type() == Gtk.License.MIT_X11
assert d.get_website().startswith('https://github.com/')
assert d.get_debug_info()
\""
check "tanı metni ortamı özetliyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.gui.about import tani_metni
m = tani_metni()
for beklenen in ('Movo Automation', 'PyYAML', 'Eylemler:', 'Yalıtım:'):
    assert beklenen in m, (beklenen, m)
\""
check "menüde hakkında girdisi var" "grep -q 'win.about' movo/gui/window.py"

echo "— simge ve masaüstü girdisi"
check "simge GNOME kalıbına uyuyor" "python3 -c \"
import xml.etree.ElementTree as ET, pathlib
for ad in ('io.github.movo.MovoAutomation.svg',
           'io.github.movo.MovoAutomation-symbolic.svg'):
    y = pathlib.Path('movo/veri/icons/hicolor')
    y = (y / 'symbolic/apps' / ad) if 'symbolic' in ad else (y / 'scalable/apps' / ad)
    ET.parse(y)                                  # geçerli SVG
    ham = y.read_bytes()
    assert ham.index(b'<svg') < 120, (ad, ham.index(b'<svg'))   # gdk-pixbuf
kok = ET.parse('movo/veri/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg').getroot()
assert kok.get('viewBox') == '0 0 128 128', kok.get('viewBox')
govde = pathlib.Path('movo/veri/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg').read_text()
for renk in ('#1a5fb4', '#3584e4', '#99c1f1'):   # GNOME paleti
    assert renk in govde, renk
\""
check "masaüstü girdisi geçerli" "python3 -c \"
import configparser
c = configparser.ConfigParser(interpolation=None)
c.read('movo/veri/io.github.movo.MovoAutomation.desktop')
e = c['Desktop Entry']
assert e['Name'] == 'Movo Automation'
assert e['Icon'] == 'io.github.movo.MovoAutomation'
assert e['Exec'].startswith('movo ')
\""

echo "— çizim (GTK)"
check "tuval PNG üretiyor"      "./tools/tuval-png.py movo/ornekler/resim-webp.yaml $SANDBOX/tuval.png 1200 640 && [ -s $SANDBOX/tuval.png ]"
check "bozuk akış da çiziliyor" "./tools/tuval-png.py tests/bozuk.yaml $SANDBOX/bozuk.png 1200 640 && [ -s $SANDBOX/bozuk.png ]"
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
  check "editör penceresi açılıp çiziliyor" "timeout 60 ./tools/pencere-png.py movo/ornekler/resim-webp.yaml $SANDBOX/pencere.png 1200 && [ -s $SANDBOX/pencere.png ]"
else
  printf '  \033[33m–\033[0m pencere testi atlandı (ekran yok)\n'
fi

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
