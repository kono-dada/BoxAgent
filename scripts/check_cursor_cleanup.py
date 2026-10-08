# /// script
# requires-python = ">=3.12"
# dependencies = ["pyobjc-framework-Quartz>=11,<13"]
# ///
"""只读观察手动测试的光标窗口与任务结束时间，不操作界面或结束任何进程。"""

import argparse
import json
from pathlib import Path
import time

import Quartz

ROOT = Path(__file__).resolve().parents[1]


def cursor_windows():
    rows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, 0)
    if not rows or not Quartz.CGPreflightScreenCaptureAccess():
        raise RuntimeError("无法可靠读取窗口；请在有屏幕录制权限的终端运行，空结果不能视为无残留")
    return sorted(row["kCGWindowNumber"] for row in rows
                  if ("Computer Use" in row.get("kCGWindowOwnerName", "")
                      or row.get("kCGWindowOwnerName") == "SkyComputerUseService")
                  and "Cursor" in row.get("kCGWindowName", "")
                  and row.get("kCGWindowIsOnscreen"))


def observe(data_dir, duration):
    started = time.time()
    output = ROOT / ".runtime" / ("cursor-observation-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    baseline = set(cursor_windows())
    seen = set()
    previous = None
    ended_seen_at = None
    current = {}
    with (output / "samples.jsonl").open("w") as stream:
        while time.time() - started < duration:
            now = time.time()
            windows = cursor_windows()
            seen.update(set(windows) - baseline)
            try:
                latest = json.loads((data_dir / "runs/latest.json").read_text())
            except (OSError, json.JSONDecodeError):
                latest = {}
            current = latest if latest.get("started_at", 0) >= started else {}
            state = (windows, current.get("task_id"), current.get("event"))
            if state != previous:
                record = {"at": now, "elapsed": round(now - started, 3),
                          "windows": windows, "task_id": current.get("task_id"),
                          "event": current.get("event"), "outcome": current.get("outcome"),
                          "ended_at": current.get("ended_at")}
                line = json.dumps(record, ensure_ascii=False)
                stream.write(line + "\n")
                stream.flush()
                print(line, flush=True)
                previous = state
            if current.get("event") == "ended":
                ended_seen_at = ended_seen_at or now
                if now - ended_seen_at >= 5:
                    break
            time.sleep(.1)
    remaining = sorted(set(windows) - baseline)
    summary = {"output": str(output), "baseline": sorted(baseline),
               "created": sorted(seen), "remaining": remaining,
               "task_id": current.get("task_id"), "outcome": current.get("outcome"),
               "cleanup": current.get("cursor_cleanup"),
               "status": "observed_clear" if seen and ended_seen_at and not remaining
                         else "residual" if remaining else "inconclusive"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".runtime/pet")
    parser.add_argument("--duration", type=float, default=300)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("观察时长必须大于 0")
    observe(args.data_dir, args.duration)
