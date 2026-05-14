"""Global hotkey parsing and listening via pynput."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable

from pynput import keyboard


MODIFIER_ALIASES = {
    "ctrl": keyboard.Key.ctrl,
    "control": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt,
    "shift": keyboard.Key.shift,
    "super": keyboard.Key.cmd,
    "meta": keyboard.Key.cmd,
    "win": keyboard.Key.cmd,
}

SPECIAL_KEYS = {
    "space": keyboard.Key.space,
    "enter": keyboard.Key.enter,
    "tab": keyboard.Key.tab,
    "esc": keyboard.Key.esc,
    "escape": keyboard.Key.esc,
    "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "insert": keyboard.Key.insert,
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "pageup": keyboard.Key.page_up,
    "pagedown": keyboard.Key.page_down,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "f1": keyboard.Key.f1,
    "f2": keyboard.Key.f2,
    "f3": keyboard.Key.f3,
    "f4": keyboard.Key.f4,
    "f5": keyboard.Key.f5,
    "f6": keyboard.Key.f6,
    "f7": keyboard.Key.f7,
    "f8": keyboard.Key.f8,
    "f9": keyboard.Key.f9,
    "f10": keyboard.Key.f10,
    "f11": keyboard.Key.f11,
    "f12": keyboard.Key.f12,
}


@dataclass
class HotkeyBinding:
    modifiers: frozenset[keyboard.Key] = field(default_factory=frozenset)
    key: keyboard.Key | keyboard.KeyCode | None = None

    @classmethod
    def parse(cls, hotkey: str) -> "HotkeyBinding":
        parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
        if not parts:
            raise ValueError("Atalho vazio")

        modifiers: set[keyboard.Key] = set()
        main_key: keyboard.Key | keyboard.KeyCode | None = None

        for part in parts:
            if part in MODIFIER_ALIASES:
                modifiers.add(MODIFIER_ALIASES[part])
                continue
            if part in SPECIAL_KEYS:
                if main_key is not None:
                    raise ValueError(f"Tecla duplicada no atalho: {hotkey}")
                main_key = SPECIAL_KEYS[part]
                continue
            if len(part) == 1:
                if main_key is not None:
                    raise ValueError(f"Tecla duplicada no atalho: {hotkey}")
                vk = _resolve_vk_for_char(part)
                main_key = (
                    keyboard.KeyCode.from_char(part, vk=vk)
                    if vk is not None
                    else keyboard.KeyCode.from_char(part)
                )
                continue
            raise ValueError(f"Tecla desconhecida no atalho: {part}")

        if main_key is None:
            raise ValueError("O atalho precisa de uma tecla além dos modificadores")

        return cls(modifiers=frozenset(modifiers), key=main_key)

    def format(self) -> str:
        names = []
        for name, key in MODIFIER_ALIASES.items():
            if key in self.modifiers and name == "ctrl":
                names.append("ctrl")
                break
        if keyboard.Key.alt in self.modifiers:
            names.append("alt")
        if keyboard.Key.shift in self.modifiers:
            names.append("shift")
        if keyboard.Key.cmd in self.modifiers:
            names.append("super")

        if isinstance(self.key, keyboard.KeyCode) and self.key.char:
            names.append(self.key.char.lower())
        else:
            for name, key in SPECIAL_KEYS.items():
                if key == self.key:
                    names.append(name)
                    break

        return "+".join(names)


def _resolve_vk_for_char(char: str) -> int | None:
    """Map a character to the platform virtual key / keysym when possible."""
    try:
        from pynput.keyboard._xorg import SYMBOLS

        lower = char.lower()
        fallback: int | None = None
        for _name, (vk, sym_char) in SYMBOLS.items():
            if not sym_char:
                continue
            if sym_char == char:
                return vk
            if sym_char.lower() == lower and fallback is None:
                fallback = vk
        return fallback
    except ImportError:
        pass
    return None


def _key_codes_match(
    expected: keyboard.KeyCode,
    actual: keyboard.KeyCode,
) -> bool:
    if expected == actual:
        return True
    if expected.char and actual.char:
        return expected.char.lower() == actual.char.lower()
    if expected.vk is not None and actual.vk is not None:
        return expected.vk == actual.vk
    if expected.char and actual.vk is not None:
        expected_vk = expected.vk or _resolve_vk_for_char(expected.char)
        return expected_vk is not None and expected_vk == actual.vk
    if actual.char and expected.vk is not None:
        actual_vk = actual.vk or _resolve_vk_for_char(actual.char)
        return actual_vk is not None and expected.vk == actual_vk
    return False


def _normalize_key(key: keyboard.Key | keyboard.KeyCode | None) -> keyboard.Key | str | None:
    if key is None:
        return None
    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char.lower()
    return key


class GlobalHotkeyListener:
    """Listen for a global hotkey and invoke a callback (toggle on each match)."""

    def __init__(self, hotkey: str, on_activate: Callable[[], None]):
        self._binding = HotkeyBinding.parse(hotkey)
        self._on_activate = on_activate
        self._pressed_modifiers: set[keyboard.Key] = set()
        self._main_key_down = False
        self._fired = False
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    @property
    def hotkey_display(self) -> str:
        return self._binding.format()

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _modifiers_match(self) -> bool:
        return self._pressed_modifiers == set(self._binding.modifiers)

    def _main_key_matches(self, key: keyboard.Key | keyboard.KeyCode | None) -> bool:
        expected = self._binding.key
        if key is None or expected is None:
            return False
        if expected == key:
            return True
        if isinstance(expected, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode):
            return _key_codes_match(expected, key)
        return _normalize_key(expected) == _normalize_key(key)

    def _track_modifier_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in MODIFIER_ALIASES.values():
            self._pressed_modifiers.add(key)
        elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._pressed_modifiers.add(keyboard.Key.ctrl)
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self._pressed_modifiers.add(keyboard.Key.alt)
        elif key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._pressed_modifiers.add(keyboard.Key.shift)
        elif key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self._pressed_modifiers.add(keyboard.Key.cmd)

    def _track_modifier_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in MODIFIER_ALIASES.values():
            self._pressed_modifiers.discard(key)
        elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._pressed_modifiers.discard(keyboard.Key.ctrl)
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self._pressed_modifiers.discard(keyboard.Key.alt)
        elif key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._pressed_modifiers.discard(keyboard.Key.shift)
        elif key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self._pressed_modifiers.discard(keyboard.Key.cmd)

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self._lock:
            self._track_modifier_press(key)
            if self._main_key_matches(key):
                self._main_key_down = True
            if (
                self._main_key_down
                and self._modifiers_match()
                and not self._fired
            ):
                self._fired = True
                self._on_activate()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self._lock:
            self._track_modifier_release(key)
            if self._main_key_matches(key):
                self._main_key_down = False
                self._fired = False


def format_hotkey_examples() -> Iterable[str]:
    return ("ctrl+shift+l", "ctrl+alt+space", "super+r")
