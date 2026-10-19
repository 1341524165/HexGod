"""Register this src-layout project in its bundled, relocatable interpreter."""
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    site = root / 'runtime/Lib/site-packages'
    if not (root / 'runtime/python.exe').is_file():
        raise SystemExit('Bundled runtime/python.exe is missing; see docs/DEVELOPMENT.md.')
    site.mkdir(parents=True, exist_ok=True)
    # Relative to site-packages; moving the whole project preserves this path.
    (site / 'hexgod-src.pth').write_text('../../../src\n', encoding='ascii')
    for directory in ('artifacts/cache','artifacts/reports','build/bin','build/obj','var/logs','var/state'):
        (root/directory).mkdir(parents=True, exist_ok=True)


if __name__ == '__main__':
    main()
