"""「健康」标签：只读助理目录的 资料/raw/health/zepp-daily.csv（助理仓库 scripts/zepp_import.py 从 Zepp 导出包生成）。

助理目录和 personal/assistant.py 用同一个（默认 /Volumes/Luochen/LCS/assistant，环境变量 ASSISTANT_ROOT）。
这里不写任何文件、不调用模型：每天一行的睡眠、心率、步数原样给页面，再算几个汇总数和几句结论。
同一目录的 分析.md 是 AI 在导入数据后写的解读（开头两行「数据截至：」「写于：」），有就原样给页面，并标出它是不是比数据旧。
睡眠记在醒来那天；入睡、醒来时刻用「相对醒来那天 0 点的分钟数」表示（23:30 入睡是 -30），页面画图和求平均都用它。
"""
from __future__ import annotations
import csv
import re
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from . import assistant

KEEP_MIN = 7 * 60  # 保 7
AIM_MIN = 8 * 60   # 争 8
STALE_DAYS = 2     # 数据比今天旧这么多天就提示重新导出
GROUP_MIN = 3      # 两组比较时每组至少这么多晚
UPDATE_HINT = "更新方法：在华米网页导出数据，解压后运行助理仓库的 python3 scripts/zepp_import.py <导出文件夹>（步骤见助理目录 规范/健康数据.md）。"


def data_file(base: Path) -> Path:
    return base / "资料" / "raw" / "health" / "zepp-daily.csv"


def analysis_file(base: Path) -> Path:
    return base / "资料" / "raw" / "health" / "分析.md"


HEADER = re.compile(r"^(数据截至|写于)[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*$")


def read_analysis(base: Path, last_date: str) -> dict | None:
    """分析.md 的正文和开头的两个日期；outdated：数据比写分析时用的新。没有文件、读不出来或正文是空的都是 None。"""
    path = analysis_file(base)
    if not path.is_file() or not assistant.inside(base, path):
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    found = {"数据截至": "", "写于": ""}
    while lines and (not lines[0].strip() or HEADER.match(lines[0].strip())):
        if match := HEADER.match(lines[0].strip()):
            found[match[1]] = match[2]
        lines.pop(0)
    text = "\n".join(lines).strip()
    if not text:
        return None
    through = found["数据截至"]
    return {"text": text, "through": through, "written": found["写于"], "outdated": bool(through and last_date and through < last_date)}


def number(value: str | None) -> int | None:
    try:
        return round(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def offset(stamp: str | None, day: date) -> int | None:
    """「2026-11-09 23:37」相对 day 那天 0 点的分钟数；读不出来是 None。"""
    try:
        when = datetime.strptime((stamp or "").strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return round((when - datetime.combine(day, datetime.min.time())).total_seconds() / 60)


def clock(minutes: float) -> str:
    """相对 0 点的分钟数写成 HH:MM（负数是前一天晚上）。"""
    total = round(minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def span(minutes: float) -> str:
    total = round(minutes)
    return f"{total // 60} 小时 {total % 60:02d} 分" if total >= 60 else f"{total} 分钟"


def short_day(iso: str) -> str:
    return iso[5:]


def read_days(path: Path, exported: date) -> list[dict]:
    """每天一行，旧的在前。partial：导出当天，或开始戴表的第一天（没有睡眠记录的首日），步数和心率不是整天的。"""
    days: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                day = date.fromisoformat((row.get("date") or "").strip())
            except ValueError:
                continue
            sleep = number(row.get("sleep_min"))
            days.append({
                "date": day.isoformat(),
                "sleep_min": sleep if sleep else None,
                "deep_min": number(row.get("deep_min")), "light_min": number(row.get("light_min")),
                "rem_min": number(row.get("rem_min")), "awake_min": number(row.get("awake_min")),
                "bed": offset(row.get("sleep_start"), day) if sleep else None,
                "wake": offset(row.get("sleep_end"), day) if sleep else None,
                "hr_avg": number(row.get("hr_avg")), "hr_resting": number(row.get("hr_resting")),
                "hr_estimated": (row.get("hr_resting_source") or "").strip() == "p10",
                "steps": number(row.get("steps")),
                "partial": False,
            })
    days.sort(key=lambda d: d["date"])
    if days:
        days[-1]["partial"] = days[-1]["date"] == exported.isoformat()
        if days[0]["sleep_min"] is None:
            days[0]["partial"] = True
    return days


def summarize(days: list[dict]) -> dict:
    nights = [d for d in days if d["sleep_min"]]
    timed = [d for d in nights if d["bed"] is not None and d["wake"] is not None]
    staged = [d for d in nights if d["deep_min"] is not None and d["rem_min"] is not None]
    resting = [d["hr_resting"] for d in days if d["hr_resting"] and not d["partial"]]
    walked = [d for d in days if d["steps"] is not None and not d["partial"]]
    asleep = sum(d["sleep_min"] for d in staged)
    return {
        "nights": len(nights),
        "sleep_avg": round(mean(d["sleep_min"] for d in nights)) if nights else None,
        "aim": sum(d["sleep_min"] >= AIM_MIN for d in nights),
        "keep": sum(KEEP_MIN <= d["sleep_min"] < AIM_MIN for d in nights),
        "short": sum(d["sleep_min"] < KEEP_MIN for d in nights),
        "bed_avg": round(mean(d["bed"] for d in timed)) if timed else None,
        "wake_avg": round(mean(d["wake"] for d in timed)) if timed else None,
        "bed_spread": max(d["bed"] for d in timed) - min(d["bed"] for d in timed) if timed else None,
        "late": sum(d["bed"] >= 0 for d in timed),
        "deep_share": round(100 * sum(d["deep_min"] for d in staged) / asleep) if asleep else None,
        "rem_share": round(100 * sum(d["rem_min"] for d in staged) / asleep) if asleep else None,
        "resting_avg": round(mean(resting)) if resting else None,
        "resting_low": min(resting) if resting else None,
        "resting_high": max(resting) if resting else None,
        "resting_estimated": any(d["hr_estimated"] for d in days),
        "steps_avg": round(mean(d["steps"] for d in walked)) if walked else None,
        "steps_days": len(walked),
    }


def findings(days: list[dict], summary: dict) -> list[str]:
    """几句结论，都从上面的数算出来；样本少的比较不写。"""
    out: list[str] = []
    nights = [d for d in days if d["sleep_min"]]
    if not nights:
        return out
    out.append(f"{summary['nights']} 晚平均睡 {span(summary['sleep_avg'])}：{summary['aim']} 晚达到 8 小时，"
               f"{summary['keep']} 晚在 7–8 小时，{summary['short']} 晚不足 7 小时。")
    timed = [d for d in nights if d["bed"] is not None and d["wake"] is not None]
    if timed:
        first, last = min(timed, key=lambda d: d["bed"]), max(timed, key=lambda d: d["bed"])
        out.append(f"平均 {clock(summary['bed_avg'])} 入睡、{clock(summary['wake_avg'])} 醒来；最早 {clock(first['bed'])}（{short_day(first['date'])}），"
                   f"最晚 {clock(last['bed'])}（{short_day(last['date'])}），相差 {span(summary['bed_spread'])}。")
        late, early = [d for d in timed if d["bed"] >= 0], [d for d in timed if d["bed"] < 0]
        if len(late) >= GROUP_MIN and len(early) >= GROUP_MIN:
            a, b = mean(d["sleep_min"] for d in late), mean(d["sleep_min"] for d in early)
            gap = f"少 {span(b - a)}" if b - a >= 1 else f"多 {span(a - b)}" if a - b >= 1 else "一样长"
            out.append(f"过 0 点才睡的 {len(late)} 晚平均睡 {span(a)}，0 点前睡的 {len(early)} 晚平均 {span(b)}，晚睡的{gap}。")
    recent, before = nights[-7:], nights[-14:-7]
    if len(recent) >= GROUP_MIN and len(before) >= GROUP_MIN:
        a, b = mean(d["sleep_min"] for d in recent), mean(d["sleep_min"] for d in before)
        gap = f"多 {span(a - b)}" if a - b >= 1 else f"少 {span(b - a)}" if b - a >= 1 else "一样长"
        out.append(f"最近 {len(recent)} 晚平均 {span(a)}，比之前 {len(before)} 晚（{span(b)}）{gap}。")
    if summary["deep_share"] is not None:
        out.append(f"深睡占睡眠时间的 {summary['deep_share']}%，REM 占 {summary['rem_share']}%。")
    if summary["resting_avg"] is not None:
        how = "估算静息心率" if summary["resting_estimated"] else "静息心率"
        out.append(f"{how}平均 {summary['resting_avg']} 次/分，在 {summary['resting_low']}–{summary['resting_high']} 之间。")
    walked = [d for d in days if d["steps"] is not None and not d["partial"]]
    if walked:
        top = max(walked, key=lambda d: d["steps"])
        out.append(f"{len(walked)} 个完整天日均 {summary['steps_avg']} 步，最多的是 {short_day(top['date'])} 的 {top['steps']} 步。")
    return out


def view(now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    base = assistant.root()
    payload: dict = {"today": now.date().isoformat(), "updated": "", "first_date": "", "last_date": "", "stale_days": None,
                     "stale": "", "days": [], "summary": None, "findings": [], "targets": {"keep": KEEP_MIN, "aim": AIM_MIN},
                     "update_hint": UPDATE_HINT, "analysis": None, "empty": "", "error": ""}
    if not base.is_dir():
        payload["error"] = f"助理目录不存在：{base}（可用环境变量 ASSISTANT_ROOT 指定）"
        return payload
    path = data_file(base)
    if not path.is_file() or not assistant.inside(base, path):
        payload["empty"] = "还没有导入过健康数据。"
        return payload
    try:
        exported = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        days = read_days(path, exported.date())
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        payload["error"] = f"健康数据读不出来：{type(exc).__name__}: {exc}"
        return payload
    if not days:
        payload["empty"] = "健康数据文件里没有记录。"
        return payload
    summary = summarize(days)
    last = date.fromisoformat(days[-1]["date"])
    stale = (now.date() - last).days
    payload.update({
        "updated": exported.isoformat(timespec="seconds"), "first_date": days[0]["date"], "last_date": days[-1]["date"],
        "stale_days": stale, "days": days, "summary": summary, "findings": findings(days, summary),
        "analysis": read_analysis(base, days[-1]["date"]),
        "stale": f"数据只到 {days[-1]['date']}，已经 {stale} 天没更新。" if stale >= STALE_DAYS else "",
    })
    return payload

