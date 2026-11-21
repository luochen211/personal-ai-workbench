"""「健康」标签：只读助理目录的 资料/raw/health/zepp-daily.csv。全部用临时目录和编的数据。"""
import os
from datetime import datetime, timedelta, timezone

from personal import health
from personal_tests.test_workspace import instance

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 3, 9, 13, 0, tzinfo=CST)
HEAD = "date,sleep_start,sleep_end,sleep_min,deep_min,light_min,rem_min,awake_min,hr_avg,hr_resting,hr_resting_source,steps"
ROWS = [
    "2026-03-02,,,,,,,,75,62,p10,30",  # 开始戴表那天：没有睡眠，步数不是整天的
    "2026-03-03,2026-03-02 23:30,2026-03-03 07:40,480,100,300,80,10,64,52,p10,6000",
    "2026-03-04,2026-03-03 23:00,2026-03-04 07:10,490,100,310,80,0,62,50,p10,8000",
    "2026-03-05,2026-03-04 23:40,2026-03-05 07:45,485,95,300,90,0,63,51,p10,4000",
    "2026-03-06,2026-03-06 00:30,2026-03-06 07:35,420,80,260,80,5,61,53,p10,5000",
    "2026-03-07,2026-03-07 00:50,2026-03-07 07:40,410,80,250,80,0,60,52,p10,3000",
    "2026-03-08,2026-03-08 01:00,2026-03-08 08:10,430,90,260,80,0,62,54,p10,10000",
    "2026-03-09,2026-03-08 23:20,2026-03-09 07:30,480,100,300,80,10,59,50,p10,2000",  # 导出当天
]


def seed(root, rows=ROWS, when=NOW):
    path = health.data_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("﻿" + "\n".join([HEAD, *rows]) + "\n", encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def test_days_summary_and_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    seed(tmp_path)
    data = health.view(NOW)
    assert data["error"] == data["empty"] == data["stale"] == ""
    assert (data["first_date"], data["last_date"], data["stale_days"]) == ("2026-03-02", "2026-03-09", 0)
    first, second, last = data["days"][0], data["days"][1], data["days"][-1]
    assert first["sleep_min"] is None and first["bed"] is None and first["partial"]
    assert (second["bed"], second["wake"], second["sleep_min"], second["hr_estimated"]) == (-30, 460, 480, True)
    assert last["partial"] and not second["partial"]
    s = data["summary"]
    assert (s["nights"], s["aim"], s["keep"], s["short"], s["late"]) == (7, 4, 2, 1, 3)
    assert s["sleep_avg"] == 456 and s["bed_spread"] == 120
    assert s["steps_days"] == 6 and s["steps_avg"] == 6000  # 首日和导出当天不算
    assert (s["resting_low"], s["resting_high"], s["resting_estimated"]) == (50, 54, True)
    text = "\n".join(data["findings"])
    assert "7 晚平均睡 7 小时 36 分：4 晚达到 8 小时，2 晚在 7–8 小时，1 晚不足 7 小时。" in text
    assert "最早 23:00（03-04），最晚 01:00（03-08），相差 2 小时 00 分。" in text
    assert "过 0 点才睡的 3 晚平均睡 7 小时 00 分，0 点前睡的 4 晚平均 8 小时 04 分，晚睡的少 1 小时 04 分。" in text
    assert "估算静息心率平均 52 次/分，在 50–54 之间。" in text
    assert "6 个完整天日均 6000 步，最多的是 03-08 的 10000 步。" in text
    assert not any("比之前" in line for line in data["findings"])  # 不满两段各 3 晚，不比较


def test_week_comparison_needs_both_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    earlier = [f"2026-02-{day:02d},2026-02-{day - 1:02d} 23:30,2026-02-{day:02d} 07:00,450,90,280,80,0,60,50,p10,5000" for day in (24, 25, 26)]
    seed(tmp_path, earlier + ROWS[1:])
    assert any(line.startswith("最近 7 晚平均 7 小时 36 分，比之前 3 晚（7 小时 30 分）多 6 分钟。") for line in health.view(NOW)["findings"])


def test_stale_empty_and_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    assert health.view(NOW)["empty"] == "还没有导入过健康数据。"
    seed(tmp_path)
    later = health.view(NOW + timedelta(days=3))
    assert later["stale"] == "数据只到 2026-03-09，已经 3 天没更新。" and later["stale_days"] == 3
    seed(tmp_path, rows=[])
    assert health.view(NOW)["empty"] == "健康数据文件里没有记录。"
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path / "nowhere"))
    assert "助理目录不存在" in health.view(NOW)["error"]


def test_analysis_is_passed_through_and_flagged_when_older_than_the_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    seed(tmp_path)
    assert health.view(NOW)["analysis"] is None
    note = health.analysis_file(tmp_path)
    note.write_text("数据截至：2026-03-09\n写于：2026-03-10\n\n## 一句话\n\n编的解读。\n", encoding="utf-8")
    assert health.view(NOW)["analysis"] == {"text": "## 一句话\n\n编的解读。", "through": "2026-03-09", "written": "2026-03-10", "outdated": False}
    note.write_text("数据截至：2026-03-05\n\n编的解读。\n", encoding="utf-8")
    assert health.view(NOW)["analysis"] == {"text": "编的解读。", "through": "2026-03-05", "written": "", "outdated": True}
    note.write_text("数据截至：2026-03-05\n\n", encoding="utf-8")
    assert health.view(NOW)["analysis"] is None
    note.unlink()
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("别处的文件", encoding="utf-8")
    note.symlink_to(elsewhere)
    assert health.view(NOW)["analysis"] is None


def test_symlinked_file_is_not_read(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("ASSISTANT_ROOT", str(root))
    real = seed(tmp_path / "elsewhere")
    link = health.data_file(root)
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert health.view(NOW)["empty"] == "还没有导入过健康数据。"


def test_route(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path / "assistant"))
    seed(tmp_path / "assistant")
    with instance(tmp_path, monkeypatch) as request:
        data = request("/api/health/daily")
    assert len(data["days"]) == 8 and data["summary"]["nights"] == 7 and data["targets"] == {"keep": 420, "aim": 480}
