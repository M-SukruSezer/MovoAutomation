#!/usr/bin/env bash
# Koşul ve dallanma testi. Üç soru soruyor:
#   1. Hatalı koşul yüklenmeden yakalanıyor mu?
#   2. Doğru dal çalışıp yanlış dal atlanıyor mu?
#   3. Atlanan adıma bağlı adım da atlanıyor mu? (zincirleme)
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-kosul-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

pass=0; fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }
# grep'li kontrol: çıktıda beklenen metin var mı
has()  { if printf '%s' "$2" | grep -qF "$3"; then ok "$1"; else
           no "$1 — gelen: $(printf '%s' "$2" | tail -3)"; fi; }

export MOVO_FLOW_DIR="$SANDBOX"
mkdir -p "$SANDBOX/kutu"

echo "— koşul dili (motorsuz)"
check "işleçler ve kısayollar ayrışıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import cond as C
g,e = C.parse({'left':'{{ a.n }}','op':'<','right':20}); assert not e
assert C.describe(g) == 'a.n < 20', C.describe(g)
assert C.refs(g) == ['a.n']
g2,_ = C.parse('{{ a.ok }}')                      # kısayol
assert g2.conds[0].op == 'doğru' and C.dump(g2) == '{{ a.ok }}'
g3,_ = C.parse({'any':[{'left':1,'op':'==','right':1},'{{ a.ok }}']})
assert g3.mode == 'any' and len(g3.conds) == 2
g4,_ = C.parse({'left':'{{ a.s }}','op':'contains','right':'x'})  # İngilizce takma ad
assert g4.conds[0].op == 'içerir'
\""
check "gerekçe çözülmüş değerleri gösteriyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo import cond as C
g,_ = C.parse({'left':'{{ pil.yuzde }}','op':'<','right':20})
ok, why = C.evaluate(g, lambda o: 79 if isinstance(o,str) and 'pil' in o else o)
assert ok is False and why == 'pil.yuzde (79) < 20 değil', why
g2,_ = C.parse([{'left':'{{ a.b }}','op':'doğru'},{'left':'{{ a.n }}','op':'>','right':5}])
vals = {'{{ a.b }}': True, '{{ a.n }}': 9}
ok2, why2 = C.evaluate(g2, lambda o: vals.get(o, o))
assert ok2 and ' ve ' in why2, why2
\""

echo "— statik denetim"
cat > "$SANDBOX/bozuk.yaml" <<'YAML'
name: Bozuk koşullar
slug: bozuk-kosullar
trigger: {type: manual}
steps:
  - id: dosya
    action: file.exists
    with: {path: /tmp}
  - id: a
    action: oturum.kilitle
    when: {left: "{{ dosya.path }}", op: "<", right: 20}
  - id: b
    action: oturum.kilitle
    when: {left: "{{ dosya.yok }}", op: doğru}
  - id: c
    action: oturum.kilitle
    when: {left: "{{ dosya.path }}", op: doğru}
  - id: d
    action: oturum.kilitle
    when: {left: "{{ dosya.size }}", op: "~=", right: 1}
  - id: e
    action: oturum.kilitle
    when: {left: "{{ e.sonuc }}", op: doğru}
  - id: f
    action: oturum.kilitle
    when: {left: "{{ dosya.path }}", op: içerir}
YAML
out="$($MOVO kontrol bozuk-kosullar 2>&1)"
check "bozuk koşullu akış reddediliyor"  "! $MOVO kontrol bozuk-kosullar"
check "altı koşul hatası da bulunuyor"   "[ \$(printf '%s' \"\$out\" | grep -c 'hata .*\.when') -eq 6 ]"
has "yol sayıyla karşılaştırılamıyor"    "$out" "sayı karşılaştırması; sol taraf yol"
has "olmayan çıktı yakalanıyor"          "$out" "'yok' çıktısı yok"
has "metne 'doğru' uygulanamıyor"        "$out" "doğru/yanlış bekler"
has "bilinmeyen işleç yakalanıyor"       "$out" "bilinmeyen işleç '~='"
has "adım kendi çıktısına dayanamıyor"   "$out" "kendi çıktısına dayanamaz"
has "eksik sağ taraf yakalanıyor"        "$out" "iki taraflı; 'right' eksik"

echo "— dallanma (gerçek çalıştırma)"
cat > "$SANDBOX/dal.yaml" <<YAML
name: Dal denemesi
slug: dal
trigger: {type: manual}
steps:
  - id: dosya
    action: file.exists
    with: {path: $SANDBOX/kutu/hedef.txt}
  - id: kopyala
    action: file.copy
    when: {left: "{{ dosya.exists }}", op: doğru}
    with: {file: $SANDBOX/kutu/hedef.txt, to_dir: $SANDBOX/kutu, pattern: "kopya.txt"}
  - id: adlandir
    action: file.rename
    with: {file: "{{ kopyala.file }}", pattern: "ikinci.txt"}
  - id: buyuk
    action: ui.notify
    when:
      - {left: "{{ dosya.exists }}", op: doğru}
      - {left: "{{ dosya.size }}", op: ">", right: 100000}
    with: {title: büyük}
YAML

out="$($MOVO calistir dal 2>&1)"
has "dosya yokken kopyalama atlanıyor"   "$out" "⊘ atlandı: dosya.exists (False) doğru"
has "atlanan adıma bağlı adım da atlanıyor" "$out" "⊘ atlandı: kopyala atlandığı için"
has "özet atlananları sayıyor"           "$out" "1/4 adım, 3 atlandı"
check "atlanan dal dosya üretmiyor"      "[ -z \"\$(ls -A $SANDBOX/kutu)\" ]"
check "atlanan adımlar hata sayılmıyor"  "$MOVO calistir dal"

printf 'merhaba\n' > "$SANDBOX/kutu/hedef.txt"
out="$($MOVO calistir dal 2>&1)"
has "dosya varken dal çalışıyor"         "$out" "koşul: dosya.exists (True) doğru"
check "dalın ürettiği dosya yerinde"     "[ -f $SANDBOX/kutu/ikinci.txt ]"
has "iki koşuldan biri tutmayınca atlanıyor" "$out" "dosya.size (8) > 100000 değil"

echo "— prova koşula karar vermez"
out="$($MOVO calistir dal --prova 2>&1)"
has "provada koşul yalnızca bildiriliyor" "$out" "koşul (çalışma anında bakılacak)"
check "provada hiçbir adım atlanmıyor"    "[ \$(printf '%s' \"\$out\" | grep -c 'atlandı') -eq 0 ]"

echo "— metin ve editör görünümü"
has "kontrol koşulları listeliyor"       "$($MOVO kontrol dal 2>&1)" "⋔ kopyala: dosya.exists doğru"
has "sema koşullu düğümü altıgen çiziyor" "$($MOVO sema dal 2>&1)" 'kopyala{{"⋔'
has "sema koşul kablosunu kesikli çiziyor" "$($MOVO sema dal 2>&1)" 'dosya -.->'
check "depodaki örnek akışlar hâlâ geçerli" "env -u MOVO_FLOW_DIR $MOVO kontrol"

check "editör koşulu port olarak görüyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-kontrol.yaml'))
assert d.flow.ok
assert ('yedek','exists','yok','when') in d.wires()
c = d.single_cond('yok'); assert c.op == 'yanlış', c
assert d.connection_of('yok','when') == ('yedek','exists')
assert d.single_cond('kucuk') is None          # çok koşullu: panelde salt okunur
assert d.can_connect('yedek','size','yok','when')[0]      # koşul portu her tipi alır
assert not d.can_connect('yedek','size','yok','title')[0] # normal port tipi tutuyor
\""
check "editörde koşul kurup kaldırmak YAML'a yazılıyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-kontrol.yaml'))
d.clear_guard('yok'); assert d.step('yok').get('when') is None
assert d.connect('yedek','size','yok','when') is None
assert d.step('yok')['when']['op'] == '>'      # sayı kaynağı -> eşik testi
assert d.set_cond('yok', op='>=', right=1024) is None
assert d.validate().ok
assert 'op: ' in d.to_yaml() and \\\"right: 1024\\\" in d.to_yaml()
\""
check "silinen adımın koşul kablosu da kopuyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-kontrol.yaml'))
d.remove_step('yedek')
assert all('when' not in s for s in d.steps()), d.steps()
\""
check "adım yeniden adlandırılınca koşul da güncelleniyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from pathlib import Path
from movo.registry import Registry
from movo.gui.model import Doc
d = Doc.open(Registry.discover(), Path('movo/ornekler/yedek-kontrol.yaml'))
assert d.rename_step('yedek','arsiv') is None
assert d.validate().ok, [str(p) for p in d.flow.problems]
assert 'arsiv.exists' in str(d.step('yok')['when'])
\""

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
