"""真实双环境隔离探针。复用正式运行器的登记、启动和回收，不另造生命周期。

运行：python -m tools.verify_e2 --run-id e2-isolation
输出：reports/engineering/<run-id>/isolation.json 及正式运行器资源摘要。
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid
import os
import socket
import tempfile
import threading
import _thread

import requests

from api.common import db_util
from test_support.scenarios import TestData, wait_until
from tools import run_tests
from tools.environments import load_environment


def verify_absent(manifest):
    """只核查指定清单中的库和进程，不按前缀枚举。"""
    import psutil
    from tools.environments import database_connection
    conn = database_connection(load_environment())
    results = []
    try:
        with conn.cursor() as cur:
            for entry in manifest['environments']:
                name = entry['env']['DB_NAME']
                cur.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
                absent = cur.fetchone()[0] == 0
                pid = entry.get('service_pid')
                running = False
                if pid and psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    running = proc.create_time() == entry.get('service_created_at') and proc.is_running()
                results.append({'worker': entry['worker'], 'database_absent': absent, 'service_absent': not running})
    finally:
        conn.close()
    return results


def run_fault(args):
    """受控故障在正式资源管理器内发生；期望故障本身不会被记成通过回归。"""
    evidence = []
    original_execute = run_tests.execute
    original_free_port = run_tests.free_port
    original_drop = run_tests.drop_database
    base = load_environment()
    stop = threading.Event()
    watcher = None
    with ExitStack() as stack:
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='shophub_e2_probe_'))
        folder = Path(temporary)
        marker = folder / 'entered.txt'
        if args.fault in ('crash', 'interrupt'):
            module = 'e2_probe_' + uuid.uuid4().hex
            code = ('import os, time\nfrom pathlib import Path\n'
                    'def pytest_runtest_call(item):\n'
                    f'    Path({str(marker)!r}).write_text("entered")\n'
                    + ('    os._exit(17)\n' if args.fault == 'crash' else '    time.sleep(60)\n'))
            (folder / (module + '.py')).write_text(code, encoding='utf8')
            base.update(PYTEST_PLUGINS=module, PYTHONPATH=str(folder) + os.pathsep + str(run_tests.ROOT))
            if args.fault == 'interrupt':
                def interrupt_when_running():
                    while not stop.wait(.05):
                        if marker.exists():
                            _thread.interrupt_main()
                            return
                watcher = threading.Thread(target=interrupt_when_running, daemon=True)
                watcher.start()
        if args.fault == 'port':
            listener = stack.enter_context(socket.socket())
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            calls = 0
            def port():
                nonlocal calls
                calls += 1
                return occupied if calls == 1 else original_free_port()
            stack.enter_context(patch.object(run_tests, 'free_port', port))
        if args.fault == 'init':
            calls = 0
            def fail_second_init(command, env, **kwargs):
                nonlocal calls
                if 'pytest' not in command:
                    calls += 1
                    if calls == 2:
                        return 42
                return original_execute(command, env, **kwargs)
            stack.enter_context(patch.object(run_tests, 'execute', fail_second_init))
        if args.fault == 'cleanup':
            failed = False
            def fail_once(env):
                nonlocal failed
                if not failed:
                    failed = True
                    raise RuntimeError('deliberately injected drop failure')
                return original_drop(env)
            stack.enter_context(patch.object(run_tests, 'drop_database', fail_once))
        stack.enter_context(patch.object(run_tests, 'load_environment', lambda: base.copy()))
        try:
            code = run_tests.main(['--suite', 'api', '--workers', '2', '--run-id', args.run_id,
                                   '--output-dir', str(args.output_dir), '--', '-q', '-k',
                                   'test_cart_isolation_with_owned_resources'])
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=5)
        injection_triggered = (marker.exists() if args.fault in ('crash', 'interrupt')
                               else calls >= 3 if args.fault == 'port'
                               else calls >= 2 if args.fault == 'init' else failed)
    output = args.output_dir / args.run_id
    manifest_path = output / 'resources.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    expected = code == 0 if args.fault == 'port' else code != 0
    record_check(evidence, 'fault_injection_triggered', injection_triggered)
    record_check(evidence, 'expected_exit', expected, {'runner_exitcode': code})
    if args.fault == 'interrupt':
        record_check(evidence, 'interrupted_during_test_execution', code == 130
                     and any(e['phase'] == 'execute' and e['type'] == 'KeyboardInterrupt' for e in manifest['errors']))
    if args.fault == 'cleanup':
        record_check(evidence, 'failed_cleanup_is_recorded', manifest['cleanup']['database'] == 'failed')
        from tools.recover_run import recover
        recovery_code = recover(manifest_path)
        record_check(evidence, 'explicit_manifest_recovery', recovery_code == 0)
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    resources = verify_absent(manifest)
    record_check(evidence, 'owned_resources_absent', all(e['database_absent'] and e['service_absent'] for e in resources),
                 {'resources': resources})
    (output / 'fault-evidence.json').write_text(json.dumps({'fault': args.fault, 'checks': evidence}, indent=2), encoding='utf8')
    return 0


def record_check(evidence, name, passed, observed=None):
    entry = {**(observed or {}), 'name': name, 'passed': bool(passed)}
    evidence.append(entry)
    if not passed:
        raise AssertionError('E2 isolation probe failed: ' + name)


def connect_bound(entry):
    env = load_environment()
    return db_util.connect(host=env.get('DB_HOST', '127.0.0.1'),
                           port=int(env.get('DB_PORT', '3306')),
                           user=env.get('DB_USER', 'root'),
                           password=env.get('DB_PASSWORD', ''),
                           database=entry['env']['DB_NAME'])


def run_probe(manifest, evidence):
    entries = manifest['environments']
    record_check(evidence, 'distinct_environments',
                 len(entries) == 2 and len({e['env']['DB_NAME'] for e in entries}) == 2
                 and len({e['env']['BASE_URL'] for e in entries}) == 2)
    with ExitStack() as stack:
        stores = []
        for entry in entries:
            conn = connect_bound(entry)
            stack.callback(conn.close)
            db = db_util.DB(conn)
            actual = db.scalar('SELECT DATABASE()')
            record_check(evidence, 'database_binding_' + entry['worker'], actual == entry['env']['DB_NAME'], {'database': actual})
            data = TestData(entry['env']['BASE_URL'], db)
            # Added after connection callbacks, hence cleanup happens before connections close.
            stack.callback(data.cleanup)
            stores.append((data, db))
        (a, adb), (b, bdb) = stores
        abuyer, bbuyer = a.buyer(), b.buyer()
        ap = a.product(a.seller(), stock=10)
        bp = b.product(b.seller(), stock=10)
        ao = a.order(abuyer, [(ap, 1)])
        bo = b.order(bbuyer, [(bp, 1)])
        assert abuyer.client.set_payment_mode('fail').status_code == 200
        failed = abuyer.client.order_pay(ao.order_id)
        paid = bbuyer.client.order_pay(bo.order_id)
        astate = adb.scalar('SELECT status FROM orders WHERE order_id=%s', (ao.order_id,))
        bstate = bdb.scalar('SELECT status FROM orders WHERE order_id=%s', (bo.order_id,))
        record_check(evidence, 'payment_mode_isolation',
                     failed.json()['status'] == 'failed' and paid.json()['status'] == 'paid'
                     and astate == 'canceled' and bstate == 'paid', {'A': astate, 'B': bstate})
        assert abuyer.client.set_payment_mode('success').status_code == 200
        apending = a.order(abuyer, [(ap, 1)])
        bpending = b.order(bbuyer, [(bp, 1)])
        assert abuyer.client.cancel_expired(seconds=0).status_code == 200
        astate = adb.scalar('SELECT status FROM orders WHERE order_id=%s', (apending.order_id,))
        bstate = bdb.scalar('SELECT status FROM orders WHERE order_id=%s', (bpending.order_id,))
        record_check(evidence, 'cancel_scan_isolation', astate == 'canceled' and bstate == 'pending', {'A': astate, 'B': bstate})

        # A real HTTP200/business-failure must not qualify as a paid precondition.
        with TestData(a.base_url, adb) as interrupted:
            broken_buyer = interrupted.buyer()
            broken_product = interrupted.product(interrupted.seller())
            assert broken_buyer.client.set_payment_mode('fail').status_code == 200
            rejected = False
            try:
                interrupted.order(broken_buyer, [(broken_product, 1)], state='paid')
            except AssertionError as exc:
                rejected = '支付业务失败' in str(exc)
            observed_state = adb.scalar('SELECT status FROM orders WHERE username=%s', (broken_buyer.username,))
            record_check(evidence, 'http200_business_failure_rejected', rejected and observed_state == 'canceled',
                         {'state_after_failed_payment': observed_state})
            assert broken_buyer.client.set_payment_mode('success').status_code == 200
            names = interrupted.usernames
        record_check(evidence, 'failed_preparation_resources_removed', all(
            adb.scalar('SELECT COUNT(*) FROM users WHERE username=%s', (name,)) == 0 for name in names))

        # Observe a real pending order while deliberately requiring impossible state.
        before = adb.scalar('SELECT COUNT(*) FROM orders WHERE username=%s', (abuyer.username,))
        expired = False
        try:
            wait_until(lambda: adb.scalar('SELECT status FROM orders WHERE order_id=%s', (apending.order_id,)) == 'invented',
                       timeout=.05, interval=.01)
        except AssertionError:
            expired = True
        after = adb.scalar('SELECT COUNT(*) FROM orders WHERE username=%s', (abuyer.username,))
        record_check(evidence, 'state_wait_deadline_read_only', expired and before == after,
                     {'orders_before': before, 'orders_after': after})

        # Client gives up at 1s; service must still report processing until its 6s delay ends.
        timeout_order = a.order(abuyer, [(ap, 1)])
        assert abuyer.client.set_payment_mode('timeout').status_code == 200
        began = time.monotonic()
        timed_out = False
        try:
            abuyer.client._request('POST', f'/order/{timeout_order.order_id}/pay',
                                   headers=abuyer.client._headers(), timeout=1)
        except requests.ReadTimeout:
            timed_out = True
        state_url = a.base_url + '/debug/payment-state'
        active = requests.get(state_url, timeout=2).json()['in_flight']
        record_check(evidence, 'client_timeout_does_not_finish_service', timed_out and active == 1,
                     {'in_flight_after_client_timeout': active})
        wait_until(lambda: requests.get(state_url, timeout=2).json()['in_flight'] == 0, timeout=15)
        elapsed = time.monotonic() - began
        assert abuyer.client.set_payment_mode('success').status_code == 200
        record_check(evidence, 'payment_processing_finished_before_cleanup', elapsed >= 5,
                     {'elapsed_seconds': elapsed, 'in_flight': 0})
        a.cleanup()
        survivor = {
            'user': bdb.scalar('SELECT COUNT(*) FROM users WHERE username=%s', (bbuyer.username,)),
            'product': bdb.scalar('SELECT COUNT(*) FROM products WHERE id=%s', (bp.product_id,)),
            'order': bdb.scalar('SELECT COUNT(*) FROM orders WHERE order_id=%s', (bpending.order_id,)),
        }
        record_check(evidence, 'cleanup_isolation', all(x == 1 for x in survivor.values()), survivor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='isolation_' + uuid.uuid4().hex[:10])
    parser.add_argument('--output-dir', type=Path, default=run_tests.ROOT / 'reports' / 'engineering')
    parser.add_argument('--fault', choices=['init', 'port', 'crash', 'interrupt', 'cleanup'])
    args = parser.parse_args(argv)
    if args.fault:
        return run_fault(args)
    evidence = []
    original_execute = run_tests.execute
    def probe_execute(command, env, **kwargs):
        if 'pytest' not in command:
            return original_execute(command, env, **kwargs)
        manifest = json.loads(Path(env['TEST_ENVIRONMENT_MANIFEST']).read_text(encoding='utf8'))
        run_probe(manifest, evidence)
        return 0
    with patch.object(run_tests, 'execute', probe_execute):
        code = run_tests.main(['--suite', 'api', '--workers', '2', '--run-id', args.run_id,
                               '--output-dir', str(args.output_dir)])
    output = args.output_dir / args.run_id
    report = {'command': [sys.executable, '-m', 'tools.verify_e2', *(argv or sys.argv[1:])],
              'exitcode': code, 'checks': evidence,
              'note': 'Real database/service probe; does not count as full pytest regression.'}
    (output / 'isolation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
