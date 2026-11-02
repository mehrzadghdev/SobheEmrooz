"""Exercise client navigation and offline installation against disposable data."""
import os
import subprocess
import sys
from pathlib import Path
from threading import Thread

from werkzeug.serving import WSGIRequestHandler, make_server

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from sobhe.models import Advertisement, db
from tests.support import AppTestCase


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        if isinstance(code, int) and code >= 500:
            super().log_request(code, size)


AppTestCase.setUpClass()
fixture = AppTestCase()
fixture.setUp()
server = None
try:
    fixture.app.config['CSRF_ENABLED'] = True
    fixture.add_ad(ma_reference=7001)
    server = make_server('127.0.0.1', 0, fixture.app, threaded=True, request_handler=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = subprocess.run(
        ['node', '--test', '--test-concurrency=1', 'tests/browser/app.test.cjs'], cwd=root,
        env={**os.environ, 'SOBHE_BROWSER_URL': f'http://127.0.0.1:{server.server_port}'},
    )
    if result.returncode == 0:
        db.session.expire_all()
        saved = Advertisement.query.filter_by(ma_reference=8101).one()
        assert saved.volume_units == 1000 and saved.file_key, 'Browser submission did not persist the expected record and file'
        assert Advertisement.query.filter_by(ma_reference=8101).count() == 1, 'A browser submission was repeated'
    raise SystemExit(result.returncode)
finally:
    if server:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    fixture.tearDown()
