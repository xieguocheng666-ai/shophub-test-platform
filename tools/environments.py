"""单套独占环境的生命周期与所有权工具。"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
import pymysql
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


class RunnerError(RuntimeError):
    """只包含运行器生成的安全诊断，不传递驱动异常文本。"""


class DatabaseCreatedError(RunnerError):
    """DDL成功后关闭管理连接失败；调用方仍拥有并须清理这个库。"""


def load_environment():
    return {**{k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}, **os.environ}


def database_connection(env):
    return pymysql.connect(host=env.get('DB_HOST', '127.0.0.1'),
                           port=int(env.get('DB_PORT', '3306')),
                           user=env.get('DB_USER', 'root'),
                           password=env.get('DB_PASSWORD', ''),
                           charset='utf8mb4', autocommit=True,
                           connect_timeout=10, read_timeout=15, write_timeout=15)


def owned_database_name(env):
    name = env['DB_NAME']
    if not re.fullmatch(r'shophub_test_[a-f0-9]{32}', name):
        raise ValueError('Only a generated dedicated database is permitted')
    return name


def create_database(env):
    name = owned_database_name(env)
    conn = database_connection(env)
    created = False
    try:
        with conn.cursor() as cur:
            # Deliberately no IF NOT EXISTS: a collision is never ownership.
            cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            created = True
    finally:
        try:
            conn.close()
        except (Exception, KeyboardInterrupt) as exc:
            if created:
                raise DatabaseCreatedError('Created database requires cleanup') from exc
            raise


def drop_database(env):
    name = owned_database_name(env)
    conn = database_connection(env)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE `{name}`')
    finally:
        conn.close()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def execute(command, env, log=None, timeout=None):
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                               stderr=subprocess.STDOUT if log else None)
    try:
        return process.wait(timeout=timeout)
    finally:
        stop_process(process)


def start_service(env, log):
    port = env['BASE_URL'].rsplit(':', 1)[1]
    return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.main:app',
                             '--host', '127.0.0.1', '--port', port, '--no-access-log'],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_for_service(process, env, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        code = process.poll()
        if code is not None:
            raise RunnerError(f'Service exited before readiness (code {code}); see service.log')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RunnerError('Service readiness deadline exceeded or run identity mismatch; see service.log')
        try:
            response = requests.get(env['BASE_URL'] + '/health', timeout=min(1, remaining))
            if (response.status_code == 200 and response.headers.get('X-Test-Run-ID') == env['TEST_RUN_ID']
                    and (not env.get('TEST_ENVIRONMENT_ID') or
                         (response.headers.get('X-Test-Environment-ID') == env['TEST_ENVIRONMENT_ID']
                          and response.json().get('database') == env['DB_NAME']))):
                return
        except requests.RequestException:
            pass
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def version_info():
    try:
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
        return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}
    except (OSError, subprocess.CalledProcessError):
        return {'commit': 'unknown', 'dirty': None}


