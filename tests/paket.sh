#!/usr/bin/env bash
# Paketleme testi: tekerlek gerçekten kurulabiliyor ve kurulu hâli
# depodan bağımsız çalışıyor mu?
#
# Burada "çalışıyor" demek, depo kökü ortadan kalktığında da eylem
# manifestlerini ve örnek akışları bulabilmek demek -- ikisi de paketin
# içinde taşınıyor.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-paket-XXXXXX")"
# pip tekerleği ağaç içinde derliyor; test depoyu kirletmemeli
temizle() { rm -rf "$SANDBOX" "$REPO/build" "$REPO"/*.egg-info; }
trap temizle EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

pass=0; fail=0; skip=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
atla() { printf '  \033[33m–\033[0m %s \033[2m(atlandı)\033[0m\n' "$1"; skip=$((skip+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }
has()  { if printf '%s' "$2" | grep -qF "$3"; then ok "$1"; else
           no "$1 — gelen: $(printf '%s' "$2" | tail -3)"; fi; }

echo "— üstveri"
check "pyproject okunabiliyor" "python3 -c \"
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
pj = d['project']
assert pj['name'] == 'movo'
assert pj['dependencies'] == ['PyYAML>=5.1'], pj['dependencies']
assert set(pj['optional-dependencies']) == {'resim','masaustu','hepsi'}
assert pj['scripts']['movo'] == 'movo.cli:main'
assert 'version' in pj['dynamic']
pd = d['tool']['setuptools']['package-data']['movo']
assert 'actions/*.action.yaml' in pd and 'ornekler/*.yaml' in pd and 'veri/*' in pd
\""
check "sürüm tek kaynaktan geliyor" "python3 -c \"
import sys, subprocess; sys.path.insert(0,'.')
import movo
out = subprocess.run(['./bin/movo','--surum'], capture_output=True, text=True).stdout
assert out.strip() == 'movo ' + movo.__version__, (out, movo.__version__)
\""
check "python -m movo çalışıyor" "python3 -m movo --surum | grep -q '^movo '"
check "LICENSE ve README yerinde" "[ -s LICENSE ] && [ -s README.md ]"

echo "— paketin içinde taşınanlar"
check "eylem manifestleri pakette" \
  "[ \$(ls movo/actions/*.action.yaml | wc -l) -ge 23 ]"
check "örnek akışlar pakette"      "[ \$(ls movo/ornekler/*.yaml | wc -l) -ge 7 ]"
check "masaüstü dosyaları pakette" \
  "[ -s movo/veri/io.github.movo.MovoAutomation.desktop ] \
   && [ -s movo/veri/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg ] \
   && [ -s movo/veri/icons/hicolor/symbolic/apps/io.github.movo.MovoAutomation-symbolic.svg ]"
check "depo kökünde ayrı akış kopyası yok" "[ ! -d flows ]"
check "örnekler arama yolunun sonunda" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.cli import flow_dirs, bundled_flows
assert flow_dirs()[-1] == bundled_flows()
assert bundled_flows().name == 'ornekler'
\""

echo "— tembel bağımlılıklar"
check "çekirdek yalnızca PyYAML yüklüyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
import movo.cli
assert 'yaml' in sys.modules
assert 'gi' not in sys.modules, 'gi erken yüklendi'
assert 'PIL' not in sys.modules, 'PIL erken yüklendi'
\""
check "manifestler gi olmadan yükleniyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
import builtins
gercek = builtins.__import__
def engelle(ad, *a, **k):
    if ad.split('.')[0] in ('gi','PIL'):
        raise ImportError('test: ' + ad + ' yok sayıldı')
    return gercek(ad, *a, **k)
builtins.__import__ = engelle
from movo.registry import Registry
r = Registry.discover()
assert len(r.actions) >= 23 and not r.problems, r.problems
from movo.flow import load
from pathlib import Path
f = load(Path('movo/ornekler/toplu-webp.yaml'), r)
assert f.ok, [str(p) for p in f.problems]
\""

echo "— masaüstü bütünleşmesi"
export XDG_DATA_HOME="$SANDBOX/veri"
out="$(./bin/movo masaustu 2>&1)"
check "desktop dosyası yazıldı" \
  "[ -s $XDG_DATA_HOME/applications/io.github.movo.MovoAutomation.desktop ]"
check "simge yazıldı" \
  "[ -s $XDG_DATA_HOME/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg ]"
has "Exec gerçek ikiliyi gösteriyor" \
  "$(cat "$XDG_DATA_HOME/applications/io.github.movo.MovoAutomation.desktop")" "/bin/movo duzenle %f"
check "desktop dosyası geçerli" "python3 -c \"
import configparser, os
c = configparser.ConfigParser(interpolation=None)
c.read(os.environ['XDG_DATA_HOME'] + '/applications/io.github.movo.MovoAutomation.desktop')
e = c['Desktop Entry']
assert e['Type'] == 'Application' and e['Icon'] == 'io.github.movo.MovoAutomation'
assert e['Categories'].endswith(';') and not e.get('Terminal','').strip() == 'true'
\""
check "simge geçerli SVG" "python3 -c \"
import os, xml.etree.ElementTree as ET
ET.parse(os.environ['XDG_DATA_HOME'] + '/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg')
\""
./bin/movo masaustu --kaldir >/dev/null 2>&1
check "kaldır dosyaları siliyor" \
  "[ ! -e $XDG_DATA_HOME/applications/io.github.movo.MovoAutomation.desktop ]"
unset XDG_DATA_HOME

echo "— tanılama"
out="$(./bin/movo surum 2>&1)"
has "sürüm basılıyor"            "$out" "movo 0.1.0"
has "bağımlılıklar raporlanıyor" "$out" "PyYAML"
has "eylem sayısı raporlanıyor"  "$out" "manifest sorunu"
has "arama yolları raporlanıyor" "$out" "movo/ornekler"

echo "— tekerlek"
if ! python3 -m pip --version >/dev/null 2>&1; then
  for t in "tekerlek kuruluyor" "kurulu konsol betiği çalışıyor" \
           "kurulu sürüm eylemleri buluyor" "kurulu sürüm örnekleri buluyor" \
           "kurulu sürüm akış çalıştırıyor" "birim kurulu ikiliyi gösteriyor"; do atla "$t"; done
else
  rm -rf "$SANDBOX/dist"
  if python3 -m pip wheel . --no-deps --no-build-isolation \
       -w "$SANDBOX/dist" >"$SANDBOX/build.log" 2>&1; then
    ok "tekerlek kuruluyor"
  else
    no "tekerlek kurulamadı: $(tail -3 "$SANDBOX/build.log")"
  fi
  whl="$(ls "$SANDBOX"/dist/movo-*.whl 2>/dev/null | head -1)"
  check "tekerlek manifestleri içeriyor" "python3 -c \"
import zipfile
n = zipfile.ZipFile('$whl').namelist()
assert sum(1 for x in n if x.endswith('.action.yaml')) >= 23
assert sum(1 for x in n if x.startswith('movo/ornekler/')) >= 7
assert 'movo/veri/io.github.movo.MovoAutomation.desktop' in n
assert 'movo/veri/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg' in n
assert 'movo/veri/icons/hicolor/symbolic/apps/io.github.movo.MovoAutomation-symbolic.svg' in n
\""
  PX="$SANDBOX/prefix"
  python3 -m pip install --no-deps --no-index --prefix="$PX" "$whl" \
    >"$SANDBOX/install.log" 2>&1
  BIN="$(find "$PX" -name movo -type f -path '*/bin/*' | head -1)"
  DP="$(find "$PX" -name 'movo' -type d -path '*packages*' | head -1)"
  DP="$(dirname "$DP")"
  if [ -x "$BIN" ]; then ok "kurulu konsol betiği çalışıyor"; else
    no "konsol betiği bulunamadı: $(tail -3 "$SANDBOX/install.log")"; fi

  # Depodan uzakta çalıştır: manifestleri ve örnekleri paketten bulmalı
  run() { (cd /tmp && PYTHONPATH="$DP" PATH="$(dirname "$BIN"):$PATH" "$BIN" "$@"); }
  has "kurulu sürüm eylemleri buluyor"  "$(run eylemler 2>&1)" "eylem, "
  has "kurulu sürüm örnekleri buluyor"  "$(run akislar 2>&1)"  "· örnek"
  check "kurulu sürüm akışı doğruluyor" "run kontrol >/dev/null 2>&1"
  has "birim kurulu ikiliyi gösteriyor" \
    "$(run kur yedek-kontrol --prova 2>&1)" "$(dirname "$BIN")/movo calistir"
  has "kurulu sürüm paket yolunu bildiriyor" "$(run surum 2>&1)" "packages/movo"
fi

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
[ "$skip" -gt 0 ] && printf '\033[33m%d atlandı\033[0m, ' "$skip"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
