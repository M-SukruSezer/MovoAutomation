"""movo — Linux için otomasyon motoru.

Katmanlar aşağıdan yukarı: tip sistemi (:mod:`movo.types`), eylem
manifestleri (:mod:`movo.manifest`), akış modeli (:mod:`movo.flow`),
çalışma zamanı (:mod:`movo.runner`), tetikleyiciler (:mod:`movo.watch`,
:mod:`movo.listen`), systemd derlemesi (:mod:`movo.units`) ve en üstte
görsel editör (:mod:`movo.gui`).

Çekirdek yalnızca PyYAML ister. Resim eylemleri Pillow, masaüstü
eylemleri ve editör PyGObject gerektirir; ikisi de isteğe bağlıdır ve
eksikliği yalnızca ilgili eylemi etkiler.
"""

__version__ = "0.1.0"

APP_ID = "io.github.movo.MovoAutomation"
APP_NAME = "Movo Automation"
DEVELOPER = "Muhammed Şükrü SEZER"
COPYRIGHT = "© 2026 Muhammed Şükrü SEZER"
PROJECT_URL = "https://github.com/M-SukruSezer/MovoAutomation"
ISSUE_URL = f"{PROJECT_URL}/issues"

__all__ = ["__version__", "APP_ID", "APP_NAME", "DEVELOPER", "COPYRIGHT",
           "PROJECT_URL", "ISSUE_URL"]
