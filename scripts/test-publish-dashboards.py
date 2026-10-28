"""Run with python3 scripts/test-publish-dashboards.py; no network or real token."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PublishDashboardsTest(unittest.TestCase):
    def publish(self, missing=None):
        sources = [
            {'uid': 'prometheus', 'type': 'prometheus', 'name': 'Prometheus', 'isDefault': True},
            {'uid': 'grafanacloud-alert-state-history', 'type': 'loki', 'name': 'Alert history'},
        ]
        for kind, uid in [('prometheus', 'grafanacloud-prom'), ('loki', 'grafanacloud-logs'), ('tempo', 'grafanacloud-traces')]:
            if uid != missing:
                sources.append({'uid': uid, 'type': kind, 'name': uid})
        writes = []

        class Opener:
            def open(self, request, timeout):
                if request.data is None:
                    return io.BytesIO(json.dumps(sources).encode())
                writes.append(json.loads(request.data))
                return io.BytesIO(b'{"url":"/d/test"}')

        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('synthetic-test-token')
            token.chmod(0o600)
            with patch('sys.argv', ['publish-dashboards.py', '--token-file', str(token)]), \
                 patch('urllib.request.build_opener', return_value=Opener()), \
                 contextlib.redirect_stdout(io.StringIO()):
                try:
                    runpy.run_path(str(ROOT / 'scripts/publish-dashboards.py'), run_name='__main__')
                except SystemExit:
                    self.assertEqual(writes, [])
                    raise
        return writes

    def test_cloud_sources_win_over_default_and_alert_history(self):
        writes = self.publish()
        self.assertEqual({w['dashboard']['uid'] for w in writes}, {'usbharulab-infra', 'usbharulab-ocms'})
        for write in writes:
            selected = {v['name']: v['current']['value'] for v in write['dashboard']['templating']['list'] if v['type'] == 'datasource'}
            self.assertEqual(selected, {'metrics': 'grafanacloud-prom', 'logs': 'grafanacloud-logs', 'traces': 'grafanacloud-traces'})

    def test_missing_cloud_source_stops_before_any_write(self):
        for uid in ['grafanacloud-prom', 'grafanacloud-logs', 'grafanacloud-traces']:
            with self.subTest(uid=uid), self.assertRaisesRegex(SystemExit, uid):
                self.publish(missing=uid)


if __name__ == '__main__':
    unittest.main()
