"""压测铺数据脚本。

用法（在项目根目录）：
    ./.venv/Scripts/python.exe perf/seed_data.py

作用：
    1. 确保库表 + 种子数据就绪（幂等）；
    2. 创建 perfuser0..(N-1) 压测账号（密码统一 perf123，哈希只算一次复用）；
    3. 把所有商品库存铺到 100 万，保证长时间压测下单不因库存耗尽报错。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from server import db

PERF_USER_COUNT = 1000
STOCK_TARGET = 1_000_000


def main():
    db.init_db()  # 幂等：确保库表种子就绪

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # 1. 压测账号（密码哈希只算一次，复用）
            cur.execute("SELECT COUNT(*) AS n FROM users WHERE username LIKE 'perfuser%'")
            existing = cur.fetchone()["n"]
            if existing < PERF_USER_COUNT:
                pwd_hash = db.hash_password("perf123")
                for i in range(PERF_USER_COUNT):
                    cur.execute(
                        "INSERT IGNORE INTO users (username, password, nickname, role) "
                        "VALUES (%s, %s, %s, 'buyer')",
                        (f"perfuser{i}", pwd_hash, f"压测用户{i}"),
                    )
                print(f"[seed] 新增 {PERF_USER_COUNT - existing} 个压测账号")
            else:
                print(f"[seed] 压测账号已存在 {existing} 个，跳过创建")

            # 2. 库存铺满
            cur.execute("UPDATE store_product SET stock = %s", (STOCK_TARGET,))
            affected = cur.rowcount
        conn.commit()
        print(f"[seed] 库存已铺到 {STOCK_TARGET}（{affected} 个商品行）")
    finally:
        conn.close()
    print("[seed] 完成")


if __name__ == "__main__":
    main()
