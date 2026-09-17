"""测试全局夹具（41）：所有测试共享同一份临时统计存储。

任何测试都不允许读写真实的 backend/data/opponents.json——
此前 record 相关测试在无隔离时会把档案写进真实数据文件。
"""

import pytest

from backend import opponents


@pytest.fixture(autouse=True)
def isolated_stats(tmp_path, monkeypatch):
    target = tmp_path / "opponents.json"
    monkeypatch.setattr(opponents, "_DATA", target)
    target.write_text("{}", encoding="utf-8")
    return target
