#!/usr/bin/env bash
# D-Bus eylem katmanının testi. İki bölüm:
#   1. manifest doğrulaması -- bus'a hiç dokunmaz, her yerde çalışır
#   2. gerçek çağrılar -- oturum bus'ı gerekir, yoksa atlanır
set -uo pipefail
cd "$(dirname "$0")/.."
MOVO=./bin/movo
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/movo-dbus-XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
# Testler kullanıcının gerçek durum dizinine (onay kayıtları,
# hazırlanan dispatcher dosyaları) dokunmamalı.
export XDG_STATE_HOME="$SANDBOX/durum"

mkdir -p "$SANDBOX/actions"
pass=0; fail=0; skip=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
atla() { printf '  \033[33m–\033[0m %s \033[2m(atlandı)\033[0m\n' "$1"; skip=$((skip+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else no "$1"; fi; }

# Bozuk manifesti ayrı bir arama yoluna koyup hata metnini yakalar.
bad() {  # bad <ad> <beklenen metin parçası> <<< manifest
  local name="$1" want="$2" out
  cat > "$SANDBOX/actions/$name.action.yaml"
  out="$(MOVO_ACTION_PATH="$SANDBOX/actions" $MOVO eylemler 2>&1 | grep -A2 "manifest sorun" || true)"
  out="$out$(MOVO_ACTION_PATH="$SANDBOX/actions" $MOVO eylemler 2>&1 || true)"
  rm -f "$SANDBOX/actions/$name.action.yaml"
  if printf '%s' "$out" | grep -qF "$want"; then ok "$name reddedildi: $want"
  else no "$name reddedilmeliydi ($want); gelen: $(printf '%s' "$out" | tail -2)"; fi
}

echo "— imza ayrıştırma"
check "GVariant imzası bölünüyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.dbus import split_sig, port_type
assert split_sig('(susssasa{sv}i)') == ['s','u','s','s','s','as','a{sv}','i']
assert split_sig('(u)') == ['u'] and split_sig('()') == []
assert split_sig('(a(ii)v)') == ['a(ii)','v']
assert port_type('u') == 'number' and port_type('b') == 'bool' and port_type('o') == 'text'
\""
check "kapanmamış imza hata veriyor" "! python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.dbus import split_sig
split_sig('(ai')\""

echo "— izin kapsamı"
check "dbus izni desenle eşleşiyor" "python3 -c \"
import sys; sys.path.insert(0,'.')
from movo.dbus import covered
assert covered('org.mpris.MediaPlayer2.vlc', ['dbus:org.mpris.MediaPlayer2.*'])
assert not covered('org.gnome.Shell', ['dbus:org.freedesktop.Notifications'])
assert not covered('org.freedesktop.Notifications', ['notify'])
\""

echo "— bozuk manifestler"
bad "izinsiz" "kapsayan bir 'dbus:...' izni yok" <<'YAML'
id: test.izinsiz
name: İzinsiz
permissions: [notify]
impl: {kind: dbus, service: org.freedesktop.Notifications,
       object: /x, interface: org.x, member: Y}
YAML

bad "imza-sayi" "imza 2 argüman istiyor, args 1 tane veriyor" <<'YAML'
id: test.imza
name: İmza
permissions: [dbus:org.x]
inputs: {a: {type: text}}
impl: {kind: dbus, service: org.x, object: /x, interface: org.x, member: Y,
       signature: "(ss)", args: [{input: a}]}
YAML

bad "yok-girdi" "'yok' diye bir girdi yok" <<'YAML'
id: test.girdi
name: Girdi
permissions: [dbus:org.x]
inputs: {a: {type: text}}
impl: {kind: dbus, service: org.x, object: /x, interface: org.x, member: Y,
       signature: "(s)", args: [{input: yok}]}
YAML

bad "yok-cikti" "diye bir çıktı portuna bağlanıyor" <<'YAML'
id: test.cikti
name: Çıktı
permissions: [dbus:org.x]
outputs: {a: {type: text}}
impl: {kind: dbus, service: org.x, object: /x, interface: org.x, member: Y,
       returns: {b: {index: 0}}}
YAML

bad "bagsiz-cikti" "returns içinde karşılığı yok" <<'YAML'
id: test.bagsiz
name: Bağsız
permissions: [dbus:org.x]
outputs: {a: {type: text}}
impl: {kind: dbus, service: org.x, object: /x, interface: org.x, member: Y}
YAML

bad "ikisi-birden" "ikisi birden ya da hiçbiri olmaz" <<'YAML'
id: test.ikisi
name: İkisi
permissions: [dbus:org.x]
impl: {kind: dbus, service: org.x, object: /x, interface: org.x,
       member: Y, property: Z}
YAML

bad "tipsiz-sozluk" "için 'type' zorunlu" <<'YAML'
id: test.sozluk
name: Sözlük
permissions: [dbus:org.x]
inputs: {a: {type: text}}
impl:
  kind: dbus
  service: org.x
  object: /x
  interface: org.x
  member: Y
  signature: "(a{sv})"
  args: [{dict: {urgency: {input: a}}}]
YAML

bad "value_type-yok" "impl.value_type zorunlu" <<'YAML'
id: test.vt
name: Değer tipi
permissions: [dbus:org.x]
inputs: {a: {type: text}}
impl: {kind: dbus, service: org.x, object: /x, interface: org.x,
       property: P, access: set, value: {input: a}}
YAML

bad "dinamik-prefixsiz" "'prefix' zorunlu" <<'YAML'
id: test.dyn
name: Dinamik
permissions: [dbus:org.x.*]
inputs: {p: {type: text}}
impl: {kind: dbus, service: {input: p}, object: /x, interface: org.x, member: Y}
YAML

echo "— yerleşik manifestler"
check "19 eylem sorunsuz yükleniyor" \
  "[ \$($MOVO eylemler 2>&1 | grep -c 'manifest sorun') -eq 0 ] && $MOVO eylemler --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d)>=19'"
check "11 eylem D-Bus gövdeli" \
  "[ \$($MOVO eylemler --json | python3 -c \"import json,sys; print(sum(1 for a in json.load(sys.stdin) if a['impl']=='dbus'))\") -eq 11 ]"
check "ui.notify artık exec izni istemiyor" \
  "! $MOVO eylemler --json | python3 -c \"
import json,sys
a = [x for x in json.load(sys.stdin) if x['id']=='ui.notify'][0]
sys.exit(0 if 'exec' in a['permissions'] else 1)\""
check "medya eylemleri joker izin taşıyor" \
  "$MOVO eylemler --json | grep -q 'dbus:org.mpris.MediaPlayer2.\*'"
check "tüm örnek akışlar geçerli" "$MOVO kontrol"

# --------------------------------------------------------- gerçek bus gerekir
if ! python3 -c "
import sys; sys.path.insert(0,'.')
from movo.dbus import names
sys.exit(0 if names('session') else 1)" 2>/dev/null; then
  echo "— oturum bus'ı yok, çalışma anı testleri atlanıyor"
  for t in "bildirim gönderiliyor" "bildirim kapatılıyor" "izin ihlali reddediliyor" \
           "eksik servis anlaşılır hata veriyor" "incele servisleri listeliyor" \
           "üretilen manifest çalışıyor" "uçtan uca D-Bus akışı"; do atla "$t"; done
else
  echo "— çalışma anı (oturum bus'ı)"
  check "bildirim gönderiliyor ve kimlik dönüyor" "python3 -c \"
import sys, pathlib; sys.path.insert(0,'.')
from movo.registry import Registry
from movo.runner import RunContext
reg = Registry.discover(); a = reg.get('ui.notify')
ctx = RunContext(workdir=pathlib.Path('/tmp'), step_id='t', quiet=True,
                 granted=frozenset(a.permissions))
out = reg.callable_for(a)(ctx, title='movo testi', body='', urgency='low', icon='')
assert out['id'] > 0, out
b = reg.get('ui.notify-kapat')
ctx2 = RunContext(workdir=pathlib.Path('/tmp'), step_id='k', quiet=True,
                  granted=frozenset(b.permissions))
reg.callable_for(b)(ctx2, id=out['id'])
\""
  ok "bildirim kapatılıyor"

  check "izin ihlali çalışma anında yakalanıyor" "python3 -c \"
import sys, pathlib; sys.path.insert(0,'.')
from movo.registry import Registry
from movo.runner import RunContext, PermissionDenied
reg = Registry.discover(); a = reg.get('medya.durum')
ctx = RunContext(workdir=pathlib.Path('/tmp'), step_id='m', quiet=True,
                 granted=frozenset(['dbus:org.freedesktop.Notifications']))
try:
    reg.callable_for(a)(ctx, player='vlc')
except PermissionDenied as e:
    assert 'org.mpris.MediaPlayer2.vlc' in str(e), e
    sys.exit(0)
sys.exit(1)\""

  check "eksik servis anlaşılır hata veriyor" "python3 -c \"
import sys, pathlib; sys.path.insert(0,'.')
from movo.registry import Registry
from movo.runner import RunContext
reg = Registry.discover(); a = reg.get('medya.durum')
ctx = RunContext(workdir=pathlib.Path('/tmp'), step_id='m', quiet=True,
                 granted=frozenset(a.permissions))
try:
    reg.callable_for(a)(ctx, player='kesinlikle-boyle-bir-oynatici-yok')
except RuntimeError as e:
    assert 'bus' in str(e), e
    sys.exit(0)
sys.exit(1)\""

  check "incele servisleri listeliyor" \
    "$MOVO incele --ara Notifications | grep -q 'org.freedesktop.Notifications'"
  check "incele kendi eylemlerimizi işaretliyor" \
    "$MOVO incele --ara Notifications | grep -q 'ui.notify'"
  check "incele arayüz üyelerini basıyor" \
    "$MOVO incele org.freedesktop.Notifications | grep -q 'CloseNotification(u'"

  # introspection → manifest → çalışan eylem: halka kapanıyor
  $MOVO incele org.gnome.Mutter.IdleMonitor --uret GetIdletime \
    > "$SANDBOX/actions/uretilen.action.yaml" 2>/dev/null
  cat > "$SANDBOX/uretilen-movo.yaml" <<'YAML'
name: Üretilen eylem testi
slug: uretilen-test
trigger: {type: manual}
steps:
  - id: bosta
    action: idlemonitor.getidletime
YAML
  check "üretilen manifest yükleniyor ve akışta çalışıyor" \
    "MOVO_ACTION_PATH=$SANDBOX/actions MOVO_FLOW_DIR=$SANDBOX $MOVO calistir uretilen-test \
      | grep -qE 'org.gnome.Mutter.IdleMonitor.GetIdletime → [0-9]+'"

  check "uçtan uca D-Bus akışı geçiyor" \
    "$MOVO calistir pil-durumu | grep -q '3/3 adım'"
  check "kontrol servis hazırlığını raporluyor" \
    "$MOVO kontrol pil-durumu | grep -q 'org.freedesktop.UPower.*hazır'"
fi

printf '\n%s: \033[32m%d geçti\033[0m, ' "$(basename "$0")" "$pass"
[ "$skip" -gt 0 ] && printf '\033[33m%d atlandı\033[0m, ' "$skip"
if [ "$fail" -gt 0 ]; then printf '\033[31m%d başarısız\033[0m\n' "$fail"; exit 1; fi
printf '0 başarısız\n'
