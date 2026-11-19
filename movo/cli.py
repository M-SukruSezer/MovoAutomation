"""movo komut satırı.

Görsel editör en son gelir; o editörün konuşacağı arayüz budur
(``movo eylemler --json`` blok paletini, ``movo sema`` tuvali besler).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import cond as Cond
from . import consent
from . import share
from . import dbus as dbuslib
from . import isolate as iso
from . import units
from .flow import Flow, TRIGGERS, load, refs_in, sole_ref
from .manifest import ManifestError, perm_label, search_path
from .registry import Registry, bundled_dir
from .runner import run as run_flow

C = {
    "b": "\033[1m", "d": "\033[2m", "r": "\033[0m",
    "g": "\033[32m", "y": "\033[33m", "e": "\033[31m", "c": "\033[36m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = dict.fromkeys(C, "")


def bundled_flows() -> Path:
    """Paketle gelen örnek akışlar. Depodan da kurulumdan da aynı yer."""
    return Path(__file__).resolve().parent / "ornekler"


def flow_dirs() -> list[Path]:
    """Akışların arandığı yerler, öncelik sırasıyla.

    Örnekler en sonda: kullanıcının kendi akışı aynı adı taşıyorsa onunki
    kazanır. Paketin içinden geldikleri için kurulu sürümde de bulunurlar.
    """
    dirs = []
    env = os.environ.get("MOVO_FLOW_DIR")
    if env:
        dirs += [Path(p).expanduser() for p in env.split(":") if p]
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    dirs.append(Path(base).expanduser() / "movo" / "flows")
    dirs.append(bundled_flows())
    return dirs


def find_flow(ref: str) -> Path:
    p = Path(ref).expanduser()
    if p.is_file():
        return p
    for d in flow_dirs():
        for cand in (d / ref, d / f"{ref}.yaml", d / f"{ref}.yml"):
            if cand.is_file():
                return cand
    # dosya adı tutmadıysa slug veya ada göre ara
    import yaml as _yaml
    from .flow import slugify
    for cand in all_flows():
        try:
            raw = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        name = str(raw.get("name", ""))
        if ref in (raw.get("slug"), slugify(str(raw.get("slug") or name)), name):
            return cand
    raise SystemExit(f"{C['e']}akış bulunamadı:{C['r']} {ref}\n"
                     f"  aranan yerler: {', '.join(str(d) for d in flow_dirs())}")


def all_flows() -> list[Path]:
    out, seen = [], set()
    for d in flow_dirs():
        if not d.is_dir():
            continue
        for f in sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml"))):
            if f.name not in seen:
                seen.add(f.name)
                out.append(f)
    return out


def _load(ref: str, reg: Registry) -> Flow:
    path = find_flow(ref)
    try:
        return load(path, reg)
    except Exception as e:
        raise SystemExit(f"{C['e']}{path}:{C['r']} {e}") from e


def _report(flow: Flow) -> bool:
    for p in flow.problems:
        col = C["e"] if p.level == "hata" else C["y"]
        print(f"  {col}{p.level}{C['r']} {C['b']}{p.where}{C['r']}: {p.message}")
    return flow.ok


# ----------------------------------------------------------------- komutlar

def cmd_actions(args, reg: Registry) -> int:
    if args.json:
        print(json.dumps([{
            "id": a.id, "name": a.name, "summary": a.summary, "category": a.category,
            "permissions": list(a.permissions),
            "impl": a.impl_kind,
            "dbus": ({"bus": a.impl.bus, "service": a.impl.pattern,
                      "interface": a.impl.interface,
                      "member": a.impl.member or f"{a.impl.access}:{a.impl.prop}",
                      "ready": dbuslib.present(a.impl.pattern, a.impl.bus)}
                     if a.impl_kind == "dbus" else None),
            "inputs": {k: {"type": p.type, "label": p.label, "required": p.required,
                           "default": p.default, "choices": list(p.choices)}
                       for k, p in a.inputs.items()},
            "outputs": {k: {"type": p.type, "label": p.label} for k, p in a.outputs.items()},
        } for a in sorted(reg.actions.values(), key=lambda a: a.id)],
            ensure_ascii=False, indent=2))
        return 0
    for cat, acts in reg.by_category().items():
        print(f"\n{C['b']}{cat}{C['r']}")
        for a in acts:
            perms = f"  {C['d']}[{', '.join(a.permissions)}]{C['r']}" if a.permissions else ""
            print(f"  {_ready(a)}{C['c']}{a.id}{C['r']} — {a.name}{perms}")
            ins = ", ".join(f"{k}:{p.type}" + ("*" if p.required else "")
                            for k, p in a.inputs.items())
            outs = ", ".join(f"{k}:{p.type}" for k, p in a.outputs.items())
            print(f"      girdi  {ins or '—'}")
            print(f"      çıktı  {outs or '—'}")
    if reg.problems:
        print(f"\n{C['y']}manifest sorunları:{C['r']}")
        for p in reg.problems:
            print(f"  {p}")
    n_dbus = sum(1 for a in reg.actions.values() if a.impl_kind == "dbus")
    print(f"\n{len(reg.actions)} eylem, {len(reg.by_category())} kategori, "
          f"{n_dbus} tanesi D-Bus")
    if n_dbus:
        print(f"{C['d']}  ● servis bus'ta hazır   ○ servis yok   ? bus'a bakılamadı{C['r']}")
    return 0


def _ready(a) -> str:
    """D-Bus eylemleri için servisin şu an bus'ta olup olmadığını işaretler."""
    if a.impl_kind != "dbus":
        return "  "
    st = dbuslib.present(a.impl.pattern, a.impl.bus)
    if st is None:
        return f"{C['d']}? {C['r']}"
    return f"{C['g']}● {C['r']}" if st else f"{C['y']}○ {C['r']}"


def _bus_report(flow, reg) -> None:
    """Akışın konuştuğu D-Bus servislerini ve hazır olup olmadıklarını basar."""
    targets: dict = {}
    for st in flow.steps:
        a = reg.get(st.action)
        if a.impl_kind == "dbus":
            targets.setdefault((a.impl.pattern, a.impl.bus), []).append(st.id)
    if not targets:
        return
    for (pattern, bus), steps in sorted(targets.items()):
        state = dbuslib.present(pattern, bus)
        if state is None:
            mark, note = f"{C['d']}?{C['r']}", "bus'a bakılamadı"
        elif state:
            mark, note = f"{C['g']}●{C['r']}", "hazır"
        else:
            mark, note = f"{C['y']}○{C['r']}", "şu an bus'ta yok"
        print(f"  {mark} {pattern} {C['d']}({dbuslib.BUS_LABEL[bus]} bus, {note}; "
              f"{', '.join(steps)}){C['r']}")


def cmd_flows(args, reg: Registry) -> int:
    paths = all_flows()
    if not paths:
        print("hiç akış yok. Örnekler: " + ", ".join(str(d) for d in flow_dirs()))
        return 0
    for p in paths:
        try:
            f = load(p, reg)
        except Exception as e:
            print(f"  {C['e']}✗{C['r']} {p.name}: {e}")
            continue
        mark = f"{C['g']}✓{C['r']}" if f.ok else f"{C['e']}✗{C['r']}"
        trg = TRIGGERS.get(f.trigger_type, {}).get("label", f.trigger_type)
        st = units.status(f)
        state = ""
        for name, s in st.items():
            if "not-found" not in s and not s.startswith("yok"):
                state = f"  {C['d']}({name}: {s}){C['r']}"
        tag = f" {C['d']}· örnek{C['r']}" if p.parent == bundled_flows() else ""
        print(f"  {mark} {C['b']}{f.name}{C['r']} {C['d']}({p.name}){C['r']}{tag}")
        chain = " → ".join(
            s.id + ("⟳" if s.loops else "") + ("⋔" if s.conditional else "")
            + ("▣" if s.isolated else "")
            for s in f.steps)
        print(f"      {trg} → {chain}{state}")
    return 0


def cmd_check(args, reg: Registry) -> int:
    refs = args.flow or [str(p) for p in all_flows()]
    if not refs:
        print("kontrol edilecek akış yok")
        return 0
    bad = 0
    for ref in refs:
        flow = _load(ref, reg)
        print(f"{C['b']}{flow.name}{C['r']} {C['d']}({flow.source}){C['r']}")
        if _report(flow):
            perms = flow.permissions(reg)
            plist = ", ".join(f"{p}" for p in perms) or "yok"
            print(f"  {C['g']}geçerli{C['r']} — {len(flow.steps)} adım, izinler: {plist}")
            _bus_report(flow, reg)
            yalitilan = [st for st in flow.steps if st.isolated]
            if yalitilan:
                good, why = iso.available()
                mark = f"{C['g']}▣{C['r']}" if good else f"{C['y']}▣{C['r']}"
                durum = why if good else f"{why} — yalıtımsız çalışacak"
                print(f"  {mark} yalıtım: {len(yalitilan)} adım "
                      f"({', '.join(st.id for st in yalitilan)}) · {durum}")
            for st in flow.steps:
                if st.loops:
                    src = (f"{len(st.each)} sabit öge" if isinstance(st.each, list)
                           else str(st.each))
                    extra = f", hata olursa {st.on_error}" if st.on_error != "dur" else ""
                    if st.workers != 1:
                        extra += f", {st.workers} paralel"
                    print(f"  {C['c']}⟳{C['r']} {st.id}: her öge · {src}"
                          f"{extra}")
                if st.conditional:
                    print(f"  {C['c']}⋔{C['r']} {st.id}: "
                          f"{Cond.describe(st.guard)}")
        else:
            bad += 1
    return 1 if bad else 0


def cmd_run(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    ask = consent.ask_for(flow, reg)
    durum, _g = consent.check(ask)
    if durum != "var" and ask.sensitive and not args.quiet:
        # Önplanda engellemiyoruz: komutu kullanıcı yazdı. Ama ne
        # istediğini bir kez görmeden geçmesin.
        print(f"{C['y']}onaysız:{C['r']} " + "; ".join(ask.why_sensitive())
              + f" {C['d']}(movo onay {flow.slug}){C['r']}", flush=True)
    if not _report(flow):
        return 1
    trig = _trigger_values(flow, args)
    res = run_flow(flow, reg, trig, dry_run=args.prova, keep=args.tut, quiet=args.quiet)
    return 0 if res.ok else 1


def _trigger_values(flow: Flow, args) -> dict:
    vals: dict[str, str] = {}
    f = getattr(args, "dosya", None)
    if f:
        p = Path(f).expanduser().resolve()
        vals.update(file=str(p), filename=p.name, directory=str(p.parent))
    elif flow.trigger_type == "folder" and flow.trigger.get("path"):
        vals["directory"] = str(Path(str(flow.trigger["path"])).expanduser())
    for key, env in (("connection", "MOVO_TRIGGER_CONNECTION"),
                     ("state", "MOVO_TRIGGER_STATE"),
                     ("interface", "MOVO_TRIGGER_INTERFACE")):
        if os.environ.get(env):
            vals[key] = os.environ[env]
    vals.setdefault("connection", str(flow.trigger.get("connection", "")))
    vals.setdefault("state", str(flow.trigger.get("state", "")))
    return vals


#: Önplanda dinlenebilen tetikleyiciler: ikisi de uzun ömürlü bir süreç.
LIVE_TRIGGERS = ("folder", "dbus")


def cmd_watch(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    if not _report(flow):
        return 1
    if not _consent_gate(flow, reg, args, "arka planda"):
        return 2
    if flow.trigger_type not in LIVE_TRIGGERS:
        print(f"{C['e']}'{flow.name}' dinlenebilir bir tetikleyici kullanmıyor{C['r']} "
              f"({flow.trigger_type}; dinlenebilenler: {', '.join(LIVE_TRIGGERS)})")
        return 1

    def fire(values: dict, etiket: str) -> None:
        res = run_flow(flow, reg, values, quiet=args.quiet)
        if not args.quiet:
            return
        if not res.ok:
            bad = next((s for s in res.steps if s.status == "hata"), None)
            print(f"movo: {flow.slug}: {etiket}: {bad.error if bad else 'hata'}",
                  file=sys.stderr, flush=True)
            return
        # Hiçbir adım çalışmadıysa "tamam" yanıltıcı olur: koşullar
        # tutmamıştır, journald'a bakan kişi bunu bilmeli.
        word = "tamam" if any(s.status == "tamam" for s in res.steps) else "atlandı"
        print(f"movo: {flow.slug}: {etiket}: {word} ({res.summary()})", flush=True)

    t = flow.trigger
    if flow.trigger_type == "dbus":
        from .listen import listen

        def on_signal(values: dict) -> None:
            key = values.get("value", values.get("member", "sinyal"))
            fire(values, str(key))

        listen(t, on_signal, once=args.bir, quiet=args.quiet)
        return 0

    from .watch import watch_folder

    def on_file(path: Path):
        fire({"file": str(path), "filename": path.name,
              "directory": str(path.parent)}, path.name)

    watch_folder(Path(str(t["path"])), on_file,
                 match=t.get("match"), events=str(t.get("events", "created,moved_in")),
                 settle_ms=int(t.get("settle_ms", 300)),
                 ignore_existing=bool(t.get("ignore_existing", True)),
                 once=args.bir, quiet=args.quiet)
    return 0


def _ask_lines(ask) -> None:
    for line in ask.lines():
        print(f"    • {line}")


def _consent_gate(flow, reg, args, nerede: str) -> bool:
    """Gözetimsiz çalışma için onay şart. Önplan için değil.

    ``movo calistir`` yazmak zaten bir onaydır -- kullanıcı o an orada ve
    komutu kendi veriyor. Arka planda uyanıp kendiliğinden çalışacak bir
    akış ise önceden, bakarak onaylanmış olmalı.
    """
    ask = consent.ask_for(flow, reg)
    durum, g = consent.check(ask)
    if durum == "var":
        return True
    if getattr(args, "izinsiz", False):
        print(f"{C['y']}uyarı:{C['r']} onaysız çalışıyor (--izinsiz)", flush=True)
        return True
    if durum == "bayat":
        print(f"{C['e']}onay bayat:{C['r']} '{flow.name}' onaylandığından beri "
              f"daha fazlasını istiyor")
        for line in consent.diff(ask, g):
            print(f"    {line}")
    else:
        print(f"{C['e']}onay yok:{C['r']} '{flow.name}' {nerede} çalışacaksa "
              f"önce onaylanmalı")
        _ask_lines(ask)
    print(f"\n  {C['c']}movo onay {flow.slug}{C['r']} ile onaylayın "
          f"{C['d']}(ya da --izinsiz){C['r']}")
    return False


def user_action_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "movo" / "actions"


def user_flow_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "movo" / "flows"


def cmd_share(args, reg: Registry) -> int:
    """Akışı tek dosyalık, okunabilir bir pakete koyar."""
    import yaml as _yaml

    flow = _load(args.flow, reg)
    if not _report(flow):
        return 1
    raw = _yaml.safe_load(Path(flow.source).read_text(encoding="utf-8")) or {}

    # Paketle gelmeyen (kullanıcının kendi yazdığı) eylemleri de koy;
    # yerleşik olanlar alıcıda zaten var.
    yerlesik = bundled_dir().resolve()
    ozel = []
    for sid in sorted({s.action for s in flow.steps}):
        if sid not in reg:
            continue
        act = reg.get(sid)
        if act.source and act.source.resolve().parent != yerlesik:
            ozel.append(_yaml.safe_load(act.source.read_text(encoding="utf-8")))

    ask = consent.ask_for(flow, reg)
    paket, uyarilar = share.build(raw, ozel, ask.lines(), note=args.notu or "")
    metin = share.dumps(paket)

    for u in uyarilar:
        print(f"  {C['y']}uyarı{C['r']} {u}", file=sys.stderr)
    if args.cikti:
        hedef = Path(args.cikti).expanduser()
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_text(metin, encoding="utf-8")
        print(f"  {C['g']}yazıldı{C['r']} {hedef} "
              f"{C['d']}({len(ozel)} özel eylem){C['r']}")
    else:
        print(metin, end="")
    return 0


def cmd_import(args, reg: Registry) -> int:
    """Paketi inceler ve -- onay alınırsa -- kurar.

    Paketin içindeki 'istem' bölümü *göndericinin beyanı*dır; denetim
    için kullanılmaz. Ne istediği burada, alıcının kendi kaydıyla
    yeniden hesaplanır.
    """
    import tempfile

    import yaml as _yaml

    from .flow import _build

    try:
        paket = share.loads(Path(args.paket).expanduser().read_text(encoding="utf-8"))
    except (share.ShareError, OSError) as e:
        print(f"{C['e']}paket alınamadı:{C['r']} {e}")
        return 1

    yerel = share.expand(paket.flow)
    gelen, eksik = share.missing_actions(paket, reg)

    print(f"{C['b']}{paket.name}{C['r']} {C['d']}({paket.slug}){C['r']}")
    meta = paket.meta
    print(f"  {C['d']}paket: motor {meta.get('motor', '?')} · "
          f"{meta.get('tarih', '?')}{C['r']}")
    if meta.get("not"):
        print(f"  {C['d']}not: {meta['not']}{C['r']}")

    # Paketteki eylemleri geçici bir arama yoluna koyup akışı öyle doğrula
    with tempfile.TemporaryDirectory(prefix="movo-paket-") as td:
        for a in paket.actions:
            (Path(td) / f"{a['id']}.action.yaml").write_text(
                _yaml.safe_dump(a, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
        onceki = os.environ.get("MOVO_ACTION_PATH")
        os.environ["MOVO_ACTION_PATH"] = td + (f":{onceki}" if onceki else "")
        try:
            reg2 = Registry.discover()
            flow = _build(yerel, reg2)
        finally:
            if onceki is None:
                os.environ.pop("MOVO_ACTION_PATH", None)
            else:
                os.environ["MOVO_ACTION_PATH"] = onceki

        if gelen:
            print(f"  {C['c']}paketle gelen eylemler:{C['r']} {', '.join(gelen)}")
        if eksik:
            print(f"  {C['e']}eksik eylemler:{C['r']} {', '.join(eksik)}")
        if not flow.ok:
            print(f"  {C['e']}bu akış burada çalışmaz:{C['r']}")
            for pr in flow.problems:
                if pr.level == "hata":
                    print(f"    {pr.where}: {pr.message}")
            return 1

        ask = consent.ask_for(flow, reg2)
        print(f"\n  {C['b']}burada şunlara izin istiyor:{C['r']}")
        for line in ask.lines():
            print(f"    • {line}")
        if ask.sensitive:
            print(f"\n  {C['y']}dikkat:{C['r']} " + "; ".join(ask.why_sensitive()))
        # Karşılaştırmayı taşınabilir biçimde yap: paket ~ yazar, bizim
        # hesapladığımız satırlar yerel ev dizinini açar.
        iddia = list(meta.get("istem") or [])
        bizimki = [share.portable_line(x) for x in ask.lines()]
        if iddia and iddia != bizimki:
            print(f"\n  {C['y']}paketin kendi beyanı farklı{C['r']} "
                  f"{C['d']}(yukarıdaki hesaplanmış hâli esastır){C['r']}")

        hedef = user_flow_dir() / f"{flow.slug}.yaml"
        eylem_hedef = user_action_dir()
        print(f"\n  yazılacak:")
        print(f"    {hedef}" + (f"  {C['y']}(üzerine){C['r']}" if hedef.exists() else ""))
        for a in paket.actions:
            print(f"    {eylem_hedef / (a['id'] + '.action.yaml')}")
        if args.prova:
            print(f"\n  {C['d']}prova: hiçbir şey yazılmadı{C['r']}")
            return 0
        if hedef.exists() and not args.ustune:
            print(f"\n  {C['e']}'{flow.slug}' zaten var{C['r']} "
                  f"(--ustune ile değiştirin)")
            return 1

        if not args.onayla:
            try:
                cevap = input("\n  bu akışı al ve onayla? [e/H] ").strip().lower()
            except EOFError:
                cevap = ""
            if cevap not in ("e", "evet", "y", "yes"):
                print("  vazgeçildi, hiçbir şey yazılmadı")
                return 1

        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_text(
            _yaml.safe_dump(yerel, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")
        print(f"  {C['g']}yazıldı{C['r']} {hedef}")
        for a in paket.actions:
            eylem_hedef.mkdir(parents=True, exist_ok=True)
            yol = eylem_hedef / f"{a['id']}.action.yaml"
            yol.write_text(_yaml.safe_dump(a, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
            print(f"  {C['g']}yazıldı{C['r']} {yol}")

    # Onayı alıcının kendi kaydıyla ver: istem yukarıda gösterildi.
    g = consent.grant(consent.ask_for(flow, Registry.discover()), source=str(hedef))
    print(f"  {C['g']}onaylandı{C['r']} {C['d']}{g.fingerprint}{C['r']}")
    return 0


def cmd_consent(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    ask = consent.ask_for(flow, reg)
    if args.kaldir:
        if consent.revoke(flow.slug):
            print(f"  {C['g']}kaldırıldı{C['r']} {flow.slug}")
            return 0
        print(f"  {C['d']}zaten onaylı değildi{C['r']} {flow.slug}")
        return 1

    durum, g = consent.check(ask)
    print(f"{C['b']}{flow.name}{C['r']} {C['d']}({flow.slug}){C['r']}")
    print(f"  {C['b']}bu akış şunlara izin istiyor:{C['r']}")
    _ask_lines(ask)
    if ask.sensitive:
        print(f"\n  {C['y']}dikkat:{C['r']} " + "; ".join(ask.why_sensitive()))
    if durum == "var":
        print(f"\n  {C['g']}zaten onaylı{C['r']} {C['d']}({g.at}){C['r']}")
        return 0
    if durum == "bayat":
        print(f"\n  {C['y']}önceki onay bayat{C['r']} {C['d']}({g.at}){C['r']} — "
              f"o günden beri:")
        for line in consent.diff(ask, g):
            print(f"    {line}")

    if not args.onayla:
        try:
            if input("\n  onaylıyor musun? [e/H] ").strip().lower() \
                    not in ("e", "evet", "y", "yes"):
                print("  vazgeçildi")
                return 1
        except EOFError:
            print(f"  {C['e']}onay alınamadı{C['r']} (--onayla ile çalıştırın)")
            return 1
    g = consent.grant(ask, source=str(flow.source or ""))
    print(f"  {C['g']}onaylandı{C['r']} {C['d']}{g.fingerprint} · "
          f"{consent.store_path()}{C['r']}")
    return 0


def cmd_consents(args, reg: Registry) -> int:
    grants = consent.load()
    if not grants:
        print("hiç onay kaydı yok")
        print(f"{C['d']}kayıt dosyası: {consent.store_path()}{C['r']}")
        return 0
    # Akışları bir kez yükleyip her onayın hâlâ geçerli olup olmadığına bak
    asks: dict = {}
    for path in all_flows():
        try:
            f = load(path, reg)
        except Exception:
            continue
        asks[f.slug] = consent.ask_for(f, reg)
    for g in sorted(grants.values(), key=lambda x: x.slug):
        ask = asks.get(g.slug)
        if ask is None:
            mark, note = f"{C['d']}?{C['r']}", "akış bulunamadı"
        elif ask.fingerprint() == g.fingerprint:
            mark, note = f"{C['g']}✓{C['r']}", "geçerli"
        else:
            mark, note = f"{C['y']}!{C['r']}", "bayat — akış değişmiş"
        print(f"  {mark} {C['b']}{g.name or g.slug}{C['r']} "
              f"{C['d']}({g.slug}){C['r']} — {note}")
        print(f"      {C['d']}{g.at} · {g.fingerprint}{C['r']}")
        if ask is not None and ask.fingerprint() != g.fingerprint:
            for line in consent.diff(ask, g):
                print(f"      {line}")
    print(f"\n{len(grants)} onay · {C['d']}{consent.store_path()}{C['r']}")
    return 0


def cmd_install(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    if not _report(flow):
        return 1
    rendered = units.render(flow)
    perms = flow.permissions(reg)

    print(f"{C['b']}{flow.name}{C['r']}")
    print(f"  tetikleyici: {TRIGGERS[flow.trigger_type]['label']} "
          f"{C['d']}{flow.trigger}{C['r']}")
    print(f"  {C['b']}bu akış şunları yapabilecek:{C['r']}")
    for p, steps in perms.items():
        tag = "" if p.startswith(dbuslib.PERM_PREFIX) else f"{p}; "
        print(f"    • {perm_label(p)} {C['d']}({tag}{', '.join(steps)}){C['r']}")
    if flow.trigger_type == "network":
        path = units.dispatcher_path(flow)
        script = units.dispatcher_script(flow)
        staged = units.state_dir() / path.name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(script, encoding="utf-8")
        staged.chmod(0o755)
        print(f"\n  Ağ tetikleyicisi root yetkisi ister; dosya hazırlandı:\n"
              f"    {staged}\n  Kurmak için:\n"
              f"    {C['c']}sudo install -m 755 {staged} {path}{C['r']}")
        return 0
    if args.prova:
        for name, body in rendered.items():
            print(f"\n{C['d']}--- {units.user_unit_dir()/name} ---{C['r']}\n{body}")
        return 0
    if not args.onayla:
        print(f"\n  {len(rendered)} systemd kullanıcı birimi yazılacak: "
              f"{', '.join(rendered)}")
        try:
            if input("  devam? [e/H] ").strip().lower() not in ("e", "evet", "y", "yes"):
                print("  vazgeçildi")
                return 1
        except EOFError:
            print("  onay alınamadı (--onayla ile çalıştırın)")
            return 1
    # Kurulum zaten "bu akış şunları yapabilecek" diye sorup onay aldı;
    # o onayı kayda geçiriyoruz ki birim arka planda çalışabilsin.
    consent.grant(consent.ask_for(flow, reg), source=str(flow.source or ""))
    written = units.install(flow, enable=not args.etkinlestirme_yok)
    for p in written:
        print(f"  {C['g']}yazıldı{C['r']} {p}")
    for name, st in units.status(flow).items():
        print(f"  {name}: {st}")
    return 0


def cmd_uninstall(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    removed = units.uninstall(flow)
    print(f"kaldırıldı: {', '.join(removed) or 'kurulu birim yoktu'}")
    return 0


def cmd_status(args, reg: Registry) -> int:
    refs = args.flow or [str(p) for p in all_flows()]
    for ref in refs:
        flow = _load(ref, reg)
        st = units.status(flow)
        print(f"{C['b']}{flow.name}{C['r']}")
        if not st:
            print("  kurulabilir birim yok (elle/ağ tetikleyicisi)")
        for name, s in st.items():
            col = C["g"] if s.startswith("active") else C["d"]
            print(f"  {name}: {col}{s}{C['r']}")
    return 0


def cmd_logs(args, reg: Registry) -> int:
    flow = _load(args.flow, reg)
    name = units.unit_name(flow, "service")
    cmd = ["journalctl", "--user", "-u", name, "-n", str(args.satir)]
    if args.takip:
        cmd.append("-f")
    return subprocess.run(cmd, check=False).returncode


def cmd_edit(args, reg: Registry) -> int:
    """Görsel editörü açar."""
    try:
        from .gui.app import main as gui_main
    except Exception as e:  # GTK yoksa anlaşılır bir mesaj
        print(f"{C['e']}editör açılamadı:{C['r']} {e}\n"
              f"  gereken: GTK4 + libadwaita python bağlamaları "
              f"(Debian/Ubuntu: gir1.2-gtk-4.0, gir1.2-adw-1, python3-gi)",
              file=sys.stderr)
        return 2
    path = str(find_flow(args.flow)) if args.flow else None
    return gui_main(path)


DESKTOP_ID = "io.github.movo.MovoAutomation"


def data_dir() -> Path:
    """Paketle gelen masaüstü dosyaları."""
    return Path(__file__).resolve().parent / "veri"


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser()


def icon_dir() -> Path:
    """Paketle gelen hicolor simge ağacı."""
    return data_dir() / "icons" / "hicolor"


def desktop_targets() -> dict[Path, Path]:
    """kaynak -> hedef eşlemesi (kullanıcı veri dizinine)."""
    home = _xdg_data_home()
    return {
        data_dir() / f"{DESKTOP_ID}.desktop":
            home / "applications" / f"{DESKTOP_ID}.desktop",
        icon_dir() / "scalable" / "apps" / f"{DESKTOP_ID}.svg":
            home / "icons" / "hicolor" / "scalable" / "apps" / f"{DESKTOP_ID}.svg",
        icon_dir() / "symbolic" / "apps" / f"{DESKTOP_ID}-symbolic.svg":
            home / "icons" / "hicolor" / "symbolic" / "apps"
            / f"{DESKTOP_ID}-symbolic.svg",
    }


def cmd_desktop(args, reg: Registry) -> int:
    """Editörü uygulama menüsüne ekler ya da çıkarır.

    Tekerlek /usr/share'e dosya koyamaz (ve koymamalı); masaüstü
    bütünleşmesi kullanıcının kendi veri dizinine, açık bir komutla
    kuruluyor. Dağıtım paketi aynı iki dosyayı sistem genelinde kurar.
    """
    targets = desktop_targets()
    if args.kaldir:
        for _, dst in targets.items():
            if dst.exists():
                dst.unlink()
                print(f"  silindi  {dst}")
            else:
                print(f"  {C['d']}yoktu    {dst}{C['r']}")
    else:
        exe = units._exe()
        for src, dst in targets.items():
            if not src.is_file():
                print(f"{C['e']}paket dosyası eksik:{C['r']} {src}")
                return 1
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            if dst.suffix == ".desktop":
                # Sanal ortamdaki ya da depodaki movo'i göstersin
                text = text.replace("Exec=movo ", f"Exec={exe} ")
            dst.write_text(text, encoding="utf-8")
            print(f"  yazıldı  {dst}")
        print(f"\n{C['g']}Akış{C['r']} artık uygulama menüsünde. "
              f"{C['d']}Komut: {exe} duzenle{C['r']}")

    for tool, arg in (("update-desktop-database", _xdg_data_home() / "applications"),
                      ("gtk-update-icon-cache", _xdg_data_home() / "icons" / "hicolor")):
        exe_t = shutil.which(tool)
        if exe_t and Path(arg).is_dir():
            subprocess.run([exe_t, "-q", str(arg)], check=False,
                           capture_output=True)
    return 0


def _dep(mod: str, attr: str = "__version__") -> str | None:
    """Kurulu mu, kuruluysa sürümü ne? Yoksa None."""
    import importlib
    try:
        m = importlib.import_module(mod)
    except Exception:
        return None
    return str(getattr(m, attr, "?"))


def cmd_version(args, reg: Registry) -> int:
    """Sürüm ve tanılama.

    Linux masaüstünde 'çalışmıyor'un çoğu sebebi eksik bir isteğe bağlı
    bağımlılık ya da ulaşılamayan bir bus. Bunu tahmin ettirmek yerine
    tek komutta söylüyoruz.
    """
    import platform

    tick = f"{C['g']}✓{C['r']}"
    cross = f"{C['y']}✗{C['r']}"
    print(f"{C['b']}movo {__version__}{C['r']} · Python {platform.python_version()} "
          f"{C['d']}({sys.executable}){C['r']}")
    print(f"{C['d']}paket: {Path(__file__).resolve().parent}{C['r']}")

    print(f"\n{C['b']}bağımlılıklar{C['r']}")
    yaml_v = _dep("yaml")
    print(f"  {tick if yaml_v else cross} PyYAML {yaml_v or ''}"
          f"{C['d']}  çekirdek{C['r']}")
    pil = _dep("PIL")
    print(f"  {tick if pil else cross} Pillow {pil or '— pip install movo[resim]'}"
          f"{C['d']}  resim eylemleri{C['r']}")
    gi_v = _dep("gi")
    print(f"  {tick if gi_v else cross} PyGObject {gi_v or '— apt install python3-gi'}"
          f"{C['d']}  editör ve D-Bus eylemleri{C['r']}")
    if gi_v:
        try:
            import gi as _gi
            _gi.require_version("Gtk", "4.0")
            _gi.require_version("Adw", "1")
            from gi.repository import Adw, Gtk
            print(f"      {tick} GTK {Gtk.get_major_version()}."
                  f"{Gtk.get_minor_version()} + libadwaita "
                  f"{Adw.get_major_version()}.{Adw.get_minor_version()}")
        except Exception as e:
            print(f"      {cross} GTK4/libadwaita yok {C['d']}({type(e).__name__}); "
                  f"apt install gir1.2-gtk-4.0 gir1.2-adw-1{C['r']}")

    print(f"\n{C['b']}eylemler{C['r']}")
    n_dbus = sum(1 for a in reg.actions.values() if a.impl_kind == "dbus")
    print(f"  {len(reg.actions)} eylem ({n_dbus} D-Bus), "
          f"{len(reg.problems)} manifest sorunu")
    for d in search_path():
        mark = tick if d.is_dir() else f"{C['d']}·{C['r']}"
        print(f"    {mark} {d}")

    print(f"\n{C['b']}akışlar{C['r']}")
    paths = all_flows()
    ornek = sum(1 for q in paths if q.parent == bundled_flows())
    print(f"  {len(paths)} akış ({ornek} örnek)")
    for d in flow_dirs():
        mark = tick if d.is_dir() else f"{C['d']}·{C['r']}"
        print(f"    {mark} {d}")

    print(f"\n{C['b']}d-bus{C['r']}")
    for bus in ("session", "system"):
        have = dbuslib.names(bus) if gi_v else None
        label = dbuslib.BUS_LABEL[bus]
        if have is None:
            print(f"  {cross} {label} bus {C['d']}ulaşılamadı{C['r']}")
        else:
            print(f"  {tick} {label} bus {C['d']}{len(have)} servis{C['r']}")

    print(f"\n{C['b']}onaylar{C['r']}")
    grants = consent.load()
    isaret = tick if grants else f"{C['d']}·{C['r']}"
    print(f"  {isaret} {len(grants)} kayıt {C['d']}{consent.store_path()}{C['r']}")

    print(f"\n{C['b']}yalıtım{C['r']}")
    good, why = iso.available()
    print(f"  {tick if good else cross} {why}")
    forkable, fwhy = iso.can_fork()
    print(f"  {tick if forkable else cross} süreç çatallama"
          + (f" {C['d']}({fwhy}){C['r']}" if not forkable else ""))

    print(f"\n{C['b']}masaüstü{C['r']}")
    for _, dst in desktop_targets().items():
        mark = tick if dst.exists() else f"{C['d']}·{C['r']}"
        print(f"  {mark} {dst}")
    if not all(d.exists() for d in desktop_targets().values()):
        print(f"  {C['d']}menüye eklemek için: movo masaustu{C['r']}")

    print(f"\n{C['b']}systemd{C['r']}")
    sysctl = shutil.which("systemctl")
    print(f"  {tick if sysctl else cross} systemctl {C['d']}{sysctl or 'yok'}{C['r']}")
    udir = units.user_unit_dir()
    kurulu = sorted(udir.glob("movo-*")) if udir.is_dir() else []
    print(f"  {len(kurulu)} kurulu birim {C['d']}{udir}{C['r']}")
    for u in kurulu:
        print(f"    · {u.name}")
    return 0


def cmd_introspect(args, reg: Registry) -> int:
    """D-Bus'ı gezdirir. Yeni eylem yazmanın başlangıç noktası burası.

    Bir masaüstü servisini bulup üyelerini görmek, sonra --uret ile hazır
    manifesti almak: "kod yazmaya gerek kalmadan" sözünün somut hâli.
    """
    bus = "system" if args.sistem else "session"
    if not args.service:
        have = dbuslib.names(bus)
        if have is None:
            print(f"{C['e']}{dbuslib.BUS_LABEL[bus]} bus'ına bağlanılamadı{C['r']}")
            return 1
        mine: dict = {}
        for a in reg.actions.values():
            if a.impl_kind == "dbus" and a.impl.bus == bus:
                mine.setdefault(a.impl.pattern, []).append(a.id)
        import fnmatch
        rows = sorted(n for n in have
                      if not args.ara or args.ara.lower() in n.lower())
        for n in rows:
            tags = sorted(i for pat, ids in mine.items()
                          if fnmatch.fnmatch(n, pat) for i in ids)
            note = f"  {C['d']}← {', '.join(tags)}{C['r']}" if tags else ""
            print(f"  {C['c']}{n}{C['r']}{note}")
        print(f"\n{len(rows)} servis ({dbuslib.BUS_LABEL[bus]} bus)"
              + (f", '{args.ara}' süzgeciyle" if args.ara else "")
              + f"\n{C['d']}ayrıntı: movo incele <servis>"
              f"   manifest: movo incele <servis> --uret <Üye>{C['r']}")
        return 0

    tree = dbuslib.walk(bus, args.service, args.path or "/")
    if not tree:
        print(f"{C['y']}{args.service} üzerinde kendine ait arayüz bulunamadı.{C['r']}")
        print(f"{C['d']}Servis bus'ta mı? 'movo incele --ara {args.service.split('.')[-1]}'{C['r']}")
        return 1

    if args.uret:
        hit = dbuslib.find_member(tree, args.uret)
        if not hit:
            print(f"{C['e']}'{args.uret}' diye bir üye yok.{C['r']} "
                  f"Üyeleri görmek için --uret olmadan çalıştırın.")
            return 1
        kindof, path, iface, member = hit
        # Sinyal eylem olamaz, tetikleyici olur: üretilen blok akışın
        # başına gider, eylem klasörüne değil.
        if kindof == "signal":
            out = dbuslib.trigger_skeleton(bus, args.service, path, iface, member)
            note = "# akış dosyanın başına koyun"
        elif kindof == "method":
            out = dbuslib.skeleton(bus, args.service, path, iface, member)
            note = "# kaydetmek için: ~/.local/share/movo/actions/<ad>.action.yaml"
        else:
            out = dbuslib.skeleton_property(bus, args.service, path, iface, member)
            note = ("# eylem olarak: ~/.local/share/movo/actions/<ad>.action.yaml\n"
                    "# değişimini dinlemek için --tetikleyici ekleyin")
            if args.tetikleyici:
                out = dbuslib.property_trigger_skeleton(
                    bus, args.service, path, iface, member)
                note = "# akış dosyanın başına koyun"
        print(f"{C['d']}# {path} · {iface}\n{note}{C['r']}")
        print(out, end="")
        return 0

    for path, ifaces in tree.items():
        print(f"\n{C['b']}{path}{C['r']}")
        for iname, info in ifaces.items():
            print(f"  {C['c']}{iname}{C['r']}")
            for m in info["methods"]:
                print(f"      {dbuslib.sig_text(m)}")
            for pr in info["properties"]:
                acc = "okunur/yazılır" if "write" in pr["access"] else "okunur"
                print(f"      {C['d']}·{C['r']} {pr['name']}: {pr['type']} "
                      f"{C['d']}({acc}){C['r']}")
            for sg in info["signals"]:
                sig_args = ", ".join(f"{t} {n}" for n, t in sg.get("args", []))
                print(f"      {C['d']}↯ {sg['name']}({sig_args}){C['r']}")
    print(f"\n{C['d']}bir üyeden manifest üretmek için: "
          f"movo incele {args.service} --uret <Üye>{C['r']}")
    return 0


def cmd_graph(args, reg: Registry) -> int:
    """Akışı Mermaid grafiğine çevirir -- görsel editörün tuval verisi."""
    flow = _load(args.flow, reg)
    _report(flow)
    lines = ["flowchart TD"]
    trg = TRIGGERS.get(flow.trigger_type, {})
    detail = flow.trigger.get("path") or flow.trigger.get("on_calendar") \
        or flow.trigger.get("connection") or ""
    lines.append(f'  trigger(["⚡ {trg.get("label", flow.trigger_type)}'
                 + (f'<br/><small>{detail}</small>' if detail else "") + '"])')
    for s in flow.steps:
        act = reg.actions.get(s.action)
        label = s.title or (act.name if act else s.action)
        body = f'{label}<br/><small>{s.action}</small>'
        if s.conditional:
            # koşullu adım altıgen: grafa bakan "burada dallanıyor" desin
            lines.append(f'  {s.id}{{{{"⋔ {body}"}}}}')
        else:
            lines.append(f'  {s.id}["{body}"]')
    for s in flow.steps:
        edges: dict[str, list[str]] = {}
        for key, value in s.args.items():
            for ref in ([sole_ref(value)] if sole_ref(value) else refs_in(value)):
                src, _, out = ref.partition(".")
                edges.setdefault(src, []).append(f"{out}→{key}")
        for src, labels in edges.items():
            lines.append(f'  {src} -->|"{", ".join(labels)}"| {s.id}')
        if s.conditional:
            # koşul kablosu kesikli: veri değil, karar taşıyor
            why = Cond.describe(s.guard).replace('"', "'")
            for ref in dict.fromkeys(Cond.refs(s.guard)):
                src = ref.partition(".")[0]
                lines.append(f'  {src} -.->|"{why}"| {s.id}')
    print("\n".join(lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="movo",
        description="Linux için görsel otomasyon motoru (çekirdek + CLI)")
    p.add_argument("--surum", "--version", action="version",
                   version=f"movo {__version__}")
    sub = p.add_subparsers(dest="komut", required=True)

    s = sub.add_parser("eylemler", help="kayıtlı eylemleri listeler")
    s.add_argument("--json", action="store_true", help="GUI/paletin tüketeceği biçim")
    s.set_defaults(fn=cmd_actions)

    s = sub.add_parser("akislar", help="bulunan akışları listeler")
    s.set_defaults(fn=cmd_flows)

    s = sub.add_parser("kontrol", help="akışı statik olarak doğrular")
    s.add_argument("flow", nargs="*")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("calistir", help="akışı bir kez çalıştırır")
    s.add_argument("flow")
    s.add_argument("--dosya", help="tetikleyici dosya (klasör akışlarını elle denemek için)")
    s.add_argument("--prova", action="store_true", help="hiçbir şey yazmaz, planı gösterir")
    s.add_argument("--tut", action="store_true", help="ara dosyaları silme")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("izle", help="canlı tetikleyiciyi önplanda çalıştırır")
    s.add_argument("--izinsiz", action="store_true",
                   help="onay kaydı olmadan da çalıştır")
    s.add_argument("flow")
    s.add_argument("--bir", action="store_true", help="ilk dosyadan sonra çık")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("kur", help="systemd kullanıcı birimlerini yazar")
    s.add_argument("flow")
    s.add_argument("--prova", action="store_true", help="birimleri yazmadan gösterir")
    s.add_argument("--onayla", action="store_true", help="soru sormadan kur")
    s.add_argument("--etkinlestirme-yok", dest="etkinlestirme_yok", action="store_true")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("kaldir", help="kurulu birimleri kaldırır")
    s.add_argument("flow")
    s.set_defaults(fn=cmd_uninstall)

    s = sub.add_parser("durum", help="kurulu akışların systemd durumu")
    s.add_argument("flow", nargs="*")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("gunluk", help="akışın journald kaydı")
    s.add_argument("flow")
    s.add_argument("-n", "--satir", default="40")
    s.add_argument("-f", "--takip", action="store_true")
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("duzenle", help="görsel editörü açar")
    s.add_argument("flow", nargs="?")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("incele", help="D-Bus servislerini gezer, manifest iskeleti üretir")
    s.add_argument("service", nargs="?", help="bus adı (boşsa tüm servisler listelenir)")
    s.add_argument("path", nargs="?", help="nesne yolu (varsayılan: /)")
    s.add_argument("--sistem", action="store_true", help="oturum yerine sistem bus'ı")
    s.add_argument("--ara", help="servis listesini süzer")
    s.add_argument("--uret", metavar="ÜYE", help="bu üye için manifest iskeleti basar")
    s.add_argument("--tetikleyici", action="store_true",
                   help="--uret bir özellikse, eylem yerine tetikleyici bloğu üretir")
    s.set_defaults(fn=cmd_introspect)

    s = sub.add_parser("paylas", help="akışı tek dosyalık pakete koyar")
    s.add_argument("flow")
    s.add_argument("-o", "--cikti", help="dosyaya yaz (yoksa ekrana basar)")
    s.add_argument("--notu", help="pakete kısa bir not ekler")
    s.set_defaults(fn=cmd_share)

    s = sub.add_parser("al", help="paylaşım paketini inceler ve kurar")
    s.add_argument("paket")
    s.add_argument("--prova", action="store_true", help="göster, hiçbir şey yazma")
    s.add_argument("--onayla", action="store_true", help="soru sormadan al")
    s.add_argument("--ustune", action="store_true", help="aynı slug varsa değiştir")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("onay", help="akışın istediklerini gösterir ve onaylar")
    s.add_argument("flow")
    s.add_argument("--onayla", action="store_true", help="soru sormadan onayla")
    s.add_argument("--kaldir", action="store_true", help="onayı geri al")
    s.set_defaults(fn=cmd_consent)

    s = sub.add_parser("onaylar", help="verilmiş onayları listeler")
    s.set_defaults(fn=cmd_consents)

    s = sub.add_parser("surum", help="sürüm ve tanılama bilgisi")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("masaustu", help="editörü uygulama menüsüne ekler")
    s.add_argument("--kaldir", action="store_true", help="menüden çıkarır")
    s.set_defaults(fn=cmd_desktop)

    s = sub.add_parser("sema", help="akışı Mermaid grafiği olarak basar")
    s.add_argument("flow")
    s.set_defaults(fn=cmd_graph)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reg = Registry.discover()
        return args.fn(args, reg)
    except ManifestError as e:
        print(f"{C['e']}eylem hatası:{C['r']} {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130
