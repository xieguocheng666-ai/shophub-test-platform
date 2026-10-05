"""测试用种子数据引用（与 server/db.py SEED_STORES 保持一致）。

集中管理种子数据里的固定 id，避免多个测试文件重复定义魔法数字。
"""
STORE1, STORE2 = 1, 2
