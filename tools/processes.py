"""测试进程身份登记：命令只保存摘要，不将潜在凭据写入清单。"""
import hashlib
import json
import psutil


def identity(process, role):
    command = json.dumps(process.cmdline(), ensure_ascii=True, separators=(',', ':'))
    return {'pid': process.pid, 'created_at': process.create_time(), 'role': role,
            'fingerprint': hashlib.sha256(command.encode()).hexdigest()}


def stop_registered(records):
    failures = []
    for record in reversed(records):
        try:
            process = psutil.Process(record['pid'])
            observed = identity(process, record['role'])
            if observed != record:
                failures.append({'pid': record['pid'], 'type': 'IdentityMismatch'})
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            failures.append({'pid': record['pid'], 'type': type(exc).__name__})
    return failures
