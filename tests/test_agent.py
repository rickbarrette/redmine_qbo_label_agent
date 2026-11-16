import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from label_agent.__main__ import Agent


class FakeRedmine:
    """Redmine's label agent API: hands out the given jobs, records results"""

    def __init__(self, jobs, token='qbl_test'):
        self.jobs = list(jobs)
        self.results = {}
        self.polls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'] or 0)) or b'{}')
                if self.headers.get('Authorization') != f'Bearer {token}':
                    return self.reply(401, {'error': 'unauthorized'})
                if self.path == '/label_agent/poll':
                    outer.polls.append(body)
                    job = outer.jobs.pop(0) if outer.jobs else None
                    return self.reply(200, {'job': job, 'poll_seconds': 0.2})
                if self.path.startswith('/label_agent/jobs/'):
                    outer.results[int(self.path.split('/')[3])] = body
                    return self.reply(200, {'ok': True})
                self.reply(404, {})

            def reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def run_agent(agent, until, seconds=10):
    thread = threading.Thread(target=agent.run, daemon=True)
    thread.start()
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        time.sleep(0.05)
    agent.stop()
    thread.join(5)


def test_prints_jobs_and_reports_back(fake_printer):
    printer = fake_printer()
    redmine = FakeRedmine([
        {'id': 7, 'lines': ['SMITH', 'E1234'], 'copies': 1, 'max_length_mm': 101.6, 'min_length_mm': 25.4},
        {'id': 8, 'lines': [], 'copies': 1},
    ])
    agent = Agent({'REDMINE_URL': redmine.url, 'AGENT_TOKEN': 'qbl_test',
                   'PRINTER': f'tcp://127.0.0.1:{printer.port}'})
    run_agent(agent, lambda: len(redmine.results) == 2)
    redmine.close()

    assert redmine.results[7] == {'status': 'printed'}
    assert redmine.results[8]['status'] == 'failed'
    # The first poll tells Redmine about the printer
    assert redmine.polls[0]['agent']['printer_address'] == f'127.0.0.1:{printer.port}'
    assert redmine.polls[0]['agent']['printer_status'] == 'Ready, 62mm continuous'
    # ... and later polls don't repeat it
    assert 'agent' not in redmine.polls[-1]


def test_unreachable_printer_fails_the_job():
    redmine = FakeRedmine([{'id': 9, 'lines': ['SMITH', 'W42'], 'copies': 1}])
    agent = Agent({'REDMINE_URL': redmine.url, 'AGENT_TOKEN': 'qbl_test', 'PRINTER': 'tcp://127.0.0.1:1'})
    run_agent(agent, lambda: 9 in redmine.results)
    redmine.close()
    assert redmine.results[9]['status'] == 'failed'
    assert "Can't reach the printer" in redmine.results[9]['error']


def test_wrong_token_keeps_running_without_printing():
    redmine = FakeRedmine([{'id': 1, 'lines': ['X'], 'copies': 1}], token='qbl_other')
    agent = Agent({'REDMINE_URL': redmine.url, 'AGENT_TOKEN': 'qbl_test', 'PRINTER': 'tcp://127.0.0.1:1'})
    run_agent(agent, lambda: False, seconds=1)
    redmine.close()
    assert redmine.jobs and not redmine.results
