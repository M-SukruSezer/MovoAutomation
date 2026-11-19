#!/usr/bin/env bash
# D-Bus sinyal tetikleyicisi testi.
#
# Gerçek bir bus üzerinde çalışır ama kullanıcının ayarlarına dokunmaz:
# tests/sahte-servis.py oturum bus'ında kendi servisini yayınlar, sinyali
# ve özellik değişimini biz üretiriz. (GNOME'un bildirim sunucusu
# CloseNotification'da NotificationClosed yaymadığı için hazır bir sinyal
# kaynağı yok; kendi servisimiz hem belirlenebilir hem de yan etkisiz.)
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-sinyal-XXXXXX")"
SVC_PID=""
cleanup() { [ -n "$SVC_PID" ] && kill "$SVC_PID" 2>/dev/null; rm -rf "$SANDBOX"; }
trap cleanup EXIT
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

export MOVO_FLOW_DIR="$SANDBOX"

echo "— statik denetim"
cat > "$SANDBOX/bozuk.yaml" <<'YAML'
name: Bozuk sinyal
slug: bozuk-sinyal
trigger:
  type: dbus
  bus: hangisi
  interface: dev.movo.Test
  member: Olay
  property: Deger
steps:
  - id: a
    action: oturum.kilitle
YAML
out="$($MOVO kontrol bozuk-sinyal 2>&1)"
check "bozuk tetikleyici reddediliyor" "! $MOVO kontrol bozuk-sinyal"
has "bilinmeyen bus yakalanıyor"       "$out" "'session' veya 'system' olmalı"
has "sinyal+özellik birlikte olmuyor"  "$out" "ikisi birden ya da hiçbiri olmaz"

cat > "$SANDBOX/bozuk2.yaml" <<'YAML'
name: Bozuk sinyal 2
slug: bozuk-sinyal2
trigger:
  type: dbus
  interface: dev.movo.Test
  property: Deger
  produces: sayi-falan
  args: {a: text}
steps:
  - id: a
    action: shell.run
    with: {command: "echo {{ trigger.yokboyle }}"}
YAML
out="$($MOVO kontrol bozuk-sinyal2 2>&1)"
has "özellik için nesne yolu zorunlu"  "$out" "özellik dinlemek için nesne yolu zorunlu"
has "bilinmeyen produces yakalanıyor"  "$out" "bilinmeyen tip 'sayi-falan'"
has "özellik kipinde args uyarılıyor"  "$out" "özellik modunda kullanılmaz"
has "olmayan tetikleyici çıktısı yakalanıyor" "$out" "'yokboyle' çıktısı yok"

check "argüman tipleri denetleniyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.flow import _build, TRIGGERS
from movo.registry import Registry
reg = Registry.discover()
f = _build({'name':'x','trigger':{'type':'dbus','interface':'a.b','member':'C',
            'args':{'kim':'metinmis'}},'steps':[{'id':'s','action':'oturum.kilitle'}]}, reg)
assert any('metinmis' in p.message for p in f.problems), f.problems
f2 = _build({'name':'x','trigger':{'type':'dbus','interface':'a.b','member':'C',
            'args':['kim']},'steps':[{'id':'s','action':'oturum.kilitle'}]}, reg)
assert any('eşleme olmalı' in p.message for p in f2.problems), f2.problems
\""
check "çıktılar yapılandırmadan doğuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.flow import dbus_trigger_outputs as d
assert d({'member':'Olay','args':{'kim':'text','kac':'number'}}) == {
    'kim':'text','kac':'number','member':'text','object':'text'}
assert d({'property':'Yuzde','produces':'number'}) == {
    'value':'number','property':'text','interface':'text','object':'text'}
\""

# ------------------------------------------------------- gerçek bus gerekir
if ! python3 -c "
import sys; sys.path.insert(0,'.')
from movo.dbus import names
sys.exit(0 if names('session') else 1)" 2>/dev/null; then
  echo "— oturum bus'ı yok, canlı testler atlanıyor"
  for t in "sinyal akışı tetikliyor" "argümanlar porta dönüşüyor" \
           "özellik değişimi tetikliyor" "aynı değer ikinci kez tetiklemiyor" \
           "settle_ms olayları birleştiriyor" "incele sinyalden tetikleyici üretiyor"; do
    atla "$t"
  done
else
  python3 tests/sahte-servis.py > "$SANDBOX/svc.log" 2>&1 &
  SVC_PID=$!
  sleep 1.5
  if ! grep -q hazır "$SANDBOX/svc.log"; then
    no "sahte servis başlamadı: $(cat "$SANDBOX/svc.log")"
  fi

  echo "— sinyal kipi (canlı)"
  cat > "$SANDBOX/sinyal.yaml" <<YAML
name: Sinyal akışı
slug: sinyal
trigger:
  type: dbus
  bus: session
  service: dev.movo.Test
  object: /dev/movo/Test
  interface: dev.movo.Test
  member: Olay
  args: {mesaj: text, sayi: number}
steps:
  - id: yaz
    action: shell.run
    with:
      command: "echo 'olay {{ trigger.mesaj }} {{ trigger.sayi }}' >> $SANDBOX/olay.log"
YAML
  check "sinyal akışı geçerli" "$MOVO kontrol sinyal"
  check "sinyal akışı onaylanıyor" "$MOVO onay sinyal --onayla"
  timeout 12 $MOVO izle sinyal --bir --quiet > "$SANDBOX/izle.log" 2>&1 &
  watcher=$!
  sleep 1.5
  python3 tests/sahte-servis.py tetikle merhaba 7 >/dev/null 2>&1
  wait $watcher 2>/dev/null
  check "sinyal akışı tetikliyor"      "[ -f $SANDBOX/olay.log ]"
  has "argümanlar porta dönüşüyor"     "$(cat "$SANDBOX/olay.log" 2>/dev/null)" "olay merhaba 7"

  echo "— özellik kipi (canlı)"
  cat > "$SANDBOX/ozellik.yaml" <<YAML
name: Özellik akışı
slug: ozellik
trigger:
  type: dbus
  bus: session
  service: dev.movo.Test
  object: /dev/movo/Test
  interface: dev.movo.Test
  property: Deger
  produces: number
steps:
  - id: dusuk
    action: shell.run
    when: {left: "{{ trigger.value }}", op: "<", right: 20}
    with: {command: "echo 'dusuk {{ trigger.value }}' >> $SANDBOX/deger.log"}
  - id: yuksek
    action: shell.run
    when: {left: "{{ trigger.value }}", op: ">=", right: 20}
    with: {command: "echo 'yuksek {{ trigger.value }}' >> $SANDBOX/deger.log"}
YAML
  $MOVO onay ozellik --onayla >/dev/null
  python3 tests/sahte-servis.py ayarla 0 >/dev/null 2>&1
  timeout 14 $MOVO izle ozellik --quiet > "$SANDBOX/izle2.log" 2>&1 &
  watcher=$!
  sleep 1.5
  for v in 50 50 12 12 77; do
    python3 tests/sahte-servis.py ayarla $v >/dev/null 2>&1
    sleep 0.4
  done
  sleep 0.8
  kill $watcher 2>/dev/null; wait $watcher 2>/dev/null
  log="$(cat "$SANDBOX/deger.log" 2>/dev/null)"
  has "özellik değişimi tetikliyor"       "$log" "yuksek 50"
  has "koşul değere göre dallanıyor"      "$log" "dusuk 12"
  check "aynı değer ikinci kez tetiklemiyor" \
    "[ \$(printf '%s\n' \"\$log\" | grep -c .) -eq 3 ]"

  echo "— settle_ms"
  sed -e 's/slug: ozellik/slug: yavas/' \
      -e 's|produces: number|produces: number\n  settle_ms: 700|' \
      -e "s|deger.log|yavas.log|g" "$SANDBOX/ozellik.yaml" > "$SANDBOX/yavas.yaml"
  $MOVO onay yavas --onayla >/dev/null
  python3 tests/sahte-servis.py ayarla 0 >/dev/null 2>&1
  timeout 14 $MOVO izle yavas --quiet > "$SANDBOX/izle3.log" 2>&1 &
  watcher=$!
  sleep 1.5
  for v in 31 32 33 34; do python3 tests/sahte-servis.py ayarla $v >/dev/null 2>&1; done
  sleep 2
  kill $watcher 2>/dev/null; wait $watcher 2>/dev/null
  log="$(cat "$SANDBOX/yavas.log" 2>/dev/null)"
  check "hızlı olaylar tek çalıştırmaya indi" \
    "[ \$(printf '%s\n' \"\$log\" | grep -c .) -eq 1 ]"
  has "son değer kazandı" "$log" "yuksek 34"

  echo "— keşif"
  has "incele sinyali argümanlarıyla basıyor" \
    "$($MOVO incele dev.movo.Test 2>&1)" "↯ Olay(s mesaj, u sayi)"
  has "incele sinyalden tetikleyici üretiyor" \
    "$($MOVO incele dev.movo.Test --uret Olay 2>&1)" "args: {mesaj: text, sayi: number}"
  has "incele özellikten tetikleyici üretiyor" \
    "$($MOVO incele dev.movo.Test --uret Deger --tetikleyici 2>&1)" "property: Deger"
  has "incele özellikten eylem de üretiyor" \
    "$($MOVO incele dev.movo.Test --uret Deger 2>&1)" "access: get"

  check "üretilen tetikleyici gerçekten yükleniyor" "python3 -c \"
import sys, subprocess, yaml; sys.path.insert(0,'.')
from movo.registry import Registry
from movo.flow import _build
blok = subprocess.run(['./bin/movo','incele','dev.movo.Test','--uret','Olay'],
                      capture_output=True, text=True).stdout
raw = yaml.safe_load('\n'.join(l for l in blok.splitlines() if not l.startswith('#')))
raw.update({'name':'x','steps':[{'id':'s','action':'oturum.kilitle'}]})
f = _build(raw, Registry.discover())
assert f.ok, [str(p) for p in f.problems]
assert f.trigger_outputs()['mesaj'] == 'text'
\""
  python3 tests/sahte-servis.py dur >/dev/null 2>&1
  SVC_PID=""
fi

echo "— birim ve editör"
has "dbus akışı uzun ömürlü servis üretiyor" \
  "$(env -u MOVO_FLOW_DIR $MOVO kur pil-dusuk --prova 2>&1)" "izle"
has "birim ne dinlediğini yazıyor" \
  "$(env -u MOVO_FLOW_DIR $MOVO kur pil-dusuk --prova 2>&1)" "# dinlenen: sistem"
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"
check "editör dinamik çıktıları gösteriyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/pil-dusuk.yaml'))
assert d.flow.ok, [str(p) for p in d.flow.problems]
trg = next(n for n in d.nodes() if n.id == 'trigger')
assert set(trg.outputs) == {'value','property','interface','object'}, list(trg.outputs)
assert trg.outputs['value'].type == 'number'
assert 'Percentage' in trg.subtitle, trg.subtitle
\""

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
[ "$skip" -gt 0 ] && printf '\033[33m%d atlandı\033[0m, ' "$skip"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
