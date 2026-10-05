"""对比 baseline vs pooled 的关键指标，输出 markdown 对比表。跑完即删。

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
        print(f"\n## {scen}（TPS 与 P95 对比）")
        print("| 并发 | 基线TPS | 池化TPS | 提升 | 基线P95(ms) | 池化P95(ms) | P95降幅 |")
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
        # 找拐点：TPS 峰值档
        peak_u, peak_tps = None, 0
        for u in USERS:
            p = load("pooled", scen, u)
            if not p:
                continue
            t = num(p, "Requests/s")
            if t > peak_tps:
                peak_tps, peak_u = t, u
        print(f"> 池化后 {scen} TPS 峰值 ≈ {peak_tps:.1f}（@ {peak_u} 并发）")


if __name__ == "__main__":
    main()
