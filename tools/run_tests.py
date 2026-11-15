"""每测试进程独占数据库和商城服务，统一登记与回收。"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
import subprocess
import threading
import xml.etree.ElementTree as ET
from tools.processes import identity, stop_registered
from tools.environments import (ROOT, RunnerError, DatabaseCreatedError, load_environment,
    database_connection, owned_database_name, create_database, drop_database, free_port,
    stop_process, start_service, wait_for_service, version_info)


def worker_count(suite, requested):
    return requested if requested is not None else 1 if suite == 'unit' else 2


def validate_extras(extras):
    forbidden = ('--tx', '--px', '-d', '--loadscope-reorder', '--rsyncdir', '-n', '--numprocesses', '--dist', '--max-worker-restart', '--output', '--junit',
                 '--base-url', '--override-ini', '-o', '-p', '--rootdir', '--confcutdir', '--basetemp')
    for value in extras:
        if any(value == x or value.startswith(x + '=') for x in forbidden) or any(value.startswith(x) and len(value) > 2 for x in ['-n', '-o', '-p']):
            raise ValueError('pytest argument overrides runner-owned configuration')


def stop_tree(process, registered=None):
    import psutil
    records = list(registered or [])
    if process.poll() is None and getattr(process, 'pid', None):
        try:
            root = psutil.Process(process.pid)
            existing = {(r['pid'], r['created_at']) for r in records}
            for proc in [root, *root.children(recursive=True)]:
                try:
                    record = identity(proc, 'pytest' if proc.pid == root.pid else 'worker')
                    if (record['pid'], record['created_at']) not in existing:
                        records.append(record)
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
    failures = stop_registered(records)
    if failures:
        raise RunnerError('Owned test process cleanup failed; recover using test-processes.json')
    if getattr(process, 'pid', None):
        process.wait(timeout=10)


def execute(command, env, log=None, timeout=None):
    import psutil
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                               stderr=subprocess.STDOUT if log else None)
    started = time.monotonic()
    records = []
    journal = env.get('TEST_PROCESS_MANIFEST') if 'pytest' in command else None
    def observe():
        if not getattr(process, 'pid', None):
            return
        try:
            root = psutil.Process(process.pid)
            known = {(r['pid'], r['created_at']) for r in records}
            for proc in [root, *root.children(recursive=True)]:
                try:
                    record = identity(proc, 'pytest' if proc.pid == root.pid else 'worker')
                    if (record['pid'], record['created_at']) not in known:
                        records.append(record)
                        known.add((record['pid'], record['created_at']))
                        if journal:
                            save(Path(journal), records)
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
    try:
        while True:
            observe()
            try:
                return process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                fatal = env.get('TEST_FATAL_FILE')
                if fatal and Path(fatal).exists():
                    raise RunnerError('Environment contaminated or worker crashed; stopping run')
                if timeout is not None and time.monotonic() - started >= timeout:
                    raise RunnerError('Owned child deadline exceeded')
    finally:
        if getattr(process, 'pid', None):
            stop_tree(process, registered=records)
        else:
            stop_process(process)


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['unit', 'api', 'all'], required=True)
    parser.add_argument('--workers', type=int)
    parser.add_argument('--run-id')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'reports' / 'engineering')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    workers = worker_count(args.suite, args.workers)
    if workers < 1:
        parser.error('workers must be positive')
    extras = args.pytest_args[1:] if args.pytest_args[:1] == ['--'] else args.pytest_args
    try:
        validate_extras(extras)
    except ValueError as exc:
        parser.error(str(exc))
    label = args.run_id or 'run_' + uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', label):
        parser.error('run-id must use 1-80 ASCII letters, numbers, underscores or hyphens')
    output = args.output_dir.resolve() / label
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / 'resources.json'
    summary = {'run_id': uuid.uuid4().hex, 'label': label, 'suite': args.suite, 'workers': workers,
               'version': version_info(), 'exitcode': 2, 'pytest_exitcode': None,
               'timings': {'prepare': 0, 'execute': 0, 'cleanup': 0}, 'environments': [], 'errors': [],
               'cleanup': {'service': 'not_started', 'database': 'not_created'},
               'test_process_manifest': str(output / 'test-processes.json')}
    base = load_environment() if args.suite != 'unit' else os.environ.copy()
    base.pop('PYTEST_ADDOPTS', None)
    if args.suite == 'unit':
        base['TEST_ARTIFACT_DIR'] = str(output / 'unit' / 'diagnostics')
    base.update(TEST_PROCESS_MANIFEST=summary['test_process_manifest'], TEST_RUN_ID=summary['run_id'], TEST_FATAL_FILE=str(output / 'fatal.txt'), PYTHONUNBUFFERED='1')
    processes = []
    logs = []
    started = total_start = time.monotonic()
    phase = 'prepare'
    sampling_stop = threading.Event()
    def sample_connections():
        peak = 0
        while not sampling_stop.wait(.05):
            try:
                conn = database_connection(base)
                try:
                    with conn.cursor() as cur:
                        cur.execute('SHOW PROCESSLIST')
                        names = {e['env']['DB_NAME'] for e in summary['environments']}
                        peak = max(peak, sum(row[3] in names for row in cur.fetchall()))
                finally:
                    conn.close()
                summary['connection_peak_owned_databases'] = peak
            except Exception:
                summary['connection_sampling_errors'] = summary.get('connection_sampling_errors', 0) + 1
    sampler = None
    try:
        if args.suite != 'unit':
            for i in range(workers):
                worker = 'main' if workers == 1 else f'gw{i}'
                folder = output / worker
                folder.mkdir()
                bound = {'DB_NAME': 'shophub_test_' + uuid.uuid4().hex,
                         'BASE_URL': f'http://127.0.0.1:{free_port()}', 'DEBUG_MODE': '1',
                         'TEST_RUN_ID': summary['run_id'], 'TEST_ENVIRONMENT_ID': uuid.uuid4().hex,
                         'TEST_ARTIFACT_DIR': str(folder / 'diagnostics'),
                         'DB_POOL_MIN': '0', 'DB_POOL_MAX': '10'}
                env = {**base, **bound}
                entry = {'worker': worker, 'env': bound, 'cleanup': {'database': 'not_created', 'service': 'not_started'}}
                summary['environments'].append(entry)
                save(manifest_path, summary)
                try:
                    create_database(env)
                except DatabaseCreatedError:
                    entry['cleanup']['database'] = 'pending'
                    save(manifest_path, summary)
                    raise
                entry['cleanup']['database'] = 'pending'
                save(manifest_path, summary)
                log = (folder / 'service.log').open('ab', buffering=0)
                logs.append(log)
                if execute([sys.executable, '-c', 'from server import db; db.init_db()'], env, log=log, timeout=90):
                    raise RunnerError('Database initialization failed; see worker service.log')
                for attempt in range(3):
                    service = start_service(env, log)
                    processes.append((service, entry))
                    entry.update(service_pid=getattr(service, 'pid', None))
                    if entry['service_pid'] is not None:
                        import psutil
                        try:
                            entry['service_created_at'] = psutil.Process(service.pid).create_time()
                        except psutil.NoSuchProcess:
                            # A bind failure may exit before process inspection; readiness handles retry.
                            entry['service_created_at'] = None
                    entry['cleanup']['service'] = 'pending'
                    save(manifest_path, summary)
                    try:
                        wait_for_service(service, env)
                        break
                    except RunnerError:
                        # Only bind failures (process exits) may get another owned port.
                        if service.poll() is None or attempt == 2:
                            raise
                        stop_process(service)
                        env['BASE_URL'] = bound['BASE_URL'] = f'http://127.0.0.1:{free_port()}'
                save(manifest_path, summary)
            sampler = threading.Thread(target=sample_connections, daemon=True)
            sampler.start()
            base.update(TEST_ENVIRONMENT_MANIFEST=str(manifest_path), TEST_PARALLEL_WORKERS=str(workers))
        summary['timings']['prepare'] = time.monotonic() - started
        phase = 'execute'
        started = time.monotonic()
        suites = ['unit'] if args.suite == 'unit' else ['api'] if args.suite == 'api' else ['unit', 'api', 'ui']
        command = [sys.executable, '-m', 'pytest', '-p', 'test_support.environment_plugin', *suites,
                   '-o', 'addopts=', f'--junitxml={output / "junit.xml"}']
        if workers > 1:
            command += ['-n', str(workers), '--dist', 'load', '--max-worker-restart=0']
        if args.suite == 'all':
            command += ['--screenshot', 'only-on-failure', '--tracing', 'retain-on-failure', f'--output={output / "ui"}']
        command += extras
        summary['command'] = command
        summary['pytest_exitcode'] = execute(command, env=base)
        summary['exitcode'] = summary['pytest_exitcode']
        if (output / 'junit.xml').exists():
            suites_xml = ET.parse(output / 'junit.xml').getroot()
            summary['results'] = {k: sum(int(e.get(k, 0)) for e in suites_xml.findall('testsuite')) for k in ['tests', 'failures', 'errors', 'skipped']}
    except KeyboardInterrupt:
        summary['exitcode'] = 130
        summary['errors'].append({'phase': phase, 'type': 'KeyboardInterrupt'})
    except Exception as exc:
        error = {'phase': phase, 'type': type(exc).__name__}
        if isinstance(exc, RunnerError):
            error['detail'] = str(exc)
        summary['errors'].append(error)
        print(f'{phase} failed ({type(exc).__name__}); see {output}', file=sys.stderr)
    finally:
        summary['timings'][phase] = time.monotonic() - started
        cleanup_start = time.monotonic()
        sampling_stop.set()
        if sampler:
            sampler.join(timeout=20)
        journal = Path(summary['test_process_manifest'])
        test_cleanup_errors = []
        if journal.exists():
            test_cleanup_errors = stop_registered(json.loads(journal.read_text(encoding='utf8')))
        summary['test_process_cleanup'] = 'failed' if test_cleanup_errors else 'stopped'
        if test_cleanup_errors:
            summary['errors'].append({'phase': 'cleanup_tests', 'type': 'ProcessCleanupFailed'})
            summary['exitcode'] = summary['exitcode'] or 2
        # Stop every service before dropping any database.
        for process, entry in reversed(processes):
            try:
                stop_process(process)
                if entry['cleanup']['service'] != 'failed':
                    entry['cleanup']['service'] = 'stopped'
            except (Exception, KeyboardInterrupt) as exc:
                entry['cleanup']['service'] = 'failed'
                summary['errors'].append({'phase': 'cleanup_service', 'type': type(exc).__name__})
        for entry in reversed(summary['environments']):
            if (entry['cleanup']['database'] == 'pending' and entry['cleanup']['service'] != 'failed'
                    and not test_cleanup_errors):
                try:
                    drop_database({**base, **entry['env']})
                    entry['cleanup']['database'] = 'removed'
                except (Exception, KeyboardInterrupt) as exc:
                    entry['cleanup']['database'] = 'failed'
                    summary['errors'].append({'phase': 'cleanup_database', 'type': type(exc).__name__})
        for log in logs:
            log.close()
        if summary['environments']:
            summary['cleanup'] = {kind: ('failed' if any(e['cleanup'][kind] in ['failed', 'pending'] for e in summary['environments']) else summary['environments'][0]['cleanup'][kind]) for kind in ['database', 'service']}
        summary['timings']['cleanup'] = time.monotonic() - cleanup_start
        summary['total_seconds'] = time.monotonic() - total_start
        if 'failed' in summary['cleanup'].values():
            summary['exitcode'] = summary['exitcode'] or 2
        save(manifest_path, summary)
        save(output / 'summary.json', summary)
        print(f'Run {label}: exit {summary["exitcode"]}; summary: {output / "summary.json"}')
    return summary['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
