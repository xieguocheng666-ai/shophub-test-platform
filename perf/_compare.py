"""对比历史 baseline/pooled 的请求速率和P95，输出Markdown表。

Requests/s包含失败请求，不表示成功事务吞吐；错误数与证据边界见report.md。

用法: python perf/_compare.py
"""
import csv
import os

SCENARIOS = ["login", "search", "order"]
USERS = [50, 100, 200, 300, 500]
RESULT_DIR = os.path.join(os.path.dirname(__file__), "results")
MAIN_NAME = {"login": "login", "search": "search", "order": "order"}


def load(label, scen, u):
    path = os.path.join(RESULT_DIR, f"{label}_{scen}_{u}_stats.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["Name"]] = row
    main = rows.get(MAIN_NAME[scen], {}) or rows.get("Aggregated", {})
    return main


def num(row, key):
    try:
        return float(row.get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def main():
    for scen in SCENARIOS:
        print(f"\n## {scen}（请求/秒与 P95 对比，请求包含失败）")
        print("| 虚拟用户数 | 基线请求/秒 | 池化请求/秒 | 请求速率比 | 基线P95(ms) | 池化P95(ms) | P95降幅 |")
        print("|---|---|---|---|---|---|---|")
        for u in USERS:
            b = load("baseline", scen, u)
            p = load("pooled", scen, u)
            if not b or not p:
                print(f"| {u} | MISSING |")
                continue
            bt, pt = num(b, "Requests/s"), num(p, "Requests/s")
            bp, pp = num(b, "95%"), num(p, "95%")
            speedup = pt / bt if bt else 0
            p95_reduce = (1 - pp / bp) * 100 if bp else 0
            print(
                f"| {u} | {bt:.1f} | {pt:.1f} | {speedup:.2f}x | "
                f"{bp:.0f} | {pp:.0f} | {p95_reduce:.0f}% |"
            )
        # 找已测档位中的请求速率峰值；不自动认定为性能拐点
        peak_u, peak_tps = None, 0
        for u in USERS:
            p = load("pooled", scen, u)
            if not p:
                continue
            t = num(p, "Requests/s")
            if t > peak_tps:
                peak_tps, peak_u = t, u
        print(f"> 池化后 {scen} 请求/秒峰值 ≈ {peak_tps:.1f}（@ {peak_u} 虚拟用户）")


if __name__ == "__main__":
    main()
