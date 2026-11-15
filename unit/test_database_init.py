"""初始化库名必须受限，避免环境变量变成SQL。"""
import pytest
from server import db


@pytest.mark.parametrize('name', ['', 'shop`; DROP DATABASE shop', 'a-b', 'a' * 65, '123', 'a\n'])
def test_init_rejects_invalid_name_before_connecting(name, monkeypatch):
    monkeypatch.setenv('DB_PASSWORD', 'unit-placeholder')
    monkeypatch.setenv('DB_NAME', name)
    monkeypatch.setattr(db.pymysql, 'connect', lambda **kw: pytest.fail('非法库名不应连接数据库'))
    with pytest.raises(ValueError):
        db.init_db()


def test_init_creates_selected_database_not_shop(monkeypatch):
    statements = []
    class Connection:
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql, *args):
            statements.append(sql)
        def close(self):
            pass
        def commit(self):
            pass
    monkeypatch.setenv('DB_NAME', 'shophub_test_specific')
    monkeypatch.setattr(db.pymysql, 'connect', lambda **kw: Connection())
    monkeypatch.setattr(db, 'get_conn', lambda: Connection())
    monkeypatch.setattr(db, '_seed', lambda cur: None)
    db.init_db()
    assert 'CREATE DATABASE IF NOT EXISTS `shophub_test_specific`' in statements[0]
    assert not any('EXISTS shop ' in sql for sql in statements)
