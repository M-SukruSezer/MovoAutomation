#!/usr/bin/env bash
# Hata dalı testi.
#
# Çekirdek iddia: hatayı yakalamak yeni bir denetim akışı kavramı
# gerektirmiyor. Patlayan adım iki port kazanıyor (tamam, hata); veri
# portlarını tüketen adım atlanıyor, hata portlarını okuyan adım
# çalışıyor. Dal buradan doğuyor.
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-hata-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

pass=0; fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }
has()  { if printf '%s' "$2" | grep -qF "$3"; then ok "$1"; else
           no "$1 — gelen: $(printf '%s' "$2" | tail -3)"; fi; }
yok()  { if printf '%s' "$2" | grep -qF "$3"; then
           no "$1 — '$3' çıkmamalıydı"; else ok "$1"; fi; }

export MOVO_FLOW_DIR="$SANDBOX"
mkdir -p "$SANDBOX/eylem"

echo "— statik denetim"
cat > "$SANDBOX/bozuk.yaml" <<'YAML'
name: Bozuk hata dalı
slug: bozuk-hata
trigger: {type: manual}
steps:
  - id: sessiz
    action: file.exists
    on_error: devam
    with: {path: /tmp}
  - id: atlamali
    action: file.exists
    on_error: atla
    with: {path: /tmp}
  - id: yanlis
    action: file.exists
    on_error: belki
    with: {path: /tmp}
  - id: erken
    action: ui.notify
    with: {title: "{{ atlamali.hata }}"}
YAML
out="$($MOVO kontrol bozuk-hata 2>&1)"
check "bozuk akış reddediliyor"       "! $MOVO kontrol bozuk-hata"
has "döngüsüz 'atla' reddediliyor"    "$out" "döngüsüz adımda karşılığı yok"
has "bilinmeyen ilke yakalanıyor"     "$out" "'belki' geçerli değil"
has "yakalamayan adımın hata portu yok" "$out" "'hata' çıktısı yok"
has "yakalanıp bakılmayan hata uyarılıyor" "$out" "kimse bakmıyor"

# tamam/hata adını zaten kullanan bir eylem hata yakalayamaz
cat > "$SANDBOX/eylem/cmovoan.action.yaml" <<'YAML'
id: test.cmovoan
name: Çakışan
permissions: [fs.read]
inputs:
  path: {type: path, required: true}
outputs:
  tamam: {type: bool, label: Tamam}
  size: {type: number, label: Boyut}
  exists: {type: bool}
impl: {kind: python, entry: movo.actions.builtin:file_exists}
YAML
cat > "$SANDBOX/cmovoma.yaml" <<'YAML'
name: Çakışma
slug: cmovoma
trigger: {type: manual}
steps:
  - id: a
    action: test.cmovoan
    on_error: devam
    with: {path: /tmp}
  - id: b
    action: shell.run
    when: {left: "{{ a.tamam }}", op: yanlış}
    with: {command: "true"}
YAML
has "port adı çakışan eylem yakalayamaz" \
  "$(MOVO_ACTION_PATH=$SANDBOX/eylem $MOVO kontrol cmovoma 2>&1)" \
  "bu eylem hata yakalayamaz"

echo "— yakala ve devam et"
iki() {  # $1: birinci komut, $2: ikinci komut
  cat > "$SANDBOX/iki.yaml" <<YAML
name: İki aşama
slug: iki
trigger: {type: manual}
steps:
  - id: arsiv
    action: shell.run
    on_error: devam
    with: {command: "$1", check: true}
  - id: gonder
    action: shell.run
    on_error: devam
    with: {command: "echo gonderildi {{ arsiv.stdout }}; $2", check: true}
  - id: arsiv-hata
    action: shell.run
    when: {left: "{{ arsiv.tamam }}", op: yanlış}
    with: {command: "echo ARSIV-HATASI"}
  - id: gonder-hata
    action: shell.run
    when: {left: "{{ gonder.tamam }}", op: yanlış}
    with: {command: "echo GONDERME-HATASI"}
  - id: bitti
    action: shell.run
    when: {left: "{{ gonder.tamam }}", op: doğru}
    with: {command: "echo BITTI {{ arsiv.stdout }}"}
YAML
  $MOVO calistir iki 2>&1
}

out="$(iki "false" "true")"
check "yakalanan hata akışı düşürmüyor" "iki 'false' 'true' >/dev/null"
has "hata yakalandı diye raporlanıyor"  "$out" "⚡ hata yakalandı"
has "veriyi tüketen adım atlanıyor"     "$out" "atlandı: arsiv hata verdiği için"
has "hata portunu okuyan adım çalışıyor" "$out" "ARSIV-HATASI"
has "özet yakalananı sayıyor"           "$out" "1 hata yakalandı"
yok "ikinci aşamanın dalı konuşmuyor"   "$out" "GONDERME-HATASI"
yok "başarı dalı konuşmuyor"            "$out" "BITTI"

out="$(iki "echo arsiv.tar" "false")"
has "ikinci aşama patlayınca onun dalı konuşuyor" "$out" "GONDERME-HATASI"
yok "ilk aşamanın dalı konuşmuyor"                "$out" "ARSIV-HATASI"
yok "başarı dalı yine konuşmuyor"                 "$out" "BITTI"

out="$(iki "echo arsiv.tar" "true")"
has "hepsi yolundaysa başarı dalı konuşuyor" "$out" "BITTI arsiv.tar"
yok "hata dalları susuyor"                   "$out" "HATASI"
yok "yakalanan hata yokken sayılmıyor"       "$out" "hata yakalandı"

echo "— 'dur' değişmedi"
cat > "$SANDBOX/dur.yaml" <<'YAML'
name: Durduran
slug: dur
trigger: {type: manual}
steps:
  - id: patla
    action: shell.run
    with: {command: "false", check: true}
  - id: sonra
    action: shell.run
    with: {command: "echo SONRAKI"}
YAML
out="$($MOVO calistir dur 2>&1)"
check "yakalanmayan hata akışı düşürüyor" "! $MOVO calistir dur"
yok "sonraki adım çalışmıyor"             "$out" "SONRAKI"

echo "— izin ihlali yakalanmaz"
cat > "$SANDBOX/eylem/izinsiz.action.yaml" <<'YAML'
id: test.izinsiz
name: İzinsiz
summary: Gövdesi fs.read istiyor ama manifestinde beyan etmiyor.
permissions: []
inputs:
  path: {type: path, required: true}
outputs:
  exists: {type: bool}
  size: {type: number}
  path: {type: path}
impl: {kind: python, entry: movo.actions.builtin:file_exists}
YAML
cat > "$SANDBOX/izin.yaml" <<'YAML'
name: İzin denemesi
slug: izin
trigger: {type: manual}
steps:
  - id: kacak
    action: test.izinsiz
    on_error: devam
    with: {path: /tmp}
  - id: dal
    action: shell.run
    when: {left: "{{ kacak.tamam }}", op: yanlış}
    with: {command: "echo DAL"}
YAML
out="$(MOVO_ACTION_PATH=$SANDBOX/eylem $MOVO calistir izin 2>&1)"
check "sözleşme ihlali 'devam' ile bile durduruyor" \
  "! MOVO_ACTION_PATH=$SANDBOX/eylem $MOVO calistir izin"
has "izin hatası olarak raporlanıyor" "$out" "izin reddedildi"
yok "hata dalı çalışmıyor"            "$out" "DAL"

echo "— döngüyle birlikte"
cat > "$SANDBOX/dongu.yaml" <<YAML
name: Döngü ve hata
slug: dongu-hata
trigger: {type: manual}
steps:
  - id: liste
    action: file.glob
    on_error: devam
    with: {dir: $SANDBOX/yok-boyle-klasor}
  - id: isle
    action: shell.run
    for_each: "{{ liste.files }}"
    with: {command: "echo {{ item.value }}"}
  - id: dal
    action: shell.run
    when: {left: "{{ liste.tamam }}", op: yanlış}
    with: {command: "echo LISTE-HATASI {{ liste.hata }}"}
YAML
out="$($MOVO calistir dongu-hata 2>&1)"
has "döngü kaynağı patlayınca yakalanıyor" "$out" "⚡ hata yakalandı"
has "döngü adımı atlanıyor"                "$out" "atlandı: liste hata verdiği için"
has "hata dalı çalışıyor"                  "$out" "LISTE-HATASI"

echo "— prova"
iki "false" "true" >/dev/null
out="$($MOVO calistir iki --prova 2>&1)"
has "prova bütün adımları planlıyor" "$out" "5/5 adım"
yok "provada hata yakalanmıyor"      "$out" "hata yakalandı"
check "yakalayan adım provada tamam/hata veriyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
f = load(Path('$SANDBOX/iki.yaml'), reg)
r = run(f, reg, dry_run=True, quiet=True)
outs = {s.id: s.outputs for s in r.steps}
assert outs['arsiv']['tamam'] is True and outs['arsiv']['hata'] == ''
assert 'tamam' not in outs['bitti']
\""

echo "— editör"
check "yakalayan düğüm iki port kazanıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-gonder.yaml'))
assert d.flow.ok, [str(p) for p in d.flow.problems]
n = {x.id: x for x in d.nodes()}
assert n['arsiv'].outputs['tamam'].type == 'bool'
assert n['arsiv'].outputs['hata'].type == 'text'
assert 'tamam' not in n['bitti'].outputs          # yakalamayan adımda yok
assert ('arsiv','tamam','arsiv-hata','when') in d.wires()
assert d.catches('arsiv') and not d.catches('bitti')
\""
check "ilke değişince portlar da değişiyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-gonder.yaml'))
d.set_on_error('bitti', 'devam')
assert 'tamam' in {x.id: x for x in d.nodes()}['bitti'].outputs
d.set_on_error('bitti', 'dur')
assert d.step('bitti').get('on_error') is None     # varsayılan yazılmıyor
assert 'tamam' not in {x.id: x for x in d.nodes()}['bitti'].outputs
\""
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
