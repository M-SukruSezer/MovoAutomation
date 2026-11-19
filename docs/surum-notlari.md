# Movo Automation 0.1.0

İlk yayın. Türkçe açıklama aşağıda, English below.

---

## Türkçe

**Movo Automation — Linux için görsel otomasyon motoru.** macOS'taki Shortcuts/Automator'ı
mümkün kılan şey tetikleyiciler değil, uygulamaların yayınladığı ortak
eylem sözlüğü ve blokların birbirine takılmasını sağlayan tip sistemi.
Linux'ta tetikleyici altyapısı zaten var; eksik olan bu iki katman ve
üstlerindeki görsel editör. Bu sürüm onları aşağıdan yukarı kuruyor.

### Bu sürümde neler var

- **Eylem manifest standardı.** Bir uygulama kendi eylemlerini
  `~/.local/share/movo/actions/*.action.yaml` içine koyarak motora görünür
  olur — `.desktop` dosyalarının uygulama menüsü için yaptığı işin
  otomasyon karşılığı. 23 yerleşik eylem.
- **Tip sistemi.** `metin/sayı/dosya/resim/...`, alt tipleme ve `list<T>`.
  Hatalı bağlantı akış çalışmadan reddedilir.
- **Görsel düğüm editörü.** GTK4 + libadwaita. Kablo çekerken yalnızca
  uyan portlar parlar; uyumsuz bağlantı çizilmeden reddedilir. Editör ile
  YAML aynı şeyin iki yüzü: elle yazılan akış editörde açılır, editörde
  kaydedilen akış komut satırından çalışır.
- **Tetikleyiciler.** Klasör izleme (inotify), zamanlama (systemd timer),
  ağ olayı (NetworkManager dispatcher), D-Bus sinyali ve özellik değişimi.
- **D-Bus köprüsü.** `kind: dbus` eylemleri kod içermez, manifestte bir
  *adres* taşır. `movo incele` ile bus gezilir ve seçilen üyeden hazır
  manifest ya da tetikleyici bloğu üretilir.
- **Akış mantığı.** Koşul ve dallanma (`when:`), döngü (`for_each:`),
  hata dalı (`on_error: devam`) ve döngü içi paralellik (`workers:`).
  Paralellik sırayı bozmaz: çıktı listesi girdi sırasını korur.
- **Üç katmanlı izin modeli.** Manifest sözleşmesi (eylem ne istiyor),
  onay katmanı (kullanıcı neye evet dedi), Landlock yalıtımı (çekirdek ne
  bırakıyor). Onay, akışın istediklerinin parmak izine bağlı: akış daha
  fazlasını istemeye başlarsa onay bayatlar ve ne değiştiği yazılır.
- **Akış paylaşımı.** Tek dosyalık, okunabilir paket. Alma tarafında ne
  istendiği alıcının kendi kaydıyla yeniden hesaplanır; paket kod taşıyamaz.
- **systemd'ye derleme.** Akış `.service` ve `.timer` birimlerine çevrilir,
  journald'a okunabilir satırlar basar.

### Kurulum

```sh
sudo apt install ./movo_0.1.0_all.deb        # Debian, Ubuntu, Mint…
sudo dnf install ./movo-0.1.0-1.noarch.rpm   # Fedora, RHEL, openSUSE…
```

Kaynaktan:

```sh
tar xf movo-0.1.0.tar.gz && cd movo-0.1.0 && pip install .
```

Çekirdek yalnızca **PyYAML** ister. Resim eylemleri için Pillow, editör ve
D-Bus eylemleri için dağıtımınızın `python3-gi`, `gir1.2-gtk-4.0`,
`gir1.2-adw-1` paketleri gerekir. Kurulumdan sonra `movo surum` neyin var
neyin eksik olduğunu söyler.

### Bilinen eksikler

Bağımsız adımlar hâlâ sırayla çalışıyor; iç içe döngü ve yeniden deneme
yok. Yalıtım dosya sistemi ve TCP'yi kapsıyor, syscall süzgeci yok. Onay
akış bazında, adım bazında değil. Paket deposu, imzalı yayın hattı ve
Flatpak yok. Ayrıntılı liste README'nin "Eksikler" bölümünde.

---

## English

**Movo Automation — a visual automation engine for Linux.** What makes Shortcuts and
Automator work on macOS is not the triggers — it is the shared action
vocabulary applications publish and the type system that lets blocks snap
together. Linux already has the trigger infrastructure; those two layers
and the editor above them were missing. This release builds them from the
bottom up.

### What is in this release

- **Action manifest standard.** An application becomes visible to the
  engine by dropping its actions into
  `~/.local/share/movo/actions/*.action.yaml` — the automation counterpart
  of what `.desktop` files do for the application menu. 23 built-in actions.
- **Type system.** `text/number/file/image/…`, subtyping and `list<T>`. An
  invalid connection is refused before the flow runs.
- **Visual node editor.** GTK4 + libadwaita. While you drag a wire only the
  ports that fit glow; an incompatible connection is refused before it is
  drawn. The editor and the YAML are two faces of one thing: a hand-written
  flow opens in the editor, an editor-saved flow runs from the CLI.
- **Triggers.** Folder watching (inotify), scheduling (systemd timers),
  network events (NetworkManager dispatcher), D-Bus signals and property
  changes.
- **D-Bus bridge.** `kind: dbus` actions carry no code, only an *address*
  in the manifest. `movo incele` walks the bus and generates a ready
  manifest or trigger block from the member you pick.
- **Flow logic.** Conditions and branching (`when:`), loops (`for_each:`),
  an error branch (`on_error: devam`) and in-loop parallelism (`workers:`).
  Parallelism does not disturb ordering: the output list keeps the input
  order.
- **A three-layer permission model.** The manifest contract (what the
  action wants), the consent layer (what the user agreed to) and Landlock
  isolation (what the kernel allows). Consent is bound to the fingerprint
  of what a flow asks for: if the flow starts asking for more, the approval
  goes stale and says what changed.
- **Flow sharing.** A single-file, readable bundle. On import, what is
  requested is recomputed with the receiver's own registry; a bundle cannot
  carry code.
- **systemd compilation.** Flows become `.service` and `.timer` units and
  print readable lines to journald.

### Installation

```sh
sudo apt install ./movo_0.1.0_all.deb        # Debian, Ubuntu, Mint…
sudo dnf install ./movo-0.1.0-1.noarch.rpm   # Fedora, RHEL, openSUSE…
```

From source:

```sh
tar xf movo-0.1.0.tar.gz && cd movo-0.1.0 && pip install .
```

The core needs only **PyYAML**. Image actions need Pillow; the editor and
D-Bus actions need your distribution's `python3-gi`, `gir1.2-gtk-4.0` and
`gir1.2-adw-1`. After installing, `movo surum` reports what is present and
what is missing.

### Known gaps

Independent steps still run in sequence; there are no nested loops and no
retry. Isolation covers the filesystem and TCP but there is no syscall
filter. Consent is per flow, not per step. There is no package repository,
signed release pipeline or Flatpak. The full list is in the README's
"Known gaps" section.

---

**Note on language.** The command line, action names and flow keywords are
Turkish (`movo calistir`, `guc.profil`). Both READMEs describe the same
tool; command examples are identical in each.
