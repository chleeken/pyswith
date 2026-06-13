"""把 Codex CLI 配置重置为干净初始状态。

- 备份当前 config.toml / config.json / auth.json 到 .codex/ccswitch-backup/
- 用 Codex 官方默认值重写 config.toml / config.json
- 不碰 auth.json（含登录凭据）

运行：python reset_codex.py
"""
from __future__ import annotations

from pathlib import Path
import datetime
import shutil
import os


def main() -> int:
    codex = Path(os.environ.get("USERPROFILE") or Path.home()) / ".codex"
    if not codex.exists():
        print(f"[!] 目录不存在：{codex}")
        return 1

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bk = codex / "ccswitch-backup"
    bk.mkdir(exist_ok=True)

    for name in ("config.toml", "config.json", "auth.json"):
        src = codex / name
        if src.exists():
            shutil.copy2(src, bk / f"{name}.{ts}.bak")
            print(f"  备份 {name} -> {bk / name}.{ts}.bak")

    # 干净的 Codex 默认值（Codex 0.116+ 原生默认）
    (codex / "config.toml").write_text(
        'model_provider = "codex"\n'
        'model = "gpt-5.2"\n',
        encoding="utf-8",
    )
    # Codex sandbox_mode 合法值：read-only / workspace-write / danger-full-access
    # 不写则走 Codex 自己默认，避免版本差异
    _ = (codex / "config.toml").read_text(encoding="utf-8")
    (codex / "config.json").write_text(
        '{\n'
        '  "apiKey": "",\n'
        '  "baseURL": "",\n'
        '  "model": "gpt-5.2"\n'
        '}\n',
        encoding="utf-8",
    )

    print("[OK] Codex 配置已重置为干净初始状态")
    print("     下次打开 Codex 会提示你登录/选择模型")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
