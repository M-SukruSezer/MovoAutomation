"""Eylem kaydı: arama yolundaki manifestleri toplar."""

from __future__ import annotations

import importlib
from pathlib import Path

from . import dbus as dbuslib
from .manifest import ActionDef, ManifestError, load_file, search_path


class Registry:
    def __init__(self, actions: dict[str, ActionDef], problems: list[str]):
        self.actions = actions
        self.problems = problems

    @classmethod
    def discover(cls) -> "Registry":
        found: dict[str, ActionDef] = {}
        problems: list[str] = []
        for d in search_path():
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.action.yaml")):
                try:
                    a = load_file(f)
                except ManifestError as e:
                    problems.append(str(e))
                    continue
                found.setdefault(a.id, a)  # arama yolunda ilk gelen kazanır
        return cls(found, problems)

    def get(self, action_id: str) -> ActionDef:
        if action_id not in self.actions:
            near = [a for a in self.actions if action_id.split(".")[0] == a.split(".")[0]]
            hint = f" Aynı kategoride: {', '.join(sorted(near))}" if near else ""
            raise ManifestError(f"'{action_id}' diye bir eylem kayıtlı değil.{hint}")
        return self.actions[action_id]

    def __contains__(self, action_id: str) -> bool:
        return action_id in self.actions

    def by_category(self) -> dict[str, list[ActionDef]]:
        out: dict[str, list[ActionDef]] = {}
        for a in self.actions.values():
            out.setdefault(a.category, []).append(a)
        for v in out.values():
            v.sort(key=lambda a: a.id)
        return dict(sorted(out.items()))

    def callable_for(self, action: ActionDef):
        """Eylemin gövdesini çağrılabilir hâle getirir.

        dbus eylemlerinin gövdesi yoktur: manifestteki adresi çalıştıran
        kapanış döndürülür. Motor ikisini ayırt etmez.
        """
        if action.impl_kind == "dbus":
            call = action.impl

            def _run(ctx, **inputs):
                return dbuslib.invoke(ctx, call, inputs)

            return _run
        if action.impl_kind == "command":
            raise ManifestError(
                f"{action.id}: 'command' gövdeleri henüz motora bağlanmadı; "
                f"şimdilik python gövdesi ya da shell.run kullanın")
        mod_name, _, func_name = str(action.impl).partition(":")
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            raise ManifestError(f"{action.id}: '{mod_name}' modülü yüklenemedi: {e}") from e
        fn = getattr(mod, func_name, None)
        if fn is None:
            raise ManifestError(f"{action.id}: '{mod_name}' içinde '{func_name}' yok")
        return fn


def bundled_dir() -> Path:
    return Path(__file__).parent / "actions"
