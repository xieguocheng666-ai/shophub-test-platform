"""服务端口必须同时可绑定且避开浏览器的限制端口。"""
from tools import environments


def test_port_allocation_uses_high_range_and_retries_collision(monkeypatch):
    attempts = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, address):
            attempts.append(address[1])
            assert 49152 <= address[1] <= 65535
            if len(attempts) == 1:
                raise OSError('port occupied')
        def getsockname(self):
            return ('127.0.0.1', attempts[-1])
    monkeypatch.setattr(environments.socket, 'socket', Socket)
    assert environments.free_port() == attempts[-1]
    assert len(attempts) == 2
