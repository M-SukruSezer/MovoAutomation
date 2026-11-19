"""Yerleşik eylemlerin gövdeleri.

Her fonksiyon ``(ctx, **girdiler) -> dict`` imzasına sahiptir ve manifestinde
beyan ettiği çıktıları döndürür. Kurallar:
  * dosya üreten eylemler çıktıyı ``ctx.workdir`` içine yazar; akışın son
    adımı onu kalıcı bir yere taşır. Böylece yarı yolda kalan bir akış
    kullanıcının klasörlerini kirletmez.
  * izin gerektiren her iş ``ctx.require(...)`` ile kapıdan geçer.
"""

from __future__ import annotations

import datetime as _dt
import os
import shlex
import shutil
import subprocess
from pathlib import Path

_IMAGE_SAVE = {
    "webp": ("WEBP", ".webp"),
    "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "avif": ("AVIF", ".avif"),
}


def _tokens(src: Path, extra: dict | None = None) -> dict:
    now = _dt.datetime.now()
    d = {
        "name": src.stem,
        "ext": src.suffix.lstrip("."),
        "parent": src.parent.name,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H%M%S"),
        "stamp": now.strftime("%Y%m%d-%H%M%S"),
    }
    d.update(extra or {})
    return d


def _apply_pattern(pattern: str, src: Path, extra: dict | None = None) -> str:
    try:
        return pattern.format(**_tokens(src, extra))
    except KeyError as e:
        raise ValueError(
            f"desende bilinmeyen alan {e}; kullanılabilir: "
            f"{', '.join('{' + k + '}' for k in _tokens(src, extra))}"
        ) from e


def _unique(path: Path) -> Path:
    """Boş bir yer bulur ve adı *atomik olarak* sahiplenir.

    Eski hâli "var mı? yoksa kullan" diyordu; paralel döngüde iki öge
    aynı adı aynı anda boş görebiliyordu. O_CREAT|O_EXCL ile adı
    çekirdek düzeyinde kapatıyoruz, sonra üzerine yazılıyor.
    """
    i = 1
    while True:
        cand = path if i == 1 else path.with_name(f"{path.stem}-{i}{path.suffix}")
        try:
            fd = os.open(cand, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            i += 1
            continue
        except OSError:
            return cand            # izin/benzeri sorun: çağırana bırak
        os.close(fd)
        return cand


# --------------------------------------------------------------------- resim

def image_resize(ctx, image, max_width=0, max_height=0, allow_upscale=False):
    from PIL import Image

    ctx.require("fs.read", "fs.write")
    src = Path(image).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"resim bulunamadı: {src}")
    with Image.open(src) as im:
        im.load()
        w, h = im.size
        mw = int(max_width) or 0
        mh = int(max_height) or 0
        scale = 1.0
        if mw:
            scale = min(scale, mw / w)
        if mh:
            scale = min(scale, mh / h)
        if not allow_upscale:
            scale = min(scale, 1.0)
        if scale == 1.0:
            out = ctx.workdir / src.name
            shutil.copy2(src, out)
            ctx.log(f"boyut zaten uygun ({w}x{h}), olduğu gibi geçti")
            return {"image": str(out), "width": w, "height": h}
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        out = ctx.workdir / src.name
        resized = im.resize((nw, nh), Image.LANCZOS)
        if resized.mode in ("P", "CMYK"):
            resized = resized.convert("RGB")
        resized.save(out)
    ctx.log(f"{w}x{h} -> {nw}x{nh}")
    return {"image": str(out), "width": nw, "height": nh}


def image_convert(ctx, image, format="webp", quality=82, strip_metadata=True):
    from PIL import Image

    ctx.require("fs.read", "fs.write")
    fmt = str(format).lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in _IMAGE_SAVE:
        raise ValueError(f"desteklenmeyen biçim '{format}' ({', '.join(_IMAGE_SAVE)})")
    pil_fmt, ext = _IMAGE_SAVE[fmt]
    src = Path(image).expanduser()
    out = ctx.workdir / (src.stem + ext)
    with Image.open(src) as im:
        im.load()
        if fmt in ("jpeg", "avif") and im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        params = {"quality": int(quality)}
        if fmt == "webp":
            params["method"] = 6
        if not strip_metadata and im.info.get("exif"):
            params["exif"] = im.info["exif"]
        try:
            im.save(out, pil_fmt, **params)
        except (OSError, KeyError) as e:
            raise RuntimeError(
                f"{fmt} yazılamadı ({e}). Pillow bu biçim için destekle "
                f"derlenmemiş olabilir."
            ) from e
    before = src.stat().st_size
    after = out.stat().st_size
    ctx.log(f"{fmt} -> {after/1024:.0f} KB (önce {before/1024:.0f} KB)")
    return {"image": str(out), "size": after}


# --------------------------------------------------------------------- dosya

def file_move(ctx, file, to_dir, pattern="{name}.{ext}", overwrite=False):
    ctx.require("fs.read", "fs.write")
    src = Path(file).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"dosya bulunamadı: {src}")
    dest_dir = Path(to_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / _apply_pattern(pattern, src)
    if not overwrite:
        target = _unique(target)
    shutil.move(str(src), str(target))
    ctx.log(f"-> {target}")
    return {"file": str(target), "directory": str(dest_dir)}


def file_copy(ctx, file, to_dir, pattern="{name}.{ext}", overwrite=False):
    ctx.require("fs.read", "fs.write")
    src = Path(file).expanduser()
    dest_dir = Path(to_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / _apply_pattern(pattern, src)
    if target.exists() and not overwrite:
        target = _unique(target)
    shutil.copy2(src, target)
    ctx.log(f"-> {target}")
    return {"file": str(target), "directory": str(dest_dir)}


def file_rename(ctx, file, pattern):
    ctx.require("fs.read", "fs.write")
    src = Path(file).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"dosya bulunamadı: {src}")
    target = _unique(src.with_name(_apply_pattern(pattern, src)))
    src.rename(target)
    ctx.log(f"{src.name} -> {target.name}")
    return {"file": str(target)}


# ------------------------------------------------------------------ masaüstü

def ui_notify(ctx, title, body="", urgency="normal"):
    ctx.require("notify")
    exe = shutil.which("notify-send")
    if not exe:
        ctx.log(f"notify-send yok, bildirim atlandı: {title}")
        return {"shown": False}
    subprocess.run(
        [exe, "-a", "Akış", "-u", str(urgency), str(title), str(body)],
        check=False,
    )
    return {"shown": True}


# ----------------------------------------------------------------- ağ / ekran

def net_connection(ctx, name, state="down"):
    ctx.require("net", "exec")
    exe = shutil.which("nmcli")
    if not exe:
        raise RuntimeError("nmcli bulunamadı")
    verb = "up" if str(state) == "up" else "down"
    r = subprocess.run(
        [exe, "connection", verb, str(name)],
        capture_output=True, text=True, check=False,
    )
    ok = r.returncode == 0
    ctx.log(f"nmcli connection {verb} {name}: {'tamam' if ok else r.stderr.strip()}")
    return {"ok": ok, "message": (r.stdout or r.stderr).strip()}


def display_brightness(ctx, percent):
    ctx.require("display")
    pct = max(1, min(100, int(percent)))
    ctl = shutil.which("brightnessctl")
    if ctl:
        r = subprocess.run([ctl, "set", f"{pct}%"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            ctx.log(f"parlaklık %{pct}")
            return {"percent": pct}
    base = Path("/sys/class/backlight")
    devs = sorted(base.iterdir()) if base.is_dir() else []
    if not devs:
        raise RuntimeError(
            "parlaklık arayüzü yok (brightnessctl kurulu değil ve "
            "/sys/class/backlight boş -- masaüstü makinede beklenen durum)"
        )
    dev = devs[0]
    mx = int((dev / "max_brightness").read_text())
    try:
        (dev / "brightness").write_text(str(round(mx * pct / 100)))
    except PermissionError as e:
        raise RuntimeError(
            f"{dev/'brightness'} yazılamadı; brightnessctl kurmak gerekir "
            f"(doğru çözüm: polkit üzerinden yetki, root değil)"
        ) from e
    ctx.log(f"parlaklık %{pct} ({dev.name})")
    return {"percent": pct}


# ------------------------------------------------------------- kaçış kapısı

def shell_run(ctx, command, workdir="", timeout=60, check=False):
    """Kasıtlı kaçış kapısı: motorun kapsamadığı her şey buradan geçer.

    Görsel editörde bu blok 'uzman' etiketiyle ve izin uyarısıyla gösterilir.

    Varsayılan olarak sıfır olmayan çıkış *hata değildir*: kodu bir port
    olarak döner, koşulla test edilir. ``check: açık`` dersen patlar --
    hata dalına ancak o zaman girer.
    """
    ctx.require("exec")
    cwd = Path(workdir).expanduser() if workdir else ctx.workdir
    r = subprocess.run(
        ["/bin/sh", "-c", str(command)],
        capture_output=True, text=True, cwd=cwd, timeout=float(timeout), check=False,
    )
    ctx.log(f"$ {shlex.join(['sh','-c',str(command)])} -> çıkış {r.returncode}")
    err = r.stderr.strip()
    if err:
        ctx.log(err.splitlines()[0])
    if check and r.returncode != 0:
        raise RuntimeError(
            f"komut {r.returncode} ile çıktı"
            + (f": {err.splitlines()[0]}" if err else ""))
    return {"stdout": r.stdout.strip(), "exit_code": r.returncode}


os.environ.setdefault("MOVO_BUILTIN", "1")


def file_exists(ctx, path):
    """Koşulların en çok ihtiyaç duyduğu şey: bir dosya orada mı?

    Dosya sistemine bakmak izin isteyen bir iş, o yüzden koşul diline
    gömülmedi; eylem olarak durur ve 'fs.read' ister.
    """
    ctx.require("fs.read")
    p = Path(str(path)).expanduser()
    exists = p.exists()
    size = p.stat().st_size if p.is_file() else 0
    ctx.log(f"{p}: {'var' if exists else 'yok'}" + (f", {size} bayt" if size else ""))
    return {"exists": exists, "size": size, "path": str(p)}


# ------------------------------------------------------------------- listeler

#: 'produces' daraltmasının uzantı karşılığı. Daraltma bir iddia, ama
#: iddianın bedava kısmını burada doğruluyoruz: resim istendiyse .txt
#: dosyası listeye hiç girmesin.
KIND_EXT = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"},
    "audio": {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac"},
    "video": {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"},
}


def file_glob(ctx, dir, pattern="*", recursive=False, sort="name",
              newest_first=False, produces="file"):
    """Döngülerin başlangıç noktası: bir klasörü listeye çevirir.

    Yalnızca dosyalar döner, klasörler değil -- çıktının tipi list<file>
    olduğuna göre içinde klasör bulunmamalı. ``produces`` çıktının tipini
    daraltır (akış tarafı) ve uzantıya göre süzer (burası).
    """
    from .. import watch as _watch

    ctx.require("fs.read")
    root = Path(str(dir)).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"klasör değil: {root}")
    pats = _watch.expand_braces(str(pattern)) or ["*"]
    found: set[Path] = set()
    for pat in pats:
        it = root.rglob(pat) if recursive else root.glob(pat)
        found |= {p for p in it if p.is_file()}
    keys = {
        "name": lambda p: p.name.lower(),
        "time": lambda p: p.stat().st_mtime,
        "size": lambda p: p.stat().st_size,
    }
    if str(sort) not in keys:
        raise ValueError(f"bilinmeyen sıralama '{sort}' (name, time, size)")
    exts = KIND_EXT.get(str(produces))
    if exts:
        before = len(found)
        found = {p for p in found if p.suffix.lower() in exts}
        if before != len(found):
            ctx.log(f"{before - len(found)} dosya '{produces}' uzantısı olmadığı "
                    f"için elendi")
    items = sorted(found, key=keys[str(sort)], reverse=bool(newest_first))
    ctx.log(f"{root}: {len(items)} dosya ({pattern})")
    return {"files": [str(p) for p in items], "count": len(items)}


def text_split(ctx, text, separator="\n", trim=True, drop_empty=True):
    sep = str(separator)
    parts = str(text).split(sep) if sep else list(str(text))
    if trim:
        parts = [p.strip() for p in parts]
    if drop_empty:
        parts = [p for p in parts if p]
    ctx.log(f"{len(parts)} parça")
    return {"parts": parts, "count": len(parts)}


def text_join(ctx, parts, separator=", ", limit=0, more="…"):
    items = [str(p) for p in (parts or [])]
    n = int(limit or 0)
    shown = items[:n] if n > 0 else items
    out = str(separator).join(shown)
    if n > 0 and len(items) > n:
        out += str(separator) + str(more).format(n=len(items) - n)
    return {"text": out, "count": len(items)}
