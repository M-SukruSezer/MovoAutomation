#!/usr/bin/env bash
# Dağıtım paketlerini üretir: .tar.gz (kaynak), .deb, .rpm
#
#   packaging/paketle.sh            # üçünü de
#   packaging/paketle.sh deb        # yalnızca biri
#
# Çıktılar dist/ altına düşer.
set -euo pipefail
cd "$(dirname "$0")/.."
KOK="$PWD"
SURUM="$(python3 -c 'import re,pathlib; print(re.search(r"\"(.*?)\"", pathlib.Path("movo/__init__.py").read_text().split("__version__")[1]).group(1))')"
AD="movo"
DIST="$KOK/dist"
mkdir -p "$DIST"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

# Pakete girecek dosyalar: depo ağacı eksi derleme artıkları
agac() {
  tar -C "$KOK" -cf - \
    --exclude='.git' --exclude='dist' --exclude='build' \
    --exclude='__pycache__' --exclude='*.egg-info' --exclude='*.py[co]' \
    "$@"
}

kaynak_tar() {
  say "tar.gz"
  local st="$DIST/$AD-$SURUM"
  rm -rf "$st"; mkdir -p "$st"
  agac movo bin tests tools docs packaging README.md README.tr.md LICENSE \
       MANIFEST.in pyproject.toml | tar -C "$st" -xf -
  tar -C "$DIST" -czf "$DIST/$AD-$SURUM.tar.gz" "$AD-$SURUM"
  rm -rf "$st"
  echo "  $DIST/$AD-$SURUM.tar.gz"
}

# /usr altına yerleşim: üç paket de aynı ağacı kullanır
yerlesim() {
  local kok="$1" sitelib="$2"
  mkdir -p "$kok/$sitelib" "$kok/usr/bin" "$kok/usr/share/applications" \
           "$kok/usr/share/icons/hicolor/scalable/apps" \
           "$kok/usr/share/icons/hicolor/symbolic/apps" "$kok/usr/share/doc/$AD"
  agac movo | tar -C "$kok/$sitelib" -xf -
  cat > "$kok/usr/bin/movo" <<'BIN'
#!/usr/bin/python3
from movo.cli import main

raise SystemExit(main())
BIN
  chmod 755 "$kok/usr/bin/movo"
  install -m 644 movo/veri/io.github.movo.MovoAutomation.desktop \
          "$kok/usr/share/applications/"
  install -m 644 movo/veri/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg \
          "$kok/usr/share/icons/hicolor/scalable/apps/"
  install -m 644 movo/veri/icons/hicolor/symbolic/apps/io.github.movo.MovoAutomation-symbolic.svg \
          "$kok/usr/share/icons/hicolor/symbolic/apps/"
  install -m 644 README.md README.tr.md LICENSE "$kok/usr/share/doc/$AD/"
}

deb() {
  say "deb"
  local kok="$DIST/.deb-$SURUM"
  rm -rf "$kok"
  yerlesim "$kok" "usr/lib/python3/dist-packages"
  mkdir -p "$kok/DEBIAN"
  cat > "$kok/DEBIAN/control" <<CTRL
Package: $AD
Version: $SURUM
Section: utils
Priority: optional
Architecture: all
Maintainer: M. Şükrü Sezer <M-SukruSezer@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-yaml
Recommends: python3-pil, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Homepage: https://github.com/M-SukruSezer/movo
Description: Linux icin gorsel otomasyon motoru
 Eylem sozlugu, tip sistemi ve dugum editoru olan bir otomasyon motoru.
 Dosya islemlerini ve sistem olaylarini kod yazmadan, gorsel akislarla
 otomatiklestirir; systemd birimlerine derler ve D-Bus uzerinden
 masaustu servisleriyle konusur.
 .
 Visual automation engine for Linux: action vocabulary, type system and
 a node editor. Automates file operations and system events without
 writing code, compiles flows to systemd units and talks to desktop
 services over D-Bus.
CTRL
  fakeroot dpkg-deb --build --root-owner-group "$kok" \
           "$DIST/${AD}_${SURUM}_all.deb" >/dev/null
  rm -rf "$kok"
  echo "  $DIST/${AD}_${SURUM}_all.deb"
}

rpm() {
  say "rpm"
  local top="$DIST/.rpm"
  rm -rf "$top"; mkdir -p "$top"/{BUILD,RPMS,SOURCES,SPECS,BUILDROOT}
  local kok="$top/BUILDROOT/$AD-$SURUM"
  yerlesim "$kok" "usr/lib/python3.$(python3 -c 'import sys;print(sys.version_info.minor)')/site-packages"
  cat > "$top/SPECS/$AD.spec" <<SPEC
Name:           $AD
Version:        $SURUM
Release:        1
Summary:        Linux icin gorsel otomasyon motoru
License:        MIT
URL:            https://github.com/M-SukruSezer/movo
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-pyyaml
Recommends:     python3-pillow
Recommends:     python3-gobject
AutoReqProv:    no

%description
Eylem sozlugu, tip sistemi ve dugum editoru olan bir otomasyon motoru.
Dosya islemlerini ve sistem olaylarini kod yazmadan, gorsel akislarla
otomatiklestirir; systemd birimlerine derler ve D-Bus uzerinden
masaustu servisleriyle konusur.

Visual automation engine for Linux: action vocabulary, type system and
a node editor. Automates file operations and system events without
writing code, compiles flows to systemd units and talks to desktop
services over D-Bus.

%files
/usr/bin/movo
/usr/lib/python3*/site-packages/movo
/usr/share/applications/io.github.movo.MovoAutomation.desktop
/usr/share/icons/hicolor/scalable/apps/io.github.movo.MovoAutomation.svg
/usr/share/icons/hicolor/symbolic/apps/io.github.movo.MovoAutomation-symbolic.svg
/usr/share/doc/$AD

%changelog
SPEC
  rpmbuild --define "_topdir $top" --buildroot "$kok" \
           -bb "$top/SPECS/$AD.spec" >/dev/null 2>&1
  find "$top/RPMS" -name '*.rpm' -exec mv {} "$DIST/" \;
  rm -rf "$top"
  echo "  $DIST/$AD-$SURUM-1.noarch.rpm"
}

case "${1:-hepsi}" in
  tar|targz) kaynak_tar ;;
  deb) deb ;;
  rpm) rpm ;;
  hepsi) kaynak_tar; deb; rpm ;;
  *) echo "kullanım: $0 [tar|deb|rpm|hepsi]" >&2; exit 2 ;;
esac
say "bitti"
ls -lh "$DIST" | tail -n +2
