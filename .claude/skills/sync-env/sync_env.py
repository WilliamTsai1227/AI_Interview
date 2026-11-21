"""把 backend/.env 對齊 backend/.env.example：照範本的順序與註解重寫，缺的設定用範本的預設值補上。

- .env 裡原本有的值一律保留（包含範本裡被註解掉、但 .env 有設定的）。
- .env 有、範本沒有的設定，放到檔案最後「只在本機的設定」。
- 不印出任何設定值（.env 有金鑰），只列出設定名稱。

用法：python3 .claude/skills/sync-env/sync_env.py [--check]
  --check：只檢查不寫入；有缺的設定時結束碼為 1。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "backend" / ".env.example"
ENV = ROOT / "backend" / ".env"
LINE = re.compile(r"^(\s*#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")


def parse(path: Path) -> dict[str, str]:
    """只收沒被註解的 KEY=value，回傳 {KEY: 原始那一行}。"""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m and not m.group(1):
            out[m.group(2)] = line.strip()
    return out


def main() -> int:
    check = "--check" in sys.argv
    env = parse(ENV) if ENV.exists() else {}
    added, kept = [], set()
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if not m:
            lines.append(line)
            continue
        key, commented = m.group(2), bool(m.group(1))
        if key in env:
            lines.append(env[key])
            kept.add(key)
        else:
            lines.append(line)
            if not commented:
                added.append(key)
    extra = [k for k in env if k not in kept]
    if extra:
        lines += ["", "# 只在本機的設定（.env.example 沒有）"] + [env[k] for k in extra]

    print(f".env 已有 {len(kept)} 個設定（值保留）")
    print("補上（用範本預設值）：", ", ".join(added) or "無")
    if extra:
        print("只在本機的設定（放到最後）：", ", ".join(extra))
    if check:
        return 1 if added else 0

    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    after = parse(ENV)
    lost = [k for k, v in env.items() if after.get(k) != v]
    if lost:
        print("錯誤：這些設定的值沒有保留下來：", ", ".join(lost))
        return 2
    print(f"已寫入 {ENV.relative_to(ROOT)}，原本的值都保留了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
