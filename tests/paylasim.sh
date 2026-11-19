#!/usr/bin/env bash
# Akış paylaşımı testi.
#
# Paylaşmak kolay (akış zaten bir dosya); asıl iş **alma** tarafında.
# Üç şey sınanıyor: paket taşınabilir mi (gönderenin ev yolu sızıyor mu),
# alıcı ne istendiğini kendisi hesaplıyor mu, ve paket kod taşıyabiliyor mu.
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-paylasim-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
export XDG_STATE_HOME="$SANDBOX/durum"
pass=0; fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }
has()  { if printf '%s' "$2" | grep -qF "$3"; then ok "$1"; else
           no "$1 — gelen: $(printf '%s' "$2" | tail -3)"; fi; }
yok()  { if printf '%s' "$2" | grep -qF "$3"; then
           no "$1 — '$3' çıkmamalıydı"; else ok "$1"; fi; }

# "gönderen" tarafı
GON="$SANDBOX/gonderen"
mkdir -p "$GON/akislar" "$GON/eylemler"
cat > "$GON/eylemler/ozel.kilit.action.yaml" <<'YAML'
id: ozel.kilit
name: Özel kilit
summary: Kullanıcının kendi yazdığı deklaratif eylem.
category: masaüstü
permissions: [dbus:org.freedesktop.ScreenSaver]
impl:
  kind: dbus
  bus: session
  service: org.freedesktop.ScreenSaver
  object: /org/freedesktop/ScreenSaver
  interface: org.freedesktop.ScreenSaver
  member: Lock
YAML
cat > "$GON/akislar/gece.yaml" <<'YAML'
name: Gece kilidi
slug: gece
trigger: {type: schedule, on_calendar: "*-*-* 23:30:00"}
steps:
  - id: arsiv
    action: file.move
    with:
      file: ~/Belgeler/gunluk.txt
      to_dir: ~/Belgeler/Arşiv
      pattern: "{date}-{name}.{ext}"
  - id: kilitle
    action: ozel.kilit
YAML
GONDER="MOVO_ACTION_PATH=$GON/eylemler MOVO_FLOW_DIR=$GON/akislar"

# "alıcı" tarafı: tamamen ayrı XDG dizinleri
AL="$SANDBOX/alici"
ALICI="XDG_CONFIG_HOME=$AL/conf XDG_DATA_HOME=$AL/data XDG_STATE_HOME=$AL/state"

echo "— paketleme"
eval "$GONDER $MOVO paylas gece -o $SANDBOX/gece.movo.yaml --notu 'geceleyin'" \
  > "$SANDBOX/paylas.log" 2>&1
check "paket yazıldı"            "[ -s $SANDBOX/gece.movo.yaml ]"
paket="$(cat "$SANDBOX/gece.movo.yaml")"
has "paket başlığı açıklayıcı"   "$paket" "movo paylaşım paketi"
has "özel eylem pakete kondu"    "$paket" "id: ozel.kilit"
has "paket not taşıyor"          "$paket" "geceleyin"
has "istem özeti var"            "$paket" "istem:"
check "paket geçerli YAML"       "python3 -c \"
import yaml, sys
d = yaml.safe_load(open('$SANDBOX/gece.movo.yaml'))
assert set(d) >= {'paket','movo','eylemler'}, list(d)
assert d['paket']['sürüm'] == 1 and d['movo']['slug'] == 'gece'
\""

echo "— taşınabilirlik"
yok "gönderenin ev yolu sızmıyor" "$paket" "$HOME"
has "ev dizini ~ ile yazılıyor"   "$paket" "~/Belgeler"
check "ev dışı mutlak yol uyarılıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import share
_, uy = share.build({'steps':[{'with':{'f':'/opt/veri/x'}}]}, [], [])
assert any('/opt/veri/x' in u for u in uy), uy
\""

echo "— alıcı ne istendiğini kendi hesaplıyor"
out="$(eval "$ALICI $MOVO al $SANDBOX/gece.movo.yaml --prova" 2>&1)"
has "istem gösteriliyor"        "$out" "burada şunlara izin istiyor"
has "yerel ev dizinine açılıyor" "$out" "$HOME/Belgeler"
has "paketle gelen eylem bildiriliyor" "$out" "paketle gelen eylemler: ozel.kilit"
has "nereye yazılacağı gösteriliyor"   "$out" "$AL/conf/movo/flows/gece.yaml"
has "prova hiçbir şey yazmıyor"        "$out" "hiçbir şey yazılmadı"
check "gerçekten de yazmadı" "[ ! -e $AL/conf/movo/flows/gece.yaml ]"

check "paketin beyanı denetimde kullanılmıyor" "python3 -c \"
import sys, yaml; sys.path.insert(0,'.')
d = yaml.safe_load(open('$SANDBOX/gece.movo.yaml'))
d['paket']['istem'] = ['zararsız bir şey yapar']          # yalan beyan
yaml.safe_dump(d, open('$SANDBOX/yalanci.movo.yaml','w'), allow_unicode=True)
\" && eval \"$ALICI $MOVO al $SANDBOX/yalanci.movo.yaml --prova\" 2>&1 \\
   | grep -q 'ScreenSaver servisiyle'"
out="$(eval "$ALICI $MOVO al $SANDBOX/yalanci.movo.yaml --prova" 2>&1)"
has "yalan beyan işaretleniyor" "$out" "paketin kendi beyanı farklı"

echo "— alma"
out="$(eval "$ALICI $MOVO al $SANDBOX/gece.movo.yaml --onayla" 2>&1)"
check "akış yazıldı"        "[ -s $AL/conf/movo/flows/gece.yaml ]"
check "özel eylem yazıldı"  "[ -s $AL/data/movo/actions/ozel.kilit.action.yaml ]"
has "onay da verildi"       "$out" "onaylandı"
check "alıcıda akış geçerli" "eval \"$ALICI $MOVO kontrol gece\""
check "alıcıda onay geçerli" "eval \"$ALICI $MOVO onaylar\" | grep -q geçerli"
check "yollar alıcının evine açıldı" "grep -q \"$HOME/Belgeler\" $AL/conf/movo/flows/gece.yaml"
check "aynı slug ikinci kez reddediliyor" \
  "! eval \"$ALICI $MOVO al $SANDBOX/gece.movo.yaml --onayla\""
check "--ustune ile değiştirilebiliyor" \
  "eval \"$ALICI $MOVO al $SANDBOX/gece.movo.yaml --onayla --ustune\""

echo "— paket kod taşıyamaz"
check "python girdili eylem pakete konmuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import share
b, uy = share.build({'steps':[]},
                    [{'id':'k','impl':{'kind':'python','entry':'disarisi:f'}}], [])
assert not b.actions and any('kod taşıyamaz' in u for u in uy), (b.actions, uy)
b2, uy2 = share.build({'steps':[]},
                      [{'id':'i','impl':{'kind':'python',
                                         'entry':'movo.actions.builtin:file_exists'}}], [])
assert len(b2.actions) == 1, b2.actions      # motorun kendi gövdesi serbest
\""
python3 - <<PY
import yaml
d = yaml.safe_load(open("$SANDBOX/gece.movo.yaml"))
d["eylemler"][0]["impl"] = {"kind": "python", "entry": "kotu_modul:calistir"}
d["movo"]["slug"] = "kotu"
yaml.safe_dump(d, open("$SANDBOX/kotu.movo.yaml", "w"), allow_unicode=True)
PY
out="$(eval "$ALICI $MOVO al $SANDBOX/kotu.movo.yaml --onayla" 2>&1)"
check "kod taşıyan paket reddediliyor" \
  "! eval \"$ALICI $MOVO al $SANDBOX/kotu.movo.yaml --onayla\""
has "red gerekçesi açık"            "$out" "paket kod taşıyamaz"
check "kötü paket hiçbir şey yazmadı" "[ ! -e $AL/conf/movo/flows/kotu.yaml ]"

echo "— bozuk paketler"
printf 'merhaba\n' > "$SANDBOX/duz.yaml"
check "movo bölümü olmayan dosya reddediliyor" \
  "! eval \"$ALICI $MOVO al $SANDBOX/duz.yaml --prova\""
python3 - <<PY
import yaml
d = yaml.safe_load(open("$SANDBOX/gece.movo.yaml"))
d["paket"]["sürüm"] = 99
yaml.safe_dump(d, open("$SANDBOX/gelecek.movo.yaml", "w"), allow_unicode=True)
PY
out="$(eval "$ALICI $MOVO al $SANDBOX/gelecek.movo.yaml --prova" 2>&1)"
has "gelecek sürüm reddediliyor" "$out" "en fazla 1 okuyor"

# Paketteki eylemi olmayan, alıcıda da bulunmayan akış
python3 - <<PY
import yaml
d = yaml.safe_load(open("$SANDBOX/gece.movo.yaml"))
d.pop("eylemler")
d["movo"]["slug"] = "eksikli"
yaml.safe_dump(d, open("$SANDBOX/eksikli.movo.yaml", "w"), allow_unicode=True)
PY
# Taze bir alıcı: yukarıdaki alma sırasında ozel.kilit kuruldu, o yüzden
# "eksik" senaryosu hiç almamış birinde sınanmalı.
AL2="$SANDBOX/alici2"
ALICI2="XDG_CONFIG_HOME=$AL2/conf XDG_DATA_HOME=$AL2/data XDG_STATE_HOME=$AL2/state"
out="$(eval "$ALICI2 $MOVO al $SANDBOX/eksikli.movo.yaml --prova" 2>&1)"
has "eksik eylem bildiriliyor"  "$out" "eksik eylemler: ozel.kilit"
check "eksik eylemli paket alınmıyor" \
  "! eval \"$ALICI2 $MOVO al $SANDBOX/eksikli.movo.yaml --onayla\""

echo "— yuvarlak gidiş-geliş"
check "örnek akış paketlenip geri alınınca aynı kalıyor" "python3 -c \"
import sys, yaml; sys.path.insert(0,'.')
from pathlib import Path
from movo import share
ham = yaml.safe_load(Path('movo/ornekler/toplu-webp.yaml').read_text())
b, _ = share.build(ham, [], [])
geri = share.expand(share.loads(share.dumps(b)).flow)
beklenen = share.expand(share.portable(ham)[0])
assert geri == beklenen, [k for k in geri if geri[k] != beklenen.get(k)]
\""
check "depodaki örnek akışlar geçerli" "$MOVO kontrol"

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
