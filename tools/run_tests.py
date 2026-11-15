"""在本轮独占的数据库和服务上顺序执行测试，finally负责回收资源。"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
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
            if response.status_code == 200 and response.headers.get('X-Test-Run-ID') == env['TEST_RUN_ID']:
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['unit', 'api', 'all'], required=True)
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'reports' / 'engineering')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER, help='pytest arguments after --')
    args = parser.parse_args(argv)
    run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid.uuid4().hex[:10]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', run_id):
        parser.error('run-id must use 1-80 ASCII letters, numbers, underscores or hyphens')
    output = args.output_dir.resolve() / run_id
    # Never replace evidence from an earlier run.
    output.mkdir(parents=True, exist_ok=False)
    summary = {'run_id': run_id, 'suite': args.suite, 'version': version_info(),
               'exitcode': 2, 'pytest_exitcode': None,
               'timings': {'prepare': 0, 'execute': 0, 'cleanup': 0},
               'cleanup': {'service': 'not_started', 'database': 'not_created'},
               'reports': {'junit': str(output / 'junit.xml'), 'service': str(output / 'service.log')},
               'errors': []}
    env = os.environ.copy()
    service = None
    database_created = False
    started = time.monotonic()
    phase = 'prepare'
    log = (output / 'service.log').open('ab', buffering=0)
    try:
        if args.suite != 'unit':
            env = load_environment()
            env['DB_NAME'] = 'shophub_test_' + uuid.uuid4().hex
            env['BASE_URL'] = f'http://127.0.0.1:{free_port()}'
            env['DEBUG_MODE'] = '1'
            # This process owns neither a pre-existing database nor service.
            summary['database'] = env['DB_NAME']
        env['TEST_RUN_ID'] = run_id
        env['TEST_ARTIFACT_DIR'] = str(output / 'diagnostics')
        env['PYTHONUNBUFFERED'] = '1'
        if args.suite != 'unit':
            try:
                create_database(env)
            except DatabaseCreatedError:
                database_created = True
                summary['cleanup']['database'] = 'pending'
                raise
            database_created = True
            summary['cleanup']['database'] = 'pending'
            code = execute([sys.executable, '-c',
                            'from server import db; '
                            'exec("try:\\n db.init_db()\\nexcept Exception as exc:\\n print(type(exc).__name__)\\n raise SystemExit(2)")'],
                           env=env, log=log, timeout=90)
            if code:
                raise RunnerError('Database initialization failed; see service.log')
            service = start_service(env, log)
            summary['cleanup']['service'] = 'pending'
            wait_for_service(service, env)
        summary['timings']['prepare'] = time.monotonic() - started
        phase = 'execute'
        started = time.monotonic()
        suites = ['unit'] if args.suite == 'unit' else ['api'] if args.suite == 'api' else ['unit', 'api', 'ui']
        extras = args.pytest_args[1:] if args.pytest_args[:1] == ['--'] else args.pytest_args
        command = [sys.executable, '-m', 'pytest', *suites, '-o', 'addopts=',
                   f'--junitxml={output / "junit.xml"}']
        if args.suite == 'all':
            command += ['--screenshot', 'only-on-failure', '--tracing', 'retain-on-failure',
                        f'--output={output / "ui"}']
        command += extras
        summary['pytest_exitcode'] = execute(command, env=env)
        summary['exitcode'] = summary['pytest_exitcode']
    except KeyboardInterrupt:
        summary['exitcode'] = 130
        summary['errors'].append({'phase': phase, 'type': 'KeyboardInterrupt'})
    except Exception as exc:
        # Exception strings may include credentials supplied by drivers; record type only.
        error = {'phase': phase, 'type': type(exc).__name__}
        if isinstance(exc, RunnerError):
            error['detail'] = str(exc)
        summary['errors'].append(error)
        print(f'{phase} failed ({type(exc).__name__}); see {output}', file=sys.stderr)
    finally:
        summary['timings'][phase] = time.monotonic() - started
        cleanup_start = time.monotonic()
        if service is not None:
            try:
                stop_process(service)
                summary['cleanup']['service'] = 'stopped'
            except (Exception, KeyboardInterrupt) as exc:
                summary['cleanup']['service'] = 'failed'
                summary['errors'].append({'phase': 'cleanup_service', 'type': type(exc).__name__})
        if database_created:
            try:
                drop_database(env)
                summary['cleanup']['database'] = 'removed'
            except (Exception, KeyboardInterrupt) as exc:
                summary['cleanup']['database'] = 'failed'
                summary['errors'].append({'phase': 'cleanup_database', 'type': type(exc).__name__})
        log.close()
        summary['timings']['cleanup'] = time.monotonic() - cleanup_start
        if 'failed' in summary['cleanup'].values() and summary['exitcode'] == 0:
            summary['exitcode'] = 2
        (output / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'Run {run_id}: exit {summary["exitcode"]}; summary: {output / "summary.json"}')
    return summary['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
