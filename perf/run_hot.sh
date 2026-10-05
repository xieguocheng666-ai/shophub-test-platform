#!/bin/bash
# 行锁竞争专项：所有用户抢同一商品 (2,5)，测原子扣库存的并发吞吐上限。
# 用法: bash perf/run_hot.sh
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python.exe
mkdir -p perf/results

for u in 50 100 200 300 500; do
  echo "=== [hot] order u=$u ==="
  env SCENARIO=order ORDER_MODE=hot $PY -m locust -f perf/locustfile.py \
    --headless -u $u -r $u -t 60s \
    --csv "perf/results/hot_order_${u}" --host http://127.0.0.1:8000 2>&1 | tail -2
done
echo "=== [hot] done ==="
