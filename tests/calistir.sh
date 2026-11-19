#!/usr/bin/env bash
# Uçtan uca duman testi: gerçek dosyalarla, geçici bir kum havuzunda.
# Kullanım: tests/calistir.sh
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-test-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

pass=0; fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }

mkdir -p "$SANDBOX/gelen" "$SANDBOX/web"
python3 - "$SANDBOX" <<'PY'
import sys
from pathlib import Path
from PIL import Image, ImageDraw
s = Path(sys.argv[1])
im = Image.new("RGB", (3000, 2000), (40, 90, 160))
ImageDraw.Draw(im).ellipse((400, 300, 2600, 1700), fill=(240, 200, 60))
im.save(s / "buyuk.png")
Image.new("RGB", (400, 300), (200, 60, 60)).save(s / "kucuk.jpg")
(s / "resim-degil.png").write_bytes(b"bu bir resim degil\n" * 10)
PY

cat > "$SANDBOX/test-movoi.yaml" <<YAML
name: Test movoi
slug: test-movoi
trigger:
  type: folder
  path: $SANDBOX/gelen
  match: "*.{png,jpg,jpeg}"
  produces: image
  settle_ms: 0
steps:
  - id: kucult
    action: image.resize
    with: {image: "{{ trigger.file }}", max_width: 800}
  - id: webp
    action: image.convert
    with: {image: "{{ kucult.image }}", format: webp, quality: 80}
  - id: tasi
    action: file.move
    with:
      file: "{{ webp.image }}"
      to_dir: $SANDBOX/web
      pattern: "{name}-web.{ext}"
YAML
export MOVO_FLOW_DIR="$SANDBOX"

echo "— doğrulama"
check "geçerli akış kontrolden geçiyor"      "$MOVO kontrol $SANDBOX/test-movoi.yaml"
check "bozuk akış reddediliyor"              "! $MOVO kontrol tests/bozuk.yaml"
check "bozuk akışta 5 hata bulunuyor"        "[ \$($MOVO kontrol tests/bozuk.yaml 2>&1 | grep -c hata) -eq 5 ]"
check "örnek akışların hepsi geçerli"        "$MOVO kontrol"

echo "— prova (hiçbir şey yazmamalı)"
$MOVO calistir test-movoi --dosya "$SANDBOX/buyuk.png" --prova >/dev/null 2>&1
check "prova dosya üretmiyor"                "[ -z \"\$(ls -A $SANDBOX/web)\" ]"

echo "— tek seferlik çalıştırma"
$MOVO calistir test-movoi --dosya "$SANDBOX/buyuk.png" >/dev/null 2>&1
check "webp üretildi"                        "[ -f $SANDBOX/web/buyuk-web.webp ]"
check "genişlik 800'e indi"                  "python3 -c \"
from PIL import Image; im=Image.open('$SANDBOX/web/buyuk-web.webp')
assert im.size==(800,533), im.size; assert im.format=='WEBP'\""
check "kaynak dosya yerinde kaldı"           "[ -f $SANDBOX/buyuk.png ]"
check "küçük resim büyütülmedi"              "$MOVO calistir test-movoi --dosya $SANDBOX/kucuk.jpg && python3 -c \"
from PIL import Image; assert Image.open('$SANDBOX/web/kucuk-web.webp').size==(400,300)\""
check "aynı adla ikinci dosya üzerine yazmıyor" "$MOVO calistir test-movoi --dosya $SANDBOX/buyuk.png && [ -f $SANDBOX/web/buyuk-web-2.webp ]"

echo "— tip sözü çalışma anında doğrulanıyor"
out=$($MOVO calistir test-movoi --dosya "$SANDBOX/resim-degil.png" 2>&1)
check "resim olmayan dosya atlanıyor"        "echo \"\$out\" | grep -q atlandı"
check "atlanan dosya için çıktı üretilmiyor" "[ ! -f $SANDBOX/web/resim-degil-web.webp ]"

echo "— izin modeli sözleşme"
check "beyan edilmemiş izin reddediliyor"    "python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from movo.runner import RunContext, PermissionDenied
from pathlib import Path
ctx = RunContext(workdir=Path('/tmp'), step_id='x', granted=frozenset({'fs.read'}))
try:
    ctx.require('fs.write')
except PermissionDenied:
    sys.exit(0)
sys.exit(1)
PY"

echo "— klasör izleyici (inotify)"
# Arka planda çalışacak akış önce onaylanmalı; 'izle' onaysız reddeder.
check "onaysız izleme reddediliyor"          "! $MOVO izle test-movoi --bir --quiet"
check "onay veriliyor"                       "$MOVO onay test-movoi --onayla"
rm -f "$SANDBOX/web"/*
( $MOVO izle test-movoi --bir --quiet >"$SANDBOX/izle.log" 2>&1 ) &
watcher=$!
sleep 1.2
cp "$SANDBOX/buyuk.png" "$SANDBOX/gelen/yeni.png"
wait $watcher 2>/dev/null
check "izleyici yeni dosyayı yakaladı"       "[ -f $SANDBOX/web/yeni-web.webp ]"
check "izleyici journald için satır bastı"   "grep -q 'test-movoi: yeni.png: tamam' $SANDBOX/izle.log"

echo "— systemd birim üretimi"
check "service birimi üretiliyor"            "$MOVO kur test-movoi --prova | grep -q 'ExecStart=.*izle'"
check "zamanlanmış akış timer üretiyor"      "$MOVO kur gece-rapor --prova | grep -q 'OnCalendar=\*-\*-\* 03:00:00'"
check "ağ akışı dispatcher script'i hazırlıyor" "$MOVO kur ev-wifi | grep -q 'sudo install'"
check "şema mermaid üretiyor"                "$MOVO sema test-movoi | grep -q 'flowchart TD'"
check "eylem paleti json veriyor"            "$MOVO eylemler --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d)>=9'"

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
