"""Build source and portable Windows ZIPs from an explicit source allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from package_vscode_bots import package as package_vsix

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.01'
PYTHON_VERSION = '3.13.15'
PYTHON_URL = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip'
PYTHON_SHA256 = 'd1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf'
WHEEL_HASHES = {
    'pyte-0.8.2-py3-none-any.whl': '85db42a35798a5aafa96ac4d8da78b090b2c933248819157fc0e6f78876a0135',
    'pywinpty-3.0.5-cp313-cp313-win_amd64.whl': '48db1b0ad9d0a1b81dcaaa7163a99a7808deaceb0c1b2344716dc1fc090c3c4c',
    'wcwidth-0.8.3-py3-none-any.whl': 'd5b73dba6158a595ec9370350e7f2637bcac8d6c5e4fde34f30fcffb6103a5e4',
}
PYTE_SOURCE_URL = 'https://files.pythonhosted.org/packages/ab/ab/b599762933eba04de7dc5b31ae083112a6c9a9db15b01d3109ad797559d9/pyte-0.8.2.tar.gz'
PYTE_SOURCE_SHA256 = '5af970e843fa96a97149d64e170c984721f20e52227a2f57f0a54207f08f083f'
SOURCE_DIRS = ('app', 'scripts', 'skills', 'extensions', 'tests', 'docs', 'launchers', '.github')
ROOT_FILES = ('orchestrator.py', 'README.md', 'requirements.txt', '.gitignore',
              'Run Python.cmd', 'Setup.cmd', 'Orchestrator.cmd', 'THIRD-PARTY-NOTICES.md')
SUFFIXES = {'.py', '.js', '.cjs', '.json', '.html', '.md', '.cmd', '.ps1', '.yaml', '.yml', '.txt'}
EXCLUDED_PARTS = {'__pycache__', 'node_modules', '.git', '.claude', '.orchestration',
                  'runtime', 'runs', 'archive', 'vendor', '.venv'}
EXCLUDED_FILES = {'settings.local.json', 'validation.json', 'credentials.json',
                  'secrets.json', 'workers.json', 'ollama-profile.json', 'policy.json'}


def source_files(root=ROOT):
    root = Path(root).resolve()
    candidates = [root / name for name in ROOT_FILES]
    candidates.extend(root.glob('*.cmd'))
    for directory in SOURCE_DIRS:
        base = root / directory
        if base.is_symlink() or base.is_junction():
            raise ValueError('Source directory cannot be a link: ' + directory)
        if base.exists():
            candidates.extend(base.rglob('*'))
    selected = set()
    for file in candidates:
        relative = file.relative_to(root)
        if set(relative.parts) & EXCLUDED_PARTS or file.name.lower() in EXCLUDED_FILES:
            continue
        if file.is_symlink() or file.is_junction() or not file.resolve().is_relative_to(root):
            raise ValueError('Source link rejected: ' + relative.as_posix())
        if not file.is_file() or (file.suffix not in SUFFIXES and file.name != '.gitignore'):
            continue
        if file.stat().st_size > 5 * 1024 * 1024:
            raise ValueError('Unexpected large source file: ' + relative.as_posix())
        selected.add(relative)
    return sorted(selected)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extract_checked(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive member')
        source.extractall(destination)


def zip_tree(tree, output):
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(tree.rglob('*')):
            if file.is_file():
                archive.write(file, Path('Agent-Orchestrator') / file.relative_to(tree))


def build(output, cache, source_only=False):
    output, cache = Path(output).resolve(), Path(cache).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    source_zip = output / f'Agent-Orchestrator-{VERSION}-source.zip'
    portable_zip = output / f'Agent-Orchestrator-{VERSION}-windows-x64.zip'
    for target in (source_zip,) if source_only else (source_zip, portable_zip):
        if target.exists():
            raise FileExistsError('Refusing to overwrite ' + str(target))
    selected = source_files()
    with tempfile.TemporaryDirectory(prefix='orchestrator-package-') as work:
        tree = Path(work) / 'Agent-Orchestrator'
        tree.mkdir()
        for relative in selected:
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        zip_tree(tree, source_zip)
        if not source_only:
            runtime_zip = cache / f'python-{PYTHON_VERSION}-embed-amd64.zip'
            if not runtime_zip.exists():
                with urllib.request.urlopen(PYTHON_URL, timeout=60) as response:
                    runtime_zip.write_bytes(response.read())
            if digest(runtime_zip) != PYTHON_SHA256:
                raise ValueError('Official Python runtime checksum mismatch')
            extract_checked(runtime_zip, tree / 'python')
            (tree / 'python/python313._pth').write_text(
                'python313.zip\n.\n..\n../app\n../scripts\n../vendor/quota\n', encoding='utf-8')
            wheels = cache / 'wheels'
            wheels.mkdir(exist_ok=True)
            subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                            '--platform', 'win_amd64', '--python-version', '313',
                            '--implementation', 'cp', '--abi', 'cp313', '--dest', str(wheels),
                            '-r', str(ROOT / 'requirements.txt')], check=True)
            for name, expected in WHEEL_HASHES.items():
                if digest(wheels / name) != expected:
                    raise ValueError('Dependency wheel checksum mismatch: ' + name)
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-compile',
                            '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '313',
                            '--implementation', 'cp', '--abi', 'cp313', '--find-links', str(wheels),
                            '--target', str(tree / 'vendor/quota'), '-r', str(ROOT / 'requirements.txt')], check=True)
            pyte_source = cache / 'pyte-0.8.2.tar.gz'
            if not pyte_source.exists():
                with urllib.request.urlopen(PYTE_SOURCE_URL, timeout=60) as response:
                    pyte_source.write_bytes(response.read())
            if digest(pyte_source) != PYTE_SOURCE_SHA256:
                raise ValueError('pyte corresponding source checksum mismatch')
            (tree / 'third-party/sources').mkdir(parents=True)
            shutil.copyfile(pyte_source, tree / 'third-party/sources/pyte-0.8.2.tar.gz')
            extension_version = json.loads((tree / 'extensions/vscode-bots/package.json').read_text(encoding='utf-8'))['version']
            package_vsix(tree / f'extensions/agent-orchestrator-bots-{extension_version}.vsix')
            manifest = {'version': VERSION, 'platform': 'windows-x64',
                        'python': {'version': PYTHON_VERSION, 'url': PYTHON_URL, 'sha256': PYTHON_SHA256},
                        'dependency_wheels_sha256': WHEEL_HASHES,
                        'files': {p.relative_to(tree).as_posix(): digest(p) for p in sorted(tree.rglob('*')) if p.is_file()}}
            (tree / 'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            zip_tree(tree, portable_zip)
    artifacts = [source_zip] + ([] if source_only else [portable_zip])
    checksums = output / 'SHA256SUMS.txt'
    checksums.write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in artifacts), encoding='utf-8')
    return {'artifacts': [str(p) for p in artifacts], 'source_files': len(selected), 'checksums': str(checksums)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--cache', type=Path, default=ROOT / '.build-cache')
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.cache, args.source_only), indent=2))
