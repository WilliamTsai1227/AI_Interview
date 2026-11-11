"""滾動更新時 migration 先跑完，舊版 Pod 還會用新的 schema 跑一陣子，所以 migration 只能「擴充」：
加表、加欄位（可為 NULL 或有預設值）、加索引。會讓舊版程式壞掉的變更要拆成兩次部署：
先上一版不再使用那個欄位的程式，下一次部署才刪。這種 migration 要寫一行 `-- deploy-safe: 原因`。
"""
import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
BREAKING = re.compile(r"\b(DROP\s+(COLUMN|TABLE)|RENAME\s+(COLUMN|TO)|SET\s+NOT\s+NULL|ALTER\s+COLUMN\s+\w+\s+(SET\s+DATA\s+)?TYPE)\b", re.I)
# 這條規則之前就已經上線的（0013 當時與移除 role 的程式同一版上線）
GRANDFATHERED = {"0013_drop_user_role.sql"}


def test_migrations_are_backward_compatible():
    bad = []
    for f in sorted(MIGRATIONS.glob("*.sql")):
        sql = f.read_text()
        code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        if BREAKING.search(code) and "-- deploy-safe:" not in sql and f.name not in GRANDFATHERED:
            bad.append(f.name)
    assert not bad, f"這些 migration 會讓滾動更新中的舊版 Pod 出錯，請拆成兩次部署並加上 `-- deploy-safe: 原因`：{bad}"
