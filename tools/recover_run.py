"""显式指定运行清单恢复回收，绝不扫描测试前缀数据库。"""
import argparse
import json
from pathlib import Path
import psutil
from tools import environments as lifecycle
from tools.processes import stop_registered


def recover(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf8'))
    if not data.get('run_id') or not data.get('environments'):
        raise ValueError('Not an owned run manifest')
    credentials = lifecycle.load_environment()
    failures = []
    # Validate the entire list before making any changes.
    for entry in data['environments']:
        env = entry['env']
        lifecycle.owned_database_name(env)
        if env.get('TEST_RUN_ID') != data['run_id'] or not env.get('TEST_ENVIRONMENT_ID'):
            raise ValueError('Manifest identity mismatch')
    journal = data.get('test_process_manifest')
    if journal:
        journal = Path(journal).resolve()
        if journal.parent != path.parent:
            raise ValueError('Test process journal must belong to this run directory')
        if journal.exists():
            failures = stop_registered(json.loads(journal.read_text(encoding='utf8')))
            if failures:
                data['recovery_errors'] = failures
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')
                return 2
    for entry in reversed(data['environments']):
        status = entry['cleanup']
        try:
            if status['service'] in ('pending', 'failed'):
                pid = entry.get('service_pid')
                if pid is not None and psutil.pid_exists(pid):
                    process = psutil.Process(pid)
                    command = process.cmdline()
                    # PID reuse or an edited process target fails closed.
                    if (process.create_time() != entry.get('service_created_at') or
                        'server.main:app' not in command or '--port' not in command or
                        command[command.index('--port') + 1] != entry['env']['BASE_URL'].rsplit(':', 1)[1]):
                        raise ValueError('Service process identity mismatch; refusing cleanup')
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except psutil.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                status['service'] = 'stopped'
            if status['database'] in ('pending', 'failed') and status['service'] != 'failed':
                env = {**credentials, **entry['env']}
                # An already absent owned database counts as recovered; no prefix enumeration.
                conn = lifecycle.database_connection(env)
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (env['DB_NAME'],))
                        exists = bool(cur.fetchone())
                finally:
                    conn.close()
                if exists:
                    lifecycle.drop_database(env)
                status['database'] = 'removed'
        except Exception as exc:
            failures.append({'worker': entry['worker'], 'type': type(exc).__name__})
    data['recovery_errors'] = failures
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')
    return 2 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    return recover(args.manifest)


if __name__ == '__main__':
    raise SystemExit(main())
