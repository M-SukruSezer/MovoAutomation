#!/usr/bin/env bash
# İzin yalıtımı testi.
#
# Asıl soru şu: eylem ``ctx.require`` çağırmadan sözleşmesinin dışına
# çıkabilir mi? Testin kalbi, bunu *kasten* deneyen bir eylem --
# yalıtım çalışıyorsa çekirdek onu durdurur, çalışmıyorsa test
# başarısız olur. Yani burası iddianın kanıtı.
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-yalitim-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
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
mkdir -p "$SANDBOX/gelen" "$SANDBOX/cikan" "$SANDBOX/gizli" "$SANDBOX/eylem" "$SANDBOX/kod"
printf 'parola\n' > "$SANDBOX/gizli/sir.txt"
python3 - "$SANDBOX" <<'PY'
import sys
from pathlib import Path
from PIL import Image
s = Path(sys.argv[1]) / "gelen"
for i, (w, h) in enumerate([(1600, 1000), (900, 700)], 1):
    Image.new("RGB", (w, h), (50 * i % 255, 90, 160)).save(s / f"r{i}.png")
PY

echo "— çekirdek desteği"
check "destek durumu raporlanıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import isolate as I
var, neden = I.available()
assert isinstance(var, bool) and neden
print(neden)
\""
DESTEK=$(python3 -c "
import sys; sys.path.insert(0,'.')
from movo import isolate as I
print('evet' if I.available()[0] else 'hayir')" 2>/dev/null)

echo "— ilke türetme (çekirdeğe dokunmaz)"
check "izinler haklara çevriliyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo import isolate as I
wd = Path('$SANDBOX/is')
p = I.policy_for(['fs.read'], {'f': '$SANDBOX/gelen/r1.png'}, wd)
assert '$SANDBOX/gelen' in p.read
assert '$SANDBOX/gelen' not in p.write          # yazma izni yok
assert str(wd) in p.write                        # geçici alan her zaman açık
assert not p.allow_exec and not p.allow_net and not p.allow_bus
q = I.policy_for(['fs.write','exec','net'], {'d': '$SANDBOX/cikan'}, wd)
assert '$SANDBOX/cikan' in q.write and q.allow_exec and q.allow_net
r = I.policy_for(['dbus:org.freedesktop.Notifications'], {}, wd)
assert r.allow_bus and not r.allow_exec
\""
check "yol olmayan argümanlar kural üretmiyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo import isolate as I
p = I.policy_for(['fs.write'], {'n': 42, 'b': True, 'metin': 'merhaba'},
                 Path('$SANDBOX/is'))
assert p.write == {'$SANDBOX/is'}, p.write
\""

if [ "$DESTEK" != "evet" ]; then
  echo "— çekirdekte Landlock yok, canlı testler atlanıyor"
  for t in "yasak yazma reddediliyor" "ev dizinine yazma reddediliyor" \
           "çalıştırma reddediliyor" "ağ reddediliyor" "izinli iş çalışıyor" \
           "akış uçtan uca yalıtımla geçiyor" "döngü tek çatallamayla yalıtılıyor"; do
    atla "$t"
  done
else
  echo "— kaçış denemeleri (eylem ctx.require çağırmıyor)"
  cat > "$SANDBOX/kod/kacak_eylem.py" <<PY
"""Kasten sözleşmesinin dışına çıkmaya çalışan eylem."""
import os
import subprocess
from pathlib import Path


def dene(ctx, hedef):
    # Dikkat: hiçbir ctx.require çağrısı yok. Süreç içi sözleşme burada
    # hiçbir şey yapmaz; duracaksa çekirdek durduracak.
    rapor = {}

    def dene_et(ad, f):
        try:
            f()
            rapor[ad] = "GECTI"
        except OSError as e:
            rapor[ad] = "reddedildi"
        except Exception as e:
            rapor[ad] = "reddedildi"

    dene_et("yasak_yazma", lambda: open("$SANDBOX/gizli/kacak.txt", "w").write("x"))
    dene_et("gizli_okuma", lambda: open("$SANDBOX/gizli/sir.txt").read())
    dene_et("ev_yazma", lambda: open(Path.home() / ".movo-kacak", "w").write("x"))
    dene_et("calistirma", lambda: subprocess.run(["/bin/echo", "x"],
                                                 capture_output=True, check=True))
    dene_et("tcp", lambda: __import__("socket").create_connection(("1.1.1.1", 80), 2))
    dene_et("izinli_yazma", lambda: open(Path(hedef) / "tamam.txt", "w").write("x"))
    dene_et("gecici_yazma", lambda: open(Path(ctx.workdir) / "ara.bin", "wb").write(b"x"))
    return {"rapor": " ".join(f"{k}={v}" for k, v in rapor.items())}
PY
  cat > "$SANDBOX/eylem/kacak.action.yaml" <<'YAML'
id: test.kacak
name: Kaçış denemesi
summary: Beyan ettiğinden fazlasını yapmaya çalışır.
category: uzman
permissions: [fs.write]
inputs:
  hedef: {type: directory, label: Hedef, required: true}
outputs:
  rapor: {type: text, label: Rapor}
impl: {kind: python, entry: kacak_eylem:dene}
YAML
  cat > "$SANDBOX/kacak.yaml" <<YAML
name: Kaçış
slug: kacak
isolate: true
trigger: {type: manual}
steps:
  - id: dene
    action: test.kacak
    with: {hedef: $SANDBOX/cikan}
  - id: goster
    action: shell.run
    with: {command: "echo RAPOR {{ dene.rapor }}"}
YAML
  out="$(PYTHONPATH=$SANDBOX/kod MOVO_ACTION_PATH=$SANDBOX/eylem \
         $MOVO calistir kacak 2>&1)"
  has "yasak yazma reddediliyor"      "$out" "yasak_yazma=reddedildi"
  has "beyansız okuma reddediliyor"   "$out" "gizli_okuma=reddedildi"
  has "ev dizinine yazma reddediliyor" "$out" "ev_yazma=reddedildi"
  has "çalıştırma reddediliyor"       "$out" "calistirma=reddedildi"
  has "ağ reddediliyor"               "$out" "tcp=reddedildi"
  has "beyan edilen yazma çalışıyor"  "$out" "izinli_yazma=GECTI"
  has "geçici alan yazılabiliyor"     "$out" "gecici_yazma=GECTI"
  check "kaçan dosya gerçekten oluşmadı" "[ ! -e $SANDBOX/gizli/kacak.txt ]"
  check "ev dizini kirlenmedi"           "[ ! -e \$HOME/.movo-kacak ]"

  echo "— yalıtımsız aynı eylem kaçabiliyor (karşılaştırma)"
  sed 's/^isolate: true$/isolate: false/' "$SANDBOX/kacak.yaml" \
    | sed 's/^slug: kacak$/slug: kacak-acik/' > "$SANDBOX/kacak-acik.yaml"
  out="$(PYTHONPATH=$SANDBOX/kod MOVO_ACTION_PATH=$SANDBOX/eylem \
         $MOVO calistir kacak-acik 2>&1)"
  has "yalıtımsız kaçış başarılı oluyor" "$out" "yasak_yazma=GECTI"
  rm -f "$SANDBOX/gizli/kacak.txt" "$HOME/.movo-kacak"

  echo "— gerçek akış yalıtımla"
  cat > "$SANDBOX/isle.yaml" <<YAML
name: Yalıtımlı işleme
slug: isle
isolate: true
trigger: {type: manual}
steps:
  - id: bul
    action: file.glob
    with: {dir: $SANDBOX/gelen, pattern: "*.png", produces: image}
  - id: kucult
    action: image.resize
    for_each: "{{ bul.files }}"
    with: {image: "{{ item.value }}", max_width: 400}
  - id: tasi
    action: file.move
    for_each: "{{ kucult.image }}"
    with: {file: "{{ item.value }}", to_dir: $SANDBOX/cikan, pattern: "{name}-k.png"}
YAML
  out="$($MOVO calistir isle 2>&1)"
  check "akış uçtan uca yalıtımla geçiyor" "$MOVO calistir isle >/dev/null 2>&1"
  has "yalıtım raporlanıyor"           "$out" "yalıtıldı (Landlock"
  has "döngü tek adımda yalıtılıyor"   "$out" "2/2 öge çalıştı"
  check "çıktılar yerinde"             "[ -f $SANDBOX/cikan/r1-k.png ]"
  has "kontrol yalıtımı bildiriyor"    "$($MOVO kontrol isle 2>&1)" "▣ yalıtım: 3 adım"

  echo "— sözleşme ihlali yalıtımda da hata"
  cat > "$SANDBOX/eylem/izinsiz.action.yaml" <<'YAML'
id: test.izinsiz
name: İzinsiz
permissions: []
inputs:
  path: {type: path, required: true}
outputs:
  exists: {type: bool}
  size: {type: number}
  path: {type: path}
impl: {kind: python, entry: movo.actions.builtin:file_exists}
YAML
  cat > "$SANDBOX/izinsiz.yaml" <<YAML
name: İzinsiz
slug: izinsiz
isolate: true
trigger: {type: manual}
steps:
  - id: a
    action: test.izinsiz
    on_error: devam
    with: {path: $SANDBOX/gelen}
  - id: b
    action: shell.run
    when: {left: "{{ a.tamam }}", op: yanlış}
    with: {command: "echo DAL"}
YAML
  out="$(MOVO_ACTION_PATH=$SANDBOX/eylem $MOVO calistir izinsiz 2>&1)"
  has "ctx.require ihlali yalıtımdan geçip hata oluyor" "$out" "izin reddedildi"
  check "hata dalı yine çalışmıyor" "! printf '%s' \"\$out\" | grep -q DAL"
fi

echo "— destek yokken zarif düşüş"
check "yalıtım kurulamazsa adım korumasız çalışır" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo import isolate as I
from movo.registry import Registry
from movo.flow import _build
from movo.runner import run
I.available = lambda: (False, 'test: kapalı')          # desteği kapat
reg = Registry.discover()
f = _build({'name':'x','slug':'x','isolate':True,'trigger':{'type':'manual'},
            'steps':[{'id':'s','action':'file.exists',
                      'with':{'path':'$SANDBOX/gelen'}}]}, reg)
assert any(p.level=='uyarı' and 'yalıtım' in p.message for p in f.problems), f.problems
r = run(f, reg, quiet=True)
assert r.ok, [s.error for s in r.steps]
assert any('yalıtım kurulamadı' in l for s in r.steps for l in s.logs), r.steps[0].logs
\""

echo "— editör"
check "yalıtım anahtarı YAML'a yazılıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/toplu-webp.yaml'))
assert d.flow.ok, [str(p) for p in d.flow.problems]
assert d.isolated('kucult')                       # akış genelinden miras
n = {x.id: x for x in d.nodes()}
assert n['kucult'].isolated
d.set_isolated('kucult', False)                   # tek adımı korumasız bırak
assert d.step('kucult')['isolate'] is False
assert not d.isolated('kucult') and d.isolated('tasi')
d.set_isolated('kucult', True)
assert 'isolate' not in d.step('kucult')          # akış geneliyle aynıysa yazılmaz
\""
check "depodaki örnek akışlar geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
[ "$skip" -gt 0 ] && printf '\033[33m%d atlandı\033[0m, ' "$skip"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
