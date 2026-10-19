"""Optional, process-first, read-only LCU gameflow adapter.

Credentials stay in this process. No account fields or tokens are returned to UI.
Discovery is throttled and executed without a visible PowerShell window.
"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import time
from hexgod.game.http import get_local_json
from hexgod.platform.processes import league_process_paths

PHASES = {'None', 'Lobby', 'Matchmaking', 'ReadyCheck', 'ChampSelect', 'GameStart',
          'InProgress', 'Reconnect', 'WaitingForStats', 'PreEndOfGame', 'EndOfGame',
          'TerminatedInError'}


def parse_auth(command):
    values = {}
    for key in ('app-port', 'remoting-auth-token'):
        match = re.search(r'--' + key + r'(?:=|\s+)(?:"([^"\r\n]+)"|([^\s"]+))', command or '')
        if match:
            values[key] = match.group(1) or match.group(2)
    port = values.get('app-port', '')
    token = values.get('remoting-auth-token', '')
    if port.isdecimal() and 1 <= int(port) <= 65535 and token and ':' not in token:
        return int(port), token
    return None


def parse_lockfile(text):
    parts = text.strip().split(':')
    if len(parts) != 5 or parts[4] != 'https' or not parts[1].isdigit():
        return None
    return parse_auth('--app-port=' + parts[2] + ' --remoting-auth-token=' + parts[3])


def discover_auth():
    if os.name != 'nt':
        return None
    # Toolhelp + limited image lookup remains available when WMI command-line
    # access is denied. Prefer each running process's nearby lockfile.
    auth = auth_from_paths(league_process_paths())
    if auth:
        return auth
    script = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
              'Get-CimInstance Win32_Process -Filter "Name=\'LeagueClientUx.exe\' OR '
              'Name=\'LeagueClient.exe\'" | Select-Object CommandLine,ExecutablePath | ConvertTo-Json -Compress')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        records = json.loads(result.stdout.decode('utf-8-sig', errors='replace') or '[]')
        records = records if isinstance(records, list) else [records]
        for record in records:
            if not isinstance(record, dict):
                continue
            auth = parse_auth(record.get('CommandLine'))
            if auth:
                return auth
        for record in records:
            if not isinstance(record, dict) or not record.get('ExecutablePath'):
                continue
            directory = Path(record['ExecutablePath']).parent
            for candidate in (directory / 'lockfile', directory.parent / 'lockfile'):
                try:
                    auth = parse_lockfile(candidate.read_text(encoding='utf-8'))
                    if auth:
                        return auth
                except (OSError, ValueError):
                    pass
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def auth_from_paths(paths):
    for image in paths:
        directory = Path(image).parent
        for candidate in (directory / 'lockfile', directory.parent / 'lockfile',
                          directory.parent / 'LeagueClient' / 'lockfile'):
            try:
                auth = parse_lockfile(candidate.read_text(encoding='utf-8'))
                if auth:
                    return auth
            except (OSError, ValueError):
                pass
    return None


class LcuClient:
    def __init__(self, discovery=discover_auth, get=get_local_json, clock=time.monotonic):
        self.discovery, self.get, self.clock = discovery, get, clock
        self.auth = None
        self.next_discovery = 0

    def read_state(self):
        now = self.clock()
        if self.auth is None and now >= self.next_discovery:
            self.next_discovery = now + 15
            self.auth = self.discovery()
        if self.auth is None:
            return {'available': False, 'connection': 'client_credentials_unavailable'}
        port, token = self.auth
        authorization = 'Basic ' + base64.b64encode(('riot:' + token).encode()).decode('ascii')
        try:
            phase = self.get(port, '/lol-gameflow/v1/gameflow-phase', authorization=authorization)
            if not isinstance(phase, str) or phase not in PHASES:
                return {'available': False}
        except (OSError, ValueError, TimeoutError):
            self.auth = None
            return {'available': False, 'connection': 'client_api_unreachable'}
        state = {'available': True, 'phase': phase}
        if phase in {'ChampSelect', 'GameStart', 'InProgress', 'Reconnect'}:
            try:
                session = self.get(port, '/lol-gameflow/v1/session', authorization=authorization)
                game = session.get('gameData') if isinstance(session, dict) else None
                game = game if isinstance(game, dict) else {}
                queue = game.get('queue')
                queue = queue if isinstance(queue, dict) else {}
                if type(game.get('gameId')) is int and game['gameId'] > 0:
                    state['gameId'] = str(game['gameId'])
                if type(queue.get('id')) is int:
                    state['queueId'] = queue['id']
                if isinstance(queue.get('gameMode'), str):
                    state['gameMode'] = queue['gameMode']
            except (OSError, ValueError, TimeoutError):
                pass  # An unavailable optional session must not erase a valid phase.
        return state
