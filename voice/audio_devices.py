"""
PyAudio input device listing. Use this to pick the correct AUDIO_INPUT_INDEX in core/config.py.

Run: python -m voice.audio_devices
"""
from __future__ import annotations

import sys
from typing import Any, Optional


def resolve_input_device_index(idx: Optional[int]) -> Optional[int]:
    """None or negative means: let PyAudio choose the default input device."""
    if idx is None or idx < 0:
        return None
    return idx


def list_input_devices() -> None:
    try:
        import pyaudio
    except ImportError:
        print("Install pyaudio first.", file=sys.stderr)
        return

    p = pyaudio.PyAudio()
    try:
        print("Input-capable devices (index | name | default sample rate):\n")
        for i in range(p.get_device_count()):
            info: Any = p.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) < 1:
                continue
            dr = info.get("defaultSampleRate", "?")
            name = info.get("name", "?")
            print(f"  [{i}] {name}  (default SR: {dr})")
        try:
            def_i = p.get_default_input_device_info()["index"]
            print(f"\nDefault input device index: {def_i}")
        except Exception:
            pass
    finally:
        p.terminate()


if __name__ == "__main__":
    list_input_devices()
