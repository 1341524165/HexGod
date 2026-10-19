"""Read-only, versioned ARAMGG client-data distribution with atomic activation.

Protocol reference: valkia/aramgg_client scripts/fetch-client-data.mjs and
src/main/data-loader.ts. Never uploads games or diagnostic files.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent / 'client-data'
ORIGIN = 'https://data.dtodo.cn'


class TrustedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).netloc != 'data.dtodo.cn' or urlsplit(newurl).scheme != 'https':
            raise ValueError('数据接口重定向到非信任地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(path):
    url = urljoin(ORIGIN, path)
    if urlsplit(url).netloc != 'data.dtodo.cn' or urlsplit(url).scheme != 'https':
        raise ValueError('非信任数据地址')
    error = None
    for _ in range(3):
        try:
            with build_opener(TrustedRedirect()).open(Request(url, headers={'User-Agent': 'hexgod/0.11 (ARAMGG client data)'}), timeout=25) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError('数据文件超出大小限制')
            return data
        except OSError as exc:
            error = exc
    raise error


def logical_path(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+\.json', value):
        raise ValueError('非法数据路径')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError('非法数据路径')
    return path


def sync(root=ROOT):
    config = json.loads(fetch('/api/client/v1/config?locale=zh-CN'))
    version = config['dataVersion']
    if config.get('locale') != 'zh-CN' or not re.fullmatch(r'\d+(?:\.\d+){1,3}', version):
        raise ValueError('数据版本/语言不匹配')
    manifest_bytes = fetch(config['manifest'])
    manifest = json.loads(manifest_bytes)
    if manifest.get('dataVersion') != version or manifest.get('locale') != 'zh-CN':
        raise ValueError('清单版本/语言不匹配')
    folder = root / version
    folder.mkdir(parents=True, exist_ok=True)
    # Champion details are directly addressed; no duplicate shard downloads.
    entries = [e for e in manifest['files'] if e['path'] in ('augments.json', 'champions.json', 'items.json')
               or re.fullmatch(r'champions/\d+\.json', e['path'])]
    def download(entry):
        target = folder.joinpath(*logical_path(entry['path']).parts)
        digest = entry.get('hash', '')
        if not re.fullmatch(r'sha256-[a-f0-9]{64}', digest):
            raise ValueError('缺少 SHA-256 校验')
        data = target.read_bytes() if target.exists() else b''
        if 'sha256-' + hashlib.sha256(data).hexdigest() != digest:
            data = fetch(entry['url'])
        if len(data) != entry['bytes'] or 'sha256-' + hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('数据完整性校验失败：' + entry['path'])
        payload = json.loads(data)
        if payload.get('dataVersion') != version:
            raise ValueError('混合版本数据')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, entries))
    champions = json.loads((folder / 'champions.json').read_bytes())['data']
    if not champions or any(not (folder / 'champions' / f"{row['id']}.json").exists() for row in champions):
        raise ValueError('英雄数据不完整，保留原版本')
    (folder / 'manifest.json').write_bytes(manifest_bytes)
    (folder / 'config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    pointer = {'version': version, 'source': ORIGIN, 'checkedAt': time.time()}
    temp = root / 'current.tmp'
    temp.write_text(json.dumps(pointer), encoding='utf-8')
    temp.replace(root / 'current.json')
    return {'version': version, 'files': len(entries), 'champions': len(champions),
            'augments': len(json.loads((folder / 'augments.json').read_bytes())['data'])}


def active(root=ROOT):
    try:
        version = json.loads((root / 'current.json').read_bytes())['version']
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}', version):
            return None
        folder = root / version
        if all((folder / name).exists() for name in ('config.json', 'augments.json', 'champions.json', 'items.json')):
            return folder
    except (OSError, ValueError, KeyError):
        pass
    return None


if __name__ == '__main__':
    print(json.dumps(sync(), ensure_ascii=False))
