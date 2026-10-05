#!/bin/bash
# 梯度压测 runner：三接口 × 五档并发，每档 60s，CSV 导出。
# 用法: ./perf/run_gradient.sh <label> [order_mode]
#   label      结果文件名前缀（baseline / pooled）
#   order_mode order 场景商品模式（spread=分散 / hot=抢同一商品，默认 spread）
LABEL=${1:-baseline}
ORDER_MODE=${2:-spread}
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python.exe
mkdir -p perf/results

for scen in login search order; do
  for u in 50 100 200 300 500; do
    echo "=== [$LABEL] $scen u=$u ==="
    if [ "$scen" = "order" ]; then
      env SCENARIO=order ORDER_MODE=$ORDER_MODE $PY -m locust -f perf/locustfile.py \
        --headless -u $u -r $u -t 60s \
        --csv "perf/results/${LABEL}_${scen}_${u}" --host http://127.0.0.1:8000 2>&1 | tail -2
    else
      env SCENARIO=$scen $PY -m locust -f perf/locustfile.py \
        --headless -u $u -r $u -t 60s \
        --csv "perf/results/${LABEL}_${scen}_${u}" --host http://127.0.0.1:8000 2>&1 | tail -2
    fi
  done
done
echo "=== [$LABEL] done ==="
