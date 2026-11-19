#!/usr/bin/env bash
# Onay katmanı testi.
#
# Onayı anlamlı kılan iki şey var ve ikisi de burada sınanıyor:
#   1. Parmak izi doğru yere bakıyor mu? Kozmetik düzenleme onayı
#      bozmamalı, yetki genişlemesi bozmalı.
#   2. Gözetimsiz çalışma gerçekten durduruluyor mu?
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-onay-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
export XDG_STATE_HOME="$SANDBOX/durum"
export MOVO_FLOW_DIR="$SANDBOX"
pass=0; fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }
has()  { if printf '%s' "$2" | grep -qF "$3"; then ok "$1"; else
           no "$1 — gelen: $(printf '%s' "$2" | tail -3)"; fi; }
yok()  { if printf '%s' "$2" | grep -qF "$3"; then
           no "$1 — '$3' çıkmamalıydı"; else ok "$1"; fi; }

mkdir -p "$SANDBOX/gelen" "$SANDBOX/cikan"
yaz() {  # $1 slug, $2 ek adımlar/alanlar
  cat > "$SANDBOX/$1.yaml" <<YAML
name: Onay denemesi $1
slug: $1
trigger:
  type: folder
  path: $SANDBOX/gelen
  settle_ms: 0
steps:
  - id: tasi
    action: file.move
    with:
      file: "{{ trigger.file }}"
      to_dir: $SANDBOX/cikan
      pattern: "{name}.{ext}"
$2
YAML
}
yaz temiz ""

echo "— istem"
out="$($MOVO onay temiz --onayla 2>&1)"
has "istem tetikleyiciyi gösteriyor" "$out" "tetikleyici: Klasör izle"
has "istem izinleri gösteriyor"      "$out" "dosya oluşturur veya değiştirir"
has "istem yolları gösteriyor"       "$out" "dokunduğu yerler:"
has "istem eylemleri gösteriyor"     "$out" "eylemler: file.move"
has "onay kaydediliyor"              "$out" "onaylandı"
check "kayıt dosyası oluştu" "[ -s $XDG_STATE_HOME/movo/onaylar.json ]"
check "kayıt geçerli JSON" "python3 -c \"
import json, os
d = json.load(open(os.environ['XDG_STATE_HOME'] + '/movo/onaylar.json'))
assert d['sürüm'] == 1 and len(d['onaylar']) == 1
g = d['onaylar'][0]
assert g['slug'] == 'temiz' and len(g['parmak_izi']) == 16
assert 'fs.write' in g['istem']['izinler']
\""
has "onaylar listesi geçerli diyor" "$($MOVO onaylar 2>&1)" "geçerli"

echo "— parmak izi doğru yere bakıyor"
check "kozmetik düzenleme onayı bozmuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load, _build
from movo import consent as O
import yaml
reg = Registry.discover()
raw = yaml.safe_load(Path('$SANDBOX/temiz.yaml').read_text())
onceki = O.ask_for(_build(raw, reg), reg).fingerprint()
raw['name'] = 'Bambaşka bir ad'                 # ad
raw['description'] = 'açıklama eklendi'
raw['steps'][0]['id'] = 'yeniad'                # adım kimliği
raw['steps'][0]['with']['pattern'] = '{name}-x.{ext}'   # desen
raw['steps'][0]['with']['file'] = '{{ trigger.file }}'
sonraki = O.ask_for(_build(raw, reg), reg).fingerprint()
assert onceki == sonraki, (onceki, sonraki)
\""
check "yeni yol onayı bozuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import _build
from movo import consent as O
import yaml
reg = Registry.discover()
raw = yaml.safe_load(Path('$SANDBOX/temiz.yaml').read_text())
onceki = O.ask_for(_build(raw, reg), reg).fingerprint()
raw['steps'][0]['with']['to_dir'] = '/tmp/bambaska-yer'
assert O.ask_for(_build(raw, reg), reg).fingerprint() != onceki
\""

# yetki genişlemesi: shell.run ekle
yaz temiz "  - id: ek
    action: shell.run
    with: {command: \"echo x\"}"
out="$($MOVO onaylar 2>&1)"
has "yetki genişleyince onay bayatlıyor" "$out" "bayat"
has "neyin değiştiği yazılıyor"          "$out" "+ yeni eylem: shell.run"
has "yeni izin de yazılıyor"             "$out" "+ yeni izin: harici komut"

echo "— gözetimsiz çalışma kapısı"
out="$($MOVO izle temiz --bir --quiet 2>&1)"
check "bayat onayla izleme reddediliyor" "! $MOVO izle temiz --bir --quiet"
has "red gerekçesi bayatlık"             "$out" "onay bayat"
has "red ne yapılacağını söylüyor"       "$out" "movo onay temiz"
# Kapı açılıyor mu? İzleyici dosya bekler, o yüzden kısa süre tanıyoruz:
# 'izinsiz' uyarısını basmışsa kapıdan geçmiş demektir.
timeout 4 $MOVO izle temiz --bir --izinsiz > "$SANDBOX/izinsiz.log" 2>&1 || true
has "--izinsiz kapıyı açıyor" "$(cat "$SANDBOX/izinsiz.log")" "onaysız çalışıyor"

yaz yeni ""
out="$($MOVO izle yeni --bir --quiet 2>&1)"
check "hiç onaylanmamış akış da reddediliyor" "! $MOVO izle yeni --bir --quiet"
has "red gerekçesi onaysızlık"                "$out" "onay yok"

echo "— yenileme ve kaldırma"
check "yeniden onaylayınca kapı açılıyor" "$MOVO onay temiz --onayla"
has "onaylar listesi yine geçerli diyor"  "$($MOVO onaylar 2>&1)" "geçerli"
check "kaldırma çalışıyor"                "$MOVO onay temiz --kaldir"
check "kaldırdıktan sonra reddediliyor"   "! $MOVO izle temiz --bir --quiet"
check "olmayan onayı kaldırmak hata"      "! $MOVO onay temiz --kaldir"

echo "— önplan engellenmiyor, uyarılıyor"
cp "$SANDBOX/gelen/."* "$SANDBOX/" 2>/dev/null || true
printf 'merhaba\n' > "$SANDBOX/gelen/a.txt"
out="$($MOVO calistir temiz --dosya $SANDBOX/gelen/a.txt 2>&1)"
check "onaysız önplan çalıştırma geçiyor" \
  "$MOVO calistir temiz --dosya $SANDBOX/gelen/a.txt >/dev/null 2>&1 || true; [ -d $SANDBOX/cikan ]"
has "ama uyarı basılıyor" "$out" "onaysız: harici komut"

yaz sessiz ""
out="$($MOVO calistir sessiz --dosya $SANDBOX/gelen/a.txt 2>&1)"
yok "hassas olmayan akış uyarı almıyor" "$out" "onaysız"

echo "— kurulum onayı kayda geçiriyor"
yaz kurulacak ""
check "kur onay kaydı bırakıyor" "python3 -c \"
import os, subprocess, json, sys
env = dict(os.environ)
r = subprocess.run(['./bin/movo','kur','kurulacak','--onayla',
                    '--etkinlestirme-yok'], env=env, capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
d = json.load(open(env['XDG_STATE_HOME'] + '/movo/onaylar.json'))
assert any(g['slug'] == 'kurulacak' for g in d['onaylar']), d
\""
check "kurulumun onayı listede görünüyor" "$MOVO onaylar | grep -q kurulacak"
$MOVO kaldir kurulacak >/dev/null 2>&1 || true

echo "— tanılama ve editör"
has "surum onay sayısını bildiriyor" "$($MOVO surum 2>&1)" "kayıt"
check "editör onay durumunu hesaplıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
from movo import consent as O
reg = Registry.discover()
d = Doc.open(reg, Path('$SANDBOX/sessiz.yaml'))
ask = O.ask_for(d.flow, reg)
assert O.check(ask)[0] == 'yok'
O.grant(ask)
assert O.check(O.ask_for(d.flow, reg))[0] == 'var'
d.set_value('tasi', 'to_dir', '/tmp/baska')     # düzenleme onayı bozar
assert O.check(O.ask_for(d.validate(), reg))[0] == 'bayat'
\""
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
