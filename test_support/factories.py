"""生成默认合法、可覆盖字段的测试参数；不在此访问接口或数据库。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import count
import re
from uuid import uuid4


@dataclass(frozen=True)
class UserParams:
    username: str
    password: str
    nickname: str
    role: str


@dataclass(frozen=True)
class ProductParams:
    name: str
    price: Decimal
    stock: int


class DataFactory:
    def __init__(self, namespace=None):
        self.namespace = namespace or f"e1_{uuid4().hex[:16]}"
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,30}", self.namespace):
            raise ValueError("namespace需要1—30个字母、数字或下划线")
        self._sequence = count(1)

    def user(self, *, role="buyer", nickname=None, password=None):
        if role not in {"buyer", "seller"}:
            raise ValueError("role只能为buyer或seller")
        name = f"{self.namespace}_{next(self._sequence)}"
        return UserParams(name, password or f"test-{uuid4().hex}", nickname or name, role)

    def product(self, *, name=None, price="19.90", stock=5):
        try:
            amount = Decimal(str(price))
            valid = amount.is_finite() and 0 <= amount <= Decimal("99999999.99")
            valid = valid and amount == amount.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            valid = False
        if not valid or not isinstance(stock, int) or isinstance(stock, bool) or stock < 0:
            raise ValueError("合法商品参数需要非负两位金额和非负整数库存")
        return ProductParams(name or f"{self.namespace}_product_{next(self._sequence)}", amount, stock)
