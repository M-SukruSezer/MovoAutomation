#!/usr/bin/env bash
# Bütün test takımlarını sırayla çalıştırır.
set -uo pipefail
cd "$(dirname "$0")/.."
takimlar=(calistir kosul dongu paralel hata yalitim onay paylasim dbus sinyal gui paket)
toplam=0; kotu=0
for t in "${takimlar[@]}"; do
  printf '\033[1m── %s\033[0m\n' "$t"
  if bash "tests/$t.sh"; then :; else kotu=$((kotu+1)); fi
  echo
done
printf '\033[1m%d takım\033[0m' "${#takimlar[@]}"
if [ "$kotu" -gt 0 ]; then printf ', \033[31m%d başarısız\033[0m\n' "$kotu"; exit 1; fi
printf ', \033[32mhepsi geçti\033[0m\n'
