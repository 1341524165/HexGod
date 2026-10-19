"""Bounded GET requests to Riot's loopback APIs; never follow redirects or proxies."""
import json
import ssl
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_local_json(port, path, *, authorization=None, timeout=0.8):
    if type(port) is not int or not 1 <= port <= 65535 or not path.startswith('/') or '://' in path:
        raise ValueError('Invalid local API endpoint')
    headers = {'Accept': 'application/json'}
    if authorization:
        headers['Authorization'] = authorization
    request = Request(f'https://127.0.0.1:{port}{path}', headers=headers, method='GET')
    opener = build_opener(ProxyHandler({}), NoRedirect(),
                         HTTPSHandler(context=ssl._create_unverified_context()))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Local API response too large')
    return json.loads(raw)
