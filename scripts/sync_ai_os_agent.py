#!/usr/bin/env python3
"""One-off: copy Operonix root packages into ai_os_agent/ and rewrite imports."""
from __future__ import annotations

import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST_ROOT = os.path.join(ROOT, "ai_os_agent")
PACKAGES = ("core", "brain", "capabilities", "context", "executor", "tools", "plugins", "learning", "api")

SUBS = [
    (r"^from core\.", "from ai_os_agent.core."),
    (r"^from brain\.", "from ai_os_agent.brain."),
    (r"^from capabilities\.", "from ai_os_agent.capabilities."),
    (r"^from context\.", "from ai_os_agent.context."),
    (r"^from executor\.", "from ai_os_agent.executor."),
    (r"^from tools\.", "from ai_os_agent.tools."),
    (r"^from plugins\.", "from ai_os_agent.plugins."),
    (r"^from api\.", "from ai_os_agent.api."),
    (r"^from learning\.", "from ai_os_agent.learning."),
]


def transform(text: str) -> str:
    out = text
    for pat, rep in SUBS:
        out = re.sub(pat, rep, out, flags=re.MULTILINE)
    return out


def ensure_init(dirpath: str):
    init = os.path.join(dirpath, "__init__.py")
    if not os.path.isfile(init):
        with open(init, "w", encoding="utf-8") as f:
            f.write('"""Package."""\n')


def copy_tree(pkg: str):
    src = os.path.join(ROOT, pkg)
    if not os.path.isdir(src):
        return
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dst_dir = os.path.join(DST_ROOT, pkg, rel if rel != "." else "")
        os.makedirs(dst_dir, exist_ok=True)
        ensure_init(dst_dir)
        for name in files:
            if not name.endswith(".py"):
                continue
            sp = os.path.join(root, name)
            dp = os.path.join(dst_dir, name)
            with open(sp, "r", encoding="utf-8") as f:
                content = f.read()
            with open(dp, "w", encoding="utf-8") as f:
                f.write(transform(content))


def main():
    os.makedirs(DST_ROOT, exist_ok=True)
    ensure_init(DST_ROOT)
    for pkg in PACKAGES:
        copy_tree(pkg)


if __name__ == "__main__":
    main()
