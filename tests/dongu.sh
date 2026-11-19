#!/usr/bin/env bash
# Liste tipleri ve döngü testi.
#   1. list<T> tip kafesine doğru oturdu mu?
#   2. for_each statik olarak denetleniyor mu?
#   3. Döngü gerçekten her öge için çalışıp sonuçları listeye topluyor mu?
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-dongu-XXXXXX")"
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
mkdir -p "$SANDBOX/gelen" "$SANDBOX/cikan"
python3 - "$SANDBOX" <<'PY'
import sys
from pathlib import Path
from PIL import Image
s = Path(sys.argv[1]) / "gelen"
for i, (w, h) in enumerate([(2400, 1600), (3000, 2000), (400, 300)], 1):
    Image.new("RGB", (w, h), (40 * i % 255, 90, 160)).save(s / f"r{i}.png")
(s / "bozuk.png").write_bytes(b"resim degil" * 20)   # uzantısı doğru, içi yanlış
(s / "notlar.txt").write_text("resim bile değil\n")
PY

echo "— liste tipleri"
check "list<T> kafese oturuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import types as T
assert T.is_assignable('list<image>', 'list<file>')      # kovaryant
assert not T.is_assignable('list<file>', 'list<image>')
assert not T.is_assignable('list<image>', 'image')       # liste tekile girmez
assert not T.is_assignable('image', 'list<image>')
assert T.is_assignable('list<image>', 'any')
assert T.label('list<image>') == 'resim listesi'
assert T.elem('list<file>') == 'file' and T.listof('text') == 'list<text>'
assert T.is_known('list<image>') and not T.is_known('list<yok>')
assert T.coerce(['1','2'], 'list<number>') == [1, 2]
\""
check "yol metnin alt tipi, tersi değil" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import types as T
assert T.is_assignable('path', 'text') and T.is_assignable('image', 'text')
assert not T.is_assignable('text', 'path')
assert T.is_assignable('list<file>', 'list<text>')
\""

echo "— statik denetim"
cat > "$SANDBOX/bozuk.yaml" <<YAML
name: Bozuk döngüler
slug: bozuk-dongu
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, produces: image}
  - id: sayi
    action: text.split
    with: {text: "a b c", separator: " "}
  - id: a
    action: image.resize
    for_each: "{{ bul.count }}"
    with: {image: "{{ item.value }}"}
  - id: b
    action: image.resize
    with: {image: "{{ bul.files }}"}
  - id: c
    action: image.resize
    with: {image: "{{ item.value }}"}
  - id: d
    action: image.resize
    for_each: "en fazla {{ bul.files }} tane"
    with: {image: "{{ item.value }}"}
  - id: e
    action: image.resize
    for_each: "{{ bul.files }}"
    on_error: belki
    with: {image: "{{ item.value }}"}
  - id: f
    action: file.glob
    with: {dir: $SANDBOX/gelen, produces: number}
YAML
out="$($MOVO kontrol bozuk-dongu 2>&1)"
check "bozuk döngülü akış reddediliyor" "! $MOVO kontrol bozuk-dongu"
has "sayı üzerinde döngü kurulamıyor"   "$out" "döngü kaynağı bir liste olmalı"
has "liste tekil porta takılmıyor"      "$out" "resim listesi veriyor, bu port resim bekliyor"
has "hata for_each'i öneriyor"          "$out" "for_each:"
has "item döngüsüz adımda reddediliyor" "$out" "yalnızca for_each kullanan adımın içinde geçerli"
has "for_each gömülü referans almıyor"  "$out" "tek bir {{ adim.cikti }} ya da düz bir liste olmalı"
has "bilinmeyen hata ilkesi yakalanıyor" "$out" "'belki' geçerli değil"
has "saçma daraltma yakalanıyor"        "$out" "bir dosya değil"

echo "— döngü (gerçek çalıştırma)"
cat > "$SANDBOX/dongu.yaml" <<YAML
name: Döngü denemesi
slug: dongu
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, pattern: "*", produces: image, sort: name}
  - id: kucult
    action: image.resize
    for_each: "{{ bul.files }}"
    on_error: atla
    with: {image: "{{ item.value }}", max_width: 800}
  - id: tasi
    action: file.move
    for_each: "{{ kucult.image }}"
    with:
      file: "{{ item.value }}"
      to_dir: $SANDBOX/cikan
      pattern: "{name}-{{ item.index }}.png"
  - id: ozet
    action: text.join
    with: {parts: "{{ tasi.file }}", separator: " | "}
YAML
out="$($MOVO calistir dongu 2>&1)"
check "akış geçerli"                     "$MOVO kontrol dongu"
has "uzantısı uymayan dosya elendi"      "$out" "1 dosya 'image' uzantısı olmadığı için elendi"
has "bozuk öge atlanıp devam edildi"     "$out" "3/4 öge çalıştı, 1 hata atlandı"
has "sonraki adım kısalan listeyi aldı"  "$out" "3/3 öge çalıştı"
check "her öge için bir dosya üretildi"  "[ \$(ls $SANDBOX/cikan | wc -l) -eq 3 ]"
check "item.index ad desenine girdi"     "[ -f $SANDBOX/cikan/r1-1.png ] && [ -f $SANDBOX/cikan/r3-3.png ]"
check "çıktılar listeye toplandı"        "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.flow import load
from movo.runner import run
reg = Registry.discover()
f = load(Path('$SANDBOX/dongu.yaml'), reg)
assert f.port_types[('kucult','image')] == 'list<image>', f.port_types[('kucult','image')]
assert f.port_types[('bul','files')] == 'list<image>'     # produces ile daraldı
assert f.port_types[('kucult','width')] == 'list<number>'
\""

echo "— süzme: for_each + when"
cat > "$SANDBOX/suz.yaml" <<YAML
name: Süzme
slug: suz
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, pattern: "*.png", sort: name}
  - id: secili
    action: file.exists
    for_each: "{{ bul.files }}"
    when: {left: "{{ item.value }}", op: eşleşir, right: "*r?.png"}
    with: {path: "{{ item.value }}"}
  - id: sabit
    action: text.split
    for_each: [bir, iki]
    with: {text: "{{ item.value }}", separator: "-"}
YAML
out="$($MOVO calistir suz 2>&1)"
has "koşula takılan öge sayılıyor"   "$out" "3/4 öge çalıştı, 1 koşula takıldı"
has "gerekçe ögeyi gösteriyor"       "$out" "eşleşir '*r?.png' değil"
has "düz liste üzerinde dönülüyor"   "$out" "2/2 öge çalıştı"

echo "— hata ilkesi"
sed 's/on_error: atla/on_error: dur/' "$SANDBOX/dongu.yaml" > "$SANDBOX/dur.yaml"
sed -i 's/slug: dongu/slug: dur/' "$SANDBOX/dur.yaml"
out="$($MOVO calistir dur 2>&1)"
check "dur ilkesinde akış hata veriyor" "! $MOVO calistir dur"
has "hangi ögede patladığı yazıyor"     "$out" ". ögede: UnidentifiedImageError"
check "izin ihlali döngüde de atlanmıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.runner import RunContext, PermissionDenied, _run_loop
from movo.flow import Step
reg = Registry.discover()
act = reg.get('file.exists')
step = Step(id='x', action='file.exists', args={'path': '{{ item.value }}'},
            each=['/tmp', '/tmp'], elem_type='text', on_error='atla')
ctx = RunContext(workdir=Path('/tmp'), step_id='x', quiet=True, granted=frozenset())
# Sözleşme ihlali ne 'atla' ile yutulur ne de yakalanabilir bir sonuca
# dönüşür: çağırana olduğu gibi gider.
try:
    _run_loop(step, act, reg, ctx, {}, 0.0)
except PermissionDenied as e:
    assert 'fs.read' in str(e), e
    sys.exit(0)
sys.exit(1)
\""

echo "— prova"
out="$($MOVO calistir dongu --prova 2>&1)"
has "prova döngü kaynağını bildiriyor" "$out" "her öge için: {{ bul.files }}"
check "provada dosya üretilmiyor"      "[ \$(ls $SANDBOX/cikan | wc -l) -eq 3 ]"

echo "— metin ve editör görünümü"
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"
check "editör döngüyü port olarak görüyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
assert d.flow.ok, [str(p) for p in d.flow.problems]
assert ('bul','files','kucult','for_each') in d.wires()
assert d.elem_type('kucult') == 'image'
assert d.on_error('kucult') == 'atla'
assert d.can_connect('bul','files','tasi','for_each')[0]
oksuz = d.can_connect('bul','count','tasi','for_each')
assert not oksuz[0] and 'liste olmalı' in oksuz[1], oksuz
nodes = {n.id: n for n in d.nodes()}
assert nodes['kucult'].outputs['image'].type == 'list<image>'
assert nodes['bul'].outputs['files'].type == 'list<image>'
\""
check "editörde döngü kurup kaldırmak YAML'a yazılıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
d.clear_each('kucult')
assert 'for_each' not in d.step('kucult') and 'on_error' not in d.step('kucult')
assert d.connect('bul','files','kucult','for_each') is None
assert d.step('kucult')['for_each'] == '{{ bul.files }}'
d.set_on_error('kucult','atla')
assert d.step('kucult')['on_error'] == 'atla'
assert 'for_each:' in d.to_yaml()
\""
check "silinen adıma giden döngü kablosu kopuyor, ötekiler durur" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
d.remove_step('bul')
by = {s['id']: s for s in d.steps()}
assert 'for_each' not in by['kucult'] and 'on_error' not in by['kucult'], by['kucult']
assert by['webp']['for_each'] == '{{ kucult.image }}'   # ilgisiz döngü bozulmadı
\""
check "adım adı değişince döngü kaynağı da değişiyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
assert d.rename_step('bul','tarama') is None
assert d.validate().ok, [str(p) for p in d.flow.problems]
assert d.step('kucult')['for_each'] == '{{ tarama.files }}'
\""

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
