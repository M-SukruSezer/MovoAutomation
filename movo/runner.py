"""Çalışma zamanı: akışı adım adım yürütür.

İzin modeli burada yaşar: bir eylem, manifestinde beyan etmediği bir yetkiyi
kullanmaya çalışırsa çalışma durur. Manifest bir reklam değil, sözleşmedir.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import cond as C
from . import dbus as dbuslib
from . import isolate as iso
from . import types as T
from .flow import CATCH_PORTS, ITEM, REF, Flow, Step, _all_refs, refs_in, sole_ref
from .manifest import ActionDef
from .registry import Registry


class PermissionDenied(Exception):
    pass


class StepFailed(Exception):
    def __init__(self, step_id: str, message: str):
        super().__init__(message)
        self.step_id = step_id


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(base).expanduser() / "movo"


@dataclass
class RunContext:
    workdir: Path
    step_id: str
    granted: frozenset
    dry_run: bool = False
    quiet: bool = False
    lines: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.lines.append(msg)
        if not self.quiet:
            print(f"    {msg}", flush=True)

    def require(self, *perms: str) -> None:
        missing = [p for p in perms if p not in self.granted]
        if missing:
            raise PermissionDenied(
                f"{self.step_id}: eylem '{', '.join(missing)}' izni kullanıyor ama "
                f"manifestinde beyan etmemiş")

    def require_bus(self, service: str) -> None:
        """D-Bus izni kapsamlıdır: hangi bus adına konuşulduğu denetlenir.

        Servis adı çalışma anında bir girdiden geliyorsa (ör. MPRIS
        oynatıcısı) asıl denetim burada olur -- manifest yalnızca deseni
        beyan edebilir.
        """
        if not dbuslib.covered(service, self.granted):
            allowed = [p for p in sorted(self.granted) if p.startswith(dbuslib.PERM_PREFIX)]
            raise PermissionDenied(
                f"{self.step_id}: '{service}' servisine konuşma izni yok "
                f"(manifestte beyan edilen: {', '.join(allowed) or 'hiç'})")


@dataclass
class StepResult:
    id: str
    action: str
    status: str           # tamam | hata | yakalandı | atlandı | plan
    duration: float
    outputs: dict = field(default_factory=dict)
    error: str = ""
    logs: list[str] = field(default_factory=list)
    reason: str = ""      # atlanma gerekçesi, çözülmüş değerlerle


@dataclass
class RunResult:
    flow: str
    ok: bool
    steps: list[StepResult]
    workdir: Path
    trigger: dict = field(default_factory=dict)
    duration: float = 0.0

    def summary(self) -> str:
        good = sum(1 for s in self.steps if s.status in ("tamam", "plan"))
        sk = sum(1 for s in self.steps if s.status == "atlandı")
        ca = sum(1 for s in self.steps if s.status == "yakalandı")
        extra = f", {sk} atlandı" if sk else ""
        extra += f", {ca} hata yakalandı" if ca else ""
        return f"{good}/{len(self.steps)} adım{extra}, {self.duration*1000:.0f} ms"


def _placeholder(port_type: str, workdir: Path, step_id: str, name: str):
    e = T.elem(port_type)
    if e is not None:
        return [_placeholder(e, workdir, step_id, name)]
    if port_type in ("file", "image", "audio", "video", "path"):
        return str(workdir / f"<{step_id}.{name}>")
    if port_type == "directory":
        return str(workdir)
    if port_type == "number":
        return 0
    if port_type == "bool":
        return False
    return f"<{step_id}.{name}>"


def resolve_operand(value, values: dict):
    """Tek bir değeri çözer: referans, gömülü referanslı metin ya da düz değer."""
    ref = sole_ref(value)
    if ref:
        src, _, name = ref.partition(".")
        return values[src][name]
    if refs_in(value):
        def sub(m):
            src, _, name = m.group(1).partition(".")
            return str(values[src][name])
        return REF.sub(sub, value)
    return value


def resolve_args(step: Step, action: ActionDef, values: dict) -> dict:
    """{{ referans }} ifadelerini çözer ve port tipine göre dönüştürür."""
    out: dict = {}
    for key, raw in step.args.items():
        port = action.input(key)
        if sole_ref(raw) or refs_in(raw):
            out[key] = resolve_operand(raw, values)
            continue
        out[key] = T.coerce(raw, port.type)
    # varsayılanlar
    for key, port in action.inputs.items():
        if key not in out and port.default is not None:
            out[key] = T.coerce(port.default, port.type)
    return out


def _sequence(step: Step, values: dict) -> list:
    """Döngünün üstünden geçeceği liste."""
    if isinstance(step.each, list):
        seq = list(step.each)
    else:
        seq = resolve_operand(step.each, values)
    if isinstance(seq, (str, bytes)) or not hasattr(seq, "__iter__"):
        raise T.TypeError_(f"döngü kaynağı bir liste değil: {seq!r}")
    return list(seq)


def _collect(action: ActionDef, step_id: str, raw_out: dict) -> dict:
    """Eylemin döndürdüğünü beyan ettiği çıktı portlarına oturtur."""
    outs = {}
    for name, port in action.outputs.items():
        if name not in raw_out:
            raise StepFailed(step_id,
                             f"eylem beyan ettiği '{name}' çıktısını döndürmedi")
        outs[name] = T.coerce(raw_out[name], port.type)
    return outs


def _policy_for(step: Step, action: ActionDef, args: dict, seq,
                workdir: Path) -> "iso.Policy":
    """Adımın çekirdeğe onaylatılacak hak kümesi.

    Döngüde argümanlar her tur değişir, o yüzden ilkeye hem temsilî
    argümanlar hem de dizinin tamamı giriyor: kapsam bütün ögeleri
    örtmeli, yoksa üçüncü resimde patlar.
    """
    kaynak = dict(args)
    if seq is not None:
        kaynak["__dizi"] = list(seq)
    return iso.policy_for(action.permissions, kaynak, workdir)


def _isolate_note(step: Step, pol, applied: str) -> str:
    return f"yalıtıldı ({applied}): {pol.summary()}"


def _per_item_guard(step: Step) -> bool:
    """Koşul döngü ögesine bakıyor mu? Bakmıyorsa adımın tamamı için bir kez."""
    return any(r.partition(".")[0] == ITEM for r in C.refs(step.guard))


def _dead_dependency(step: Step, skipped: dict, failed: dict) -> str | None:
    """Adımın dayandığı bir değer hiç üretilmedi mi? Üretilmediyse gerekçe.

    Atlanan bir adımın çıktısı yoktur; ona bağlı adım da atlanmalıdır.
    Patlayıp yakalanan adımın da veri çıktıları yoktur -- ama 'tamam' ve
    'hata' portları vardır. İşte hata dalı bu ayrımdan doğuyor: hatayı
    *işleyen* adım çalışır, veriyi *tüketen* adım atlanır.
    """
    for ref in _all_refs(step):
        src, _, port = ref.partition(".")
        if src in skipped:
            return f"{src} atlandığı için"
        if src in failed and port not in CATCH_PORTS:
            return f"{src} hata verdiği için"
    return None


def _verify_trigger(flow: Flow, values: dict) -> str | None:
    """Beyan edilen tetikleyici tipini doğrular. Uymuyorsa akış atlanır."""
    declared = str(flow.trigger.get("produces") or "")
    path = values.get("file") or ""
    if not declared or declared in ("file", "path") or not path:
        return None
    if declared == "image":
        try:
            from PIL import Image, UnidentifiedImageError
            with Image.open(path) as im:
                im.verify()
        except (UnidentifiedImageError, OSError, ImportError) as e:
            return f"atlandı: {Path(path).name} resim olarak açılamadı ({type(e).__name__})"
    return None


@dataclass
class ItemResult:
    """Tek bir döngü ögesinin sonucu. Sıra korunabilsin diye indisli."""
    index: int
    status: str                 # tamam | koşul | hata
    outs: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    detail: str = ""


def _worker_count(step: Step, total: int) -> int:
    w = step.workers
    n = (os.cpu_count() or 1) if w == "auto" else int(w or 1)
    return max(1, min(n, max(total, 1)))


def _run_item(i: int, item, total: int, step: Step, action: ActionDef, fn,
              ctx: RunContext, values: dict, per_item_guard: bool,
              tagged: bool) -> ItemResult:
    """Bir ögeyi çalıştırır. Paylaşılan hiçbir şeye yazmaz.

    Döngü değişkeni paylaşılan values'a değil, ögeye özel bir kopyaya
    konuyor; kayıtlar da ögeye özel bir tampona. İkisi de paralel
    çalışmanın ön koşulu -- ve sıralı kipte de davranışı değiştirmiyor.
    """
    tag = f"{i}/{total}"
    local = {**values, ITEM: {"value": item, "index": i}}
    # Her ögenin kendi ara klasörü var. İki kaynak aynı adı taşıyorsa
    # (a/x.png ve b/x.png) ara dosyaları birbirini ezmiyor -- sıralı
    # kipte sessiz bir veri kaybıydı, paralelde yarışa dönerdi.
    wd = ctx.workdir / f"oge-{i}"
    wd.mkdir(parents=True, exist_ok=True)
    ictx = replace(ctx, lines=[], quiet=True, workdir=wd)

    if per_item_guard:
        try:
            verdict, why = C.evaluate(step.guard,
                                      lambda o: resolve_operand(o, local))
        except (KeyError, ValueError, TypeError) as e:
            return ItemResult(i, "hata", detail=f"koşul değerlendirilemedi: {e}")
        if not verdict:
            return ItemResult(i, "koşul", logs=[f"{tag} ⊘ {why}"])

    try:
        args = resolve_args(step, action, local)
        outs = _collect(action, step.id, fn(ictx, **args) or {})
    except PermissionDenied:
        raise
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        if os.environ.get("MOVO_DEBUG"):
            detail += "\n" + traceback.format_exc()
        return ItemResult(i, "hata", logs=list(ictx.lines), detail=detail)

    lines = [f"{tag} {ln}" for ln in ictx.lines] if tagged else list(ictx.lines)
    return ItemResult(i, "tamam", outs=outs, logs=lines)


def _run_loop(step: Step, action: ActionDef, reg: Registry, ctx: RunContext,
              values: dict, t0: float) -> StepResult:
    """Adımı listenin her ögesi için bir kez çalıştırır.

    Çıktılar toplanır: eylem T -> U ise döngülü adım list<T> -> list<U>
    olur ve akışın geri kalanı bunu tip sisteminden görür. Koşul ögeye
    bakıyorsa her turda yeniden değerlendirilir -- yani for_each + when
    birlikte "süz ve dönüştür" demek.

    ``workers`` birden büyükse ögeler iş parçacıklarıyla paralel koşar.
    Sonuç listeleri yine *girdi sırasını* korur: tamamlanma sırası
    değişse de kucult.image[i] ile bul.files[i] eşleşmeye devam eder.
    """
    def fail(msg: str) -> StepResult:
        return StepResult(step.id, step.action, "hata", time.perf_counter() - t0,
                          error=msg, logs=ctx.lines)

    try:
        seq = _sequence(step, values)
    except (KeyError, T.TypeError_, ValueError) as e:
        return fail(f"döngü kaynağı çözülemedi: {e}")
    try:
        fn = reg.callable_for(action)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}")

    total = len(seq)
    workers = _worker_count(step, total)
    per_item_guard = step.guard is not None and _per_item_guard(step)
    head = f"{total} öge"
    if workers > 1:
        head += f", {workers} paralel"
    if total:
        head += f", hata olursa: {step.on_error}"
    ctx.log(head)

    sonuc: dict[int, ItemResult] = {}
    ilk_hata: ItemResult | None = None

    def kaydet(r: ItemResult) -> bool:
        """Sonucu yazar; akışın durması gerekiyorsa False döner."""
        nonlocal ilk_hata
        sonuc[r.index] = r
        for ln in r.logs:
            ctx.log(ln)
        if r.status != "hata":
            return True
        if step.on_error == "atla":
            ctx.log(f"{r.index}/{total} ✗ {r.detail}")
            return True
        if ilk_hata is None or r.index < ilk_hata.index:
            ilk_hata = r
        return False

    if workers == 1:
        for i, item in enumerate(seq, 1):
            if not kaydet(_run_item(i, item, total, step, action, fn, ctx,
                                    values, per_item_guard, False)):
                break
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix=f"movo-{step.id}") as havuz:
            isler = {havuz.submit(_run_item, i, item, total, step, action, fn,
                                  ctx, values, per_item_guard, True): i
                     for i, item in enumerate(seq, 1)}
            try:
                from concurrent.futures import as_completed
                for fut in as_completed(isler):
                    if not kaydet(fut.result()):
                        # Sıraya girmiş ama başlamamış ögeleri iptal et;
                        # başlamış olanları durduramayız, biterler.
                        for f in isler:
                            f.cancel()
            except PermissionDenied:
                for f in isler:
                    f.cancel()
                raise

    if ilk_hata is not None:
        return fail(f"{ilk_hata.index}/{total}. ögede: {ilk_hata.detail}")

    collected: dict[str, list] = {n: [] for n in action.outputs}
    ran = filtered = broken = 0
    for i in sorted(sonuc):                  # girdi sırası korunur
        r = sonuc[i]
        if r.status == "koşul":
            filtered += 1
        elif r.status == "hata":
            broken += 1
        else:
            ran += 1
            for name, v in r.outs.items():
                collected[name].append(v)

    values[step.id] = collected
    note = f"{ran}/{total} öge çalıştı"
    if filtered:
        note += f", {filtered} koşula takıldı"
    if broken:
        note += f", {broken} hata atlandı"
    ctx.log(note)
    return StepResult(step.id, step.action, "tamam", time.perf_counter() - t0,
                      collected, logs=ctx.lines, reason=note)


def _loop_isolated(step: Step, action: ActionDef, reg: Registry, ctx: RunContext,
                   values: dict, t0: float, quiet: bool) -> StepResult:
    """Döngünün tamamını tek bir kısıtlanmış çocukta çalıştırır.

    Öge başına çatallanmak 500 resimde 500 fork demekti; ilke zaten adım
    başına olduğu için bir kez çatallamak hem doğru hem ucuz.
    """
    try:
        seq = _sequence(step, values)
    except (KeyError, T.TypeError_, ValueError) as e:
        return StepResult(step.id, step.action, "hata", time.perf_counter() - t0,
                          error=f"döngü kaynağı çözülemedi: {e}", logs=ctx.lines)
    pol = _policy_for(step, action, step.args, seq, ctx.workdir)

    def work():
        r = _run_loop(step, action, reg, ctx, values, t0)
        return {"status": r.status, "outputs": r.outputs, "logs": r.logs,
                "reason": r.reason, "error": r.error}

    try:
        res = iso.run_isolated(work, pol, ctx)
    except iso.IsolationUnavailable as e:
        ctx.log(f"yalıtım kurulamadı, korumasız çalışıyor: {e}")
        return _run_loop(step, action, reg, ctx, values, t0)

    logs = list(res.get("logs") or [])
    if not res.get("ok"):
        if res.get("perm"):
            raise PermissionDenied(res.get("err", ""))
        return StepResult(step.id, step.action, "hata", time.perf_counter() - t0,
                          error=res.get("err", "bilinmeyen hata"), logs=logs)

    payload = res.get("out") or {}
    logs = list(payload.get("logs") or logs)
    logs.append(_isolate_note(step, pol, res.get("applied", "")))
    if not quiet:
        for line in logs:
            print(f"    {line}", flush=True)
    ctx.lines.extend(logs)
    outs = payload.get("outputs") or {}
    if payload.get("status") == "tamam":
        values[step.id] = outs
    return StepResult(step.id, step.action, str(payload.get("status", "tamam")),
                      time.perf_counter() - t0, outs, logs=logs,
                      error=str(payload.get("error", "")),
                      reason=str(payload.get("reason", "")))


def run(flow: Flow, reg: Registry, trigger_values: dict | None = None,
        dry_run: bool = False, keep: bool = False, quiet: bool = False) -> RunResult:
    if not flow.ok:
        raise ValueError(f"'{flow.name}' akışı doğrulamadan geçmedi; önce 'movo kontrol' çalıştırın")

    cache_root().mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{flow.slug}-", dir=cache_root()))
    values: dict[str, dict] = {"trigger": dict(trigger_values or {})}
    for name in flow.trigger_outputs():
        values["trigger"].setdefault(name, "")

    # Tetikleyici 'produces: image' diyorsa sözünü tutuyor mu? Statik tip
    # denetimi burada çalışma anı kontrolüyle kapanır.
    guard = _verify_trigger(flow, values["trigger"])
    if guard:
        if not quiet:
            print(f"▶ {flow.name}\n  {guard}", flush=True)
        shutil.rmtree(workdir, ignore_errors=True)
        return RunResult(flow=flow.name, ok=True, steps=[
            StepResult(s.id, s.action, "atlandı", 0.0) for s in flow.steps],
            workdir=workdir, trigger=values["trigger"])

    results: list[StepResult] = []
    skipped: dict[str, str] = {}      # atlanan adım -> gerekçe
    failed: dict[str, str] = {}       # patlayıp yakalanan adım -> hata
    started = time.perf_counter()
    ok = True

    def skip(step: Step, reason: str, t0: float) -> None:
        skipped[step.id] = reason
        results.append(StepResult(step.id, step.action, "atlandı",
                                  time.perf_counter() - t0, reason=reason))
        if not quiet:
            print(f"    ⊘ atlandı: {reason}", flush=True)

    def catch(step: Step, detail: str, t0: float, logs: list) -> None:
        """Hatayı porta çevirir: adım düşer, akış ayakta kalır."""
        failed[step.id] = detail
        values[step.id] = {"tamam": False, "hata": detail}
        results.append(StepResult(step.id, step.action, "yakalandı",
                                  time.perf_counter() - t0,
                                  dict(values[step.id]), error=detail, logs=logs,
                                  reason=detail))
        if not quiet:
            print(f"    ⚡ hata yakalandı: {detail}", flush=True)

    if not quiet:
        head = f"▶ {flow.name}" + ("  [prova]" if dry_run else "")
        print(head, flush=True)
        if values["trigger"].get("file"):
            print(f"  tetikleyici: {values['trigger']['file']}", flush=True)

    for step in flow.steps:
        action = reg.get(step.action)
        t0 = time.perf_counter()
        if not quiet:
            mark = "⟳" if step.loops else ("⋔" if step.conditional else "·")
            print(f"  {mark} {step.id} ({action.name})", flush=True)

        # Dayandığı bir değer hiç üretilmediyse bu adım da çalışamaz.
        dead = _dead_dependency(step, skipped, failed)
        if dead:
            skip(step, dead, t0)
            continue

        # Koşul. Provada değerler sahte olduğu için karar verilmez:
        # yanlış bir "atlandı" raporu, hiç rapor etmemekten kötüdür.
        # Koşul ögeye bakıyorsa karar döngünün içinde, bakmıyorsa burada.
        if step.guard is not None and not (step.loops and _per_item_guard(step)):
            if not dry_run:
                try:
                    verdict, why = C.evaluate(
                        step.guard, lambda o: resolve_operand(o, values))
                except (KeyError, ValueError, TypeError) as e:
                    results.append(StepResult(
                        step.id, step.action, "hata", time.perf_counter() - t0,
                        error=f"koşul değerlendirilemedi: {e}"))
                    ok = False
                    break
                if not verdict:
                    skip(step, why, t0)
                    continue

        ctx = RunContext(workdir=workdir, step_id=step.id,
                         granted=frozenset(action.permissions), dry_run=dry_run, quiet=quiet)

        if step.loops and not dry_run:
            try:
                if step.isolated:
                    res = _loop_isolated(step, action, reg, ctx, values, t0, quiet)
                else:
                    res = _run_loop(step, action, reg, ctx, values, t0)
            except PermissionDenied as e:
                results.append(StepResult(
                    step.id, step.action, "hata", time.perf_counter() - t0,
                    error=f"izin reddedildi: {e}", logs=ctx.lines))
                ok = False
                break
            if res.status == "hata" and step.catches:
                catch(step, res.error, t0, res.logs)
                continue
            if res.status == "tamam" and step.catches:
                values[step.id].update({"tamam": True, "hata": ""})
                res.outputs.update({"tamam": True, "hata": ""})
            results.append(res)
            if res.status == "hata":
                ok = False
                break
            continue

        if step.loops:            # provada öge sahte, ama akış görünür olsun
            values[ITEM] = {
                "value": _placeholder(step.elem_type or "text", workdir, ITEM, "value"),
                "index": 1,
            }
        try:
            args = resolve_args(step, action, values)
        except (KeyError, T.TypeError_) as e:
            results.append(StepResult(step.id, step.action, "hata",
                                      time.perf_counter() - t0,
                                      error=f"girdi çözülemedi: {e}"))
            ok = False
            break

        if dry_run:
            outs = {n: _placeholder(T.listof(p.type) if step.loops else p.type,
                                    workdir, step.id, n)
                    for n, p in action.outputs.items()}
            if step.catches:
                outs.update({"tamam": True, "hata": ""})
            values[step.id] = outs
            if step.loops:
                src = step.each if isinstance(step.each, list) else str(step.each)
                ctx.log(f"her öge için: {src}"
                        + (f" ({T.label(step.elem_type)})" if step.elem_type else ""))
            if step.guard is not None:
                ctx.log(f"koşul (çalışma anında bakılacak): {C.describe(step.guard)}")
            shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
            ctx.log(f"çalıştırılacak: {shown}")
            if action.permissions:
                ctx.log(f"izinler: {', '.join(action.permissions)}")
            results.append(StepResult(step.id, step.action, "plan",
                                      time.perf_counter() - t0, outs, logs=ctx.lines))
            continue

        if step.guard is not None:
            ctx.log(f"koşul: {why}")
        try:
            fn = reg.callable_for(action)
            if step.isolated:
                pol = _policy_for(step, action, args, None, workdir)
                try:
                    res = iso.run_isolated(lambda: fn(ctx, **args), pol, ctx)
                except iso.IsolationUnavailable as e:
                    ctx.log(f"yalıtım kurulamadı, korumasız çalışıyor: {e}")
                    raw_out = fn(ctx, **args) or {}
                else:
                    ctx.lines.extend(res.get("logs") or [])
                    if not quiet:
                        for line in (res.get("logs") or []):
                            print(f"    {line}", flush=True)
                    if not res.get("ok"):
                        if res.get("perm"):
                            raise PermissionDenied(res.get("err", ""))
                        raise StepFailed(step.id, res.get("err", "bilinmeyen hata"))
                    ctx.log(_isolate_note(step, pol, res.get("applied", "")))
                    raw_out = res.get("out") or {}
            else:
                raw_out = fn(ctx, **args) or {}
            outs = _collect(action, step.id, raw_out)
            if step.catches:
                outs.update({"tamam": True, "hata": ""})
            values[step.id] = outs
            results.append(StepResult(step.id, step.action, "tamam",
                                      time.perf_counter() - t0, outs, logs=ctx.lines))
        except PermissionDenied as e:
            # Sözleşme ihlali yakalanmaz: 'devam' veri hataları içindir.
            results.append(StepResult(step.id, step.action, "hata",
                                      time.perf_counter() - t0,
                                      error=f"izin reddedildi: {e}", logs=ctx.lines))
            ok = False
            break
        except Exception as e:  # eylemler güvenilmez kod; akışı kirli bırakmadan dur
            detail = f"{type(e).__name__}: {e}"
            if os.environ.get("MOVO_DEBUG"):
                detail += "\n" + traceback.format_exc()
            if step.catches:
                catch(step, detail, t0, ctx.lines)
                continue
            results.append(StepResult(step.id, step.action, "hata",
                                      time.perf_counter() - t0, error=detail, logs=ctx.lines))
            ok = False
            break

    values.pop(ITEM, None)
    for step in flow.steps[len(results):]:
        results.append(StepResult(step.id, step.action, "atlandı", 0.0))

    result = RunResult(flow=flow.name, ok=ok, steps=results, workdir=workdir,
                       trigger=values["trigger"], duration=time.perf_counter() - started)

    if keep or not ok:
        if not quiet:
            print(f"  ara dosyalar: {workdir}", flush=True)
    else:
        shutil.rmtree(workdir, ignore_errors=True)

    if not quiet:
        bad = [s for s in results if s.status == "hata"]
        if bad:
            print(f"✗ {bad[0].id}: {bad[0].error}", file=sys.stderr, flush=True)
        print(("✓ " if ok else "✗ ") + result.summary(), flush=True)
    return result
