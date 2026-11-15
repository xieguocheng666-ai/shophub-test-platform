"""顺序 pytest 用例共享诊断；同例工作线程可写入，不能用于共享进程并行用例。"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_TEXT = 2048
MAX_ITEMS = 100
MAX_REQUESTS = 200
_lock = RLock()
_active = None
_cases = {}
_DEFAULT_CASE = object()
_sensitive = re.compile(r"authorization|cookie|password|passwd|pwd|token|secret|credential|api.?key|db.?user", re.I)


def sanitize(value, _depth=0):
    """递归脱敏并限制尺寸；未知对象不调用可能含凭据的 repr/str。"""
    if _depth > 8:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(k)[:MAX_TEXT]: "[REDACTED]" if _sensitive.search(str(k)) else sanitize(v, _depth + 1)
                for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, _depth + 1) for v in value[:MAX_ITEMS]]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)((?:password|passwd|pwd|token|secret|authorization|credential|api_key)[\w-]*\s*[=:]\s*)[^\s&,;\"']+", r"\1[REDACTED]", value)
        return value[:MAX_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported value omitted]"


def safe_params(params):
    """统一查询参数的字典、键值对和编码字符串形式，保留重复键并按键脱敏。"""
    if params is None:
        return None
    if isinstance(params, bytes):
        query = params.decode("utf-8", errors="replace")
    elif isinstance(params, str):
        query = params
    else:
        query = urlencode(params, doseq=True)
    return [(sanitize(k), "[REDACTED]" if _sensitive.search(k) else sanitize(v))
            for k, v in parse_qsl(query, keep_blank_values=True)[:MAX_ITEMS]]


def safe_url(url):
    """去除 URL 用户信息并按键脱敏查询串。"""
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    query = urlencode(safe_params(parts.query))
    return sanitize(urlunsplit((parts.scheme, host, parts.path, query, "")))


@dataclass
class CaseRecord:
    nodeid: str
    requests: list = field(default_factory=list)
    phases: list = field(default_factory=list)
    dropped_requests: int = 0
    closed: bool = False


def begin_case(nodeid):
    """在 pytest setup 之前调用。当前执行模式只允许一个活动用例。"""
    global _active
    with _lock:
        _active = CaseRecord(nodeid)
        _cases[nodeid] = _active


def get_case():
    with _lock:
        return _active


def record_request(record, *, case=_DEFAULT_CASE):
    """case 在发送前捕获，使延迟返回的旧请求不会写入下一例。"""
    try:
        with _lock:
            target = _active if case is _DEFAULT_CASE else case
            if target is None or target.closed:
                return
            if len(target.requests) < MAX_REQUESTS:
                target.requests.append(sanitize(record))
            else:
                target.dropped_requests += 1
    except Exception:
        pass  # 诊断永远不能替代原业务结果。


def finish_case(phase, outcome, error=None):
    """记录每个 pytest 阶段；异常仅保留类别，避免异常消息泄漏凭据。"""
    try:
        if error is None:
            category = None
        elif isinstance(error, type) and issubclass(error, BaseException):
            category = error.__name__
        elif isinstance(error, BaseException):
            category = type(error).__name__
        else:
            category = "[error message omitted]"
        with _lock:
            if _active is not None:
                _active.phases.append({"phase": sanitize(phase), "outcome": sanitize(outcome), "error": category})
    except Exception:
        pass


def write_failure(output_dir, nodeid):
    """写入固定哈希文件名；写失败返回 None，不抛出异常。"""
    try:
        with _lock:
            case = _cases.get(nodeid)
            if case is None:
                return None
            payload = {"nodeid": sanitize(nodeid), "requests": list(case.requests),
                       "phases": list(case.phases), "dropped_requests": case.dropped_requests}
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:24]
        path = directory / f"case-{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def end_case(nodeid=None):
    """teardown 报告处理完成后释放上下文，拒收晚到请求。"""
    global _active
    with _lock:
        key = nodeid if nodeid is not None else (_active.nodeid if _active else None)
        case = _cases.pop(key, None)
        if case is not None:
            case.closed = True
        if case is _active:
            _active = None
