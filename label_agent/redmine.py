"""Talks to Redmine's label print API (redmine_qbo_labels' LabelAgentController)."""

import logging

import requests

from . import __version__

log = logging.getLogger(__name__)


class Unauthorized(Exception):
    """Redmine doesn't know this agent's token (wrong, replaced, or the printer is inactive)"""


class Redmine:
    def __init__(self, url, token, verify=True, timeout=15):
        self.url = url.rstrip('/')
        self.timeout = timeout
        # One kept-alive connection: the name is looked up once, not on every poll
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
            'User-Agent': f'redmine-qbo-label-agent/{__version__}',
        })

    def _post(self, path, payload):
        response = self.session.post(f'{self.url}{path}', json=payload, timeout=self.timeout)
        if response.status_code == 401:
            raise Unauthorized('Redmine rejected the agent token')
        response.raise_for_status()
        return response.json()

    def poll(self, agent=None):
        """The next label to print (or None) and how long to wait before asking again"""
        data = self._post('/label_agent/poll', {'agent': agent} if agent else {})
        return data.get('job'), float(data.get('poll_seconds') or 2)

    def report(self, job_id, printed, error=None):
        payload = {'status': 'printed' if printed else 'failed'}
        if error:
            payload['error'] = str(error)[:1000]
        return self._post(f'/label_agent/jobs/{int(job_id)}/result', payload)
