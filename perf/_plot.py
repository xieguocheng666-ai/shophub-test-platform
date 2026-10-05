"""画「并发-TPS」与「并发-P95」拐点曲线（baseline vs pooled），跑完即删。

用法: python perf/_plot.py
输出: perf/results/charts/tps_curve.png / p95_curve.png
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCENARIOS = {"login": "登录", "search": "搜索", "order": "下单"}
USERS = [50, 100, 200, 300, 500]
RESULT_DIR = os.path.join(os.path.dirname(__file__), "results")
OUT_DIR = os.path.join(RESULT_DIR, "charts")
MAIN_NAME = {"login": "login", "search": "search", "order": "order"}

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def load(label, scen, u):
    path = os.path.join(RESULT_DIR, f"{label}_{scen}_{u}_stats.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["Name"]] = row
    return rows.get(MAIN_NAME[scen], {}) or rows.get("Aggregated", {})


def series(label, scen, key):
    out = []
    for u in USERS:
        row = load(label, scen, u)
        if not row:
            out.append(None)
            continue
        try:
            out.append(float(row.get(key, 0)))
        except (TypeError, ValueError):
            out.append(None)
    return out


def make_figure(metric, ylabel, fname):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), sharex=True)
    for ax, (scen, cname) in zip(axes, SCENARIOS.items()):
        base = series("baseline", scen, metric)
        pool = series("pooled", scen, metric)
        ax.plot(USERS, base, "o--", color="#c0504d", label="无连接池")
        ax.plot(USERS, pool, "o-", color="#1f6fb2", label="连接池")
        ax.set_title(cname, fontsize=12)
        ax.set_xlabel("并发数")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
    fig.suptitle(f"并发 - {ylabel}", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    os.makedirs(OUT_DIR, exist_ok=True)
    fig.savefig(os.path.join(OUT_DIR, fname), dpi=130)
    print(f"saved {fname}")


if __name__ == "__main__":
    make_figure("Requests/s", "TPS", "tps_curve.png")
    make_figure("95%", "P95 响应时间 (ms)", "p95_curve.png")
