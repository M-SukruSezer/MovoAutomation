#!/usr/bin/env bash
# Paralel döngü testi.
#
# İki iddia var ve ikisi de ölçülüyor:
#   1. Paralellik sırayı bozmuyor — çıktı listesi girdi sırasını koruyor,
#      hangi öge önce bittiğinden bağımsız olarak.
#   2. Gerçekten hızlandırıyor — Pillow GIL'i bıraktığı için iş
#      parçacıkları işe yarıyor. Bu varsayım değil, ölçüm.
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-paralel-XXXXXX")"
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

export MOVO_FLOW_DIR="$SANDBOX"
mkdir -p "$SANDBOX/gelen" "$SANDBOX/cikan" "$SANDBOX/ayni/a" "$SANDBOX/ayni/b"
python3 - "$SANDBOX" <<'PY'
import random
import sys
from pathlib import Path
from PIL import Image
s = Path(sys.argv[1])
random.seed(11)
for i in range(1, 17):                       # gürültülü resim: sıkıştırma iş yapsın
    im = Image.new("RGB", (1800, 1200))
    px = im.load()
    for y in range(0, 1200, 6):
        for x in range(0, 1800, 6):
            px[x, y] = (random.randint(0, 255), (x * i) % 255, (y * i) % 255)
    im.save(s / "gelen" / f"f{i:02d}.png")
# aynı ada sahip iki kaynak: ara klasör çakışması sınavı
Image.new("RGB", (900, 600), (10, 120, 200)).save(s / "ayni/a/ayni.png")
Image.new("RGB", (400, 300), (200, 40, 40)).save(s / "ayni/b/ayni.png")
# uzantısı doğru, içi bozuk: hata ilkesi sınavı
(s / "bozuk").mkdir(exist_ok=True)
Image.new("RGB", (800, 600), (30, 90, 140)).save(s / "bozuk" / "iyi1.png")
Image.new("RGB", (800, 600), (140, 90, 30)).save(s / "bozuk" / "iyi2.png")
(s / "bozuk" / "kirik.png").write_bytes(b"png degil" * 40)
PY

movo_yaml() {  # $1 slug, $2 workers, $3 ek satır
  cat > "$SANDBOX/$1.yaml" <<YAML
name: Paralel $1
slug: $1
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, pattern: "*.png", produces: image, sort: name}
  - id: kucult
    action: image.resize
    for_each: "{{ bul.files }}"
    workers: $2
    $3
    with: {image: "{{ item.value }}", max_width: 700}
YAML
}

echo "— statik denetim"
cat > "$SANDBOX/bozuk.yaml" <<'YAML'
name: Bozuk paralel
slug: bozuk-par
trigger: {type: manual}
steps:
  - id: tek
    action: file.exists
    workers: 4
    with: {path: /tmp}
  - id: sifir
    action: file.exists
    for_each: [/tmp]
    workers: 0
    with: {path: "{{ item.value }}"}
  - id: sacma
    action: file.exists
    for_each: [/tmp]
    workers: bolca
    with: {path: "{{ item.value }}"}
YAML
out="$($MOVO kontrol bozuk-par 2>&1)"
check "bozuk paralellik reddediliyor" "! $MOVO kontrol bozuk-par"
has "döngüsüz workers yakalanıyor"    "$out" "for_each olmayan adımda"
has "sıfır işçi yakalanıyor"          "$out" "1 veya daha büyük"
has "sayı olmayan değer yakalanıyor"  "$out" "'bolca' geldi"

movo_yaml durdur 4 "on_error: dur"
has "paralel + dur uyarı veriyor" "$($MOVO kontrol durdur 2>&1)" \
  "başlamış ögeleri durduramaz"

echo "— sıra korunuyor"
movo_yaml seri 1 ""
movo_yaml dort 4 ""
movo_yaml sekiz 8 ""
movo_yaml oto auto ""
check "paralel çıktı sırası sıralıyla aynı" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
def calistir(slug):
    r = run(load(Path('$SANDBOX/%s.yaml' % slug), reg), reg, quiet=True)
    adim = next(s for s in r.steps if s.id == 'kucult')
    assert adim.status == 'tamam', adim.error
    return [Path(p).name for p in adim.outputs['image']]
temel = calistir('seri')
assert len(temel) == 16, temel
for s in ('dort', 'sekiz', 'oto'):
    assert calistir(s) == temel, (s, calistir(s)[:4], temel[:4])
\""
has "çalıştırma kaç paralel olduğunu yazıyor" "$($MOVO calistir dort 2>&1)" \
  "16 öge, 4 paralel"
has "satırlar hangi ögeden geldiğini söylüyor" "$($MOVO calistir dort 2>&1)" \
  "/16 1800x1200 -> 700x"

echo "— paralelliğin açtığı yarışlar"
cat > "$SANDBOX/cmovoma.yaml" <<YAML
name: Ad çakışması
slug: cmovoma
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/ayni, pattern: "*/ayni.png", recursive: true, produces: image}
  - id: kucult
    action: image.resize
    for_each: "{{ bul.files }}"
    workers: 4
    with: {image: "{{ item.value }}", max_width: 200}
  - id: tasi
    action: file.move
    for_each: "{{ kucult.image }}"
    workers: 4
    with: {file: "{{ item.value }}", to_dir: $SANDBOX/cikan, pattern: "ayni.png"}
YAML
check "aynı adlı kaynaklar birbirini ezmiyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
r = run(load(Path('$SANDBOX/cmovoma.yaml'), reg), reg, quiet=True)
ara = next(s for s in r.steps if s.id == 'kucult').outputs['image']
assert len(set(ara)) == 2, ara          # ögeye özel ara klasör
son = next(s for s in r.steps if s.id == 'tasi').outputs['file']
assert len(set(son)) == 2, son          # atomik ad sahiplenme
import os
for p in son:
    assert os.path.getsize(p) > 0, p
\""
check "iki farklı dosya hedefe düştü" "[ \$(ls $SANDBOX/cikan | wc -l) -eq 2 ]"

echo "— süzme ve hata ilkesi paralelde"
cat > "$SANDBOX/suz.yaml" <<YAML
name: Paralel süzme
slug: suz
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, pattern: "*.png", produces: image, sort: name}
  - id: tek
    action: image.resize
    for_each: "{{ bul.files }}"
    workers: 8
    when: {left: "{{ item.value }}", op: eşleşir, right: "*[13579].png"}
    with: {image: "{{ item.value }}", max_width: 200}
YAML
out="$($MOVO calistir suz 2>&1)"
has "koşul paralelde de süzüyor" "$out" "8/16 öge çalıştı, 8 koşula takıldı"
check "süzülmüş liste sırayı koruyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
r = run(load(Path('$SANDBOX/suz.yaml'), reg), reg, quiet=True)
adlar = [Path(p).name for p in next(s for s in r.steps if s.id=='tek').outputs['image']]
assert adlar == sorted(adlar), adlar
assert all(a[2] in '13579' for a in adlar), adlar
\""

cat > "$SANDBOX/bozukoge.yaml" <<YAML
name: Bozuk öge
slug: bozukoge
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/bozuk, pattern: "*.png", produces: image, sort: name}
  - id: kucult
    action: image.resize
    for_each: "{{ bul.files }}"
    workers: 4
    on_error: atla
    with: {image: "{{ item.value }}", max_width: 200}
YAML
out="$($MOVO calistir bozukoge 2>&1)"
has "paralelde bozuk öge atlanıyor" "$out" "2/3 öge çalıştı, 1 hata atlandı"
check "atlanan öge listeden düşüyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
r = run(load(Path('$SANDBOX/bozukoge.yaml'), reg), reg, quiet=True)
o = next(s for s in r.steps if s.id=='kucult').outputs['image']
assert [Path(p).name for p in o] == ['iyi1.png','iyi2.png'], o
\""

echo "— yalıtımla birlikte"
movo_yaml yal 4 ""
sed -i 's/^slug: yal$/slug: yal\nisolate: true/' "$SANDBOX/yal.yaml"
out="$($MOVO calistir yal 2>&1)"
has "yalıtılmış döngü paralel koşuyor" "$out" "16 öge, 4 paralel"
has "tek çatallamayla yalıtıldı"       "$out" "yalıtıldı (Landlock"
has "hepsi tamamlandı"                 "$out" "16/16 öge çalıştı"

echo "— hız (ölçüm, varsayım değil)"
olc() { python3 -c "
import sys, time; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
f = load(Path('$SANDBOX/$1.yaml'), reg)
t = time.perf_counter(); run(f, reg, quiet=True); print(int((time.perf_counter()-t)*1000))
"; }
SERI=$(olc seri); DORT=$(olc dort)
printf '    sıralı %s ms · 4 paralel %s ms\n' "$SERI" "$DORT"
check "4 paralel sıralıdan belirgin hızlı" "[ $DORT -lt \$(( SERI * 3 / 4 )) ]"

echo "— uyumluluk"
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"
check "editör işçi sayısını yazıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
assert d.flow.ok, [str(p) for p in d.flow.problems]
assert d.workers('kucult') == 'auto'
d.set_workers('kucult', 4)
assert d.step('kucult')['workers'] == 4
d.set_workers('kucult', 1)
assert 'workers' not in d.step('kucult')     # varsayılan yazılmaz
assert d.workers('tasi') == 1
\""

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
