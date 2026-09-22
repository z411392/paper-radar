import ast
import base64
import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = 'z411392/paper-radar'
BASE = '6a030bf8e5c2adfa8183cc36ff7a5a91c0315ff7'
DIGEST = 'ef26c8c245df267957fa5eebabd545c37d98aa01e6be22251ac58e1c30f22fa8'
CHUNKS = (
    'e083413574cb5a119358ec32951132e307566d5f',
    '02e38a8f4577f91cd0c6a74f0f1378be78d2bcc2',
    '0623cf855d986686583a7dbc15fa89b6f172e580',
    '9f41b65bb2b8f9ca8cd6a48766c5c8c04022498a',
    'b08f466bdb1dddfa2fbe6a6c626949e9854b0134',
    'd7ff38cf14df7906bc681999338d59f0422f7e14',
)
DOCS = {'specs/5-local-workspace/plan.md', 'specs/5-local-workspace/progress.md'}
TEMP = ('.github/task8_stage.py', '.github/workflows/task8-stage.yml')
MANIFEST = Path(os.environ['RUNNER_TEMP']) / 'task8-payload.json'


def api(endpoint, body=None):
    args = ['gh', 'api', f'repos/{REPO}/{endpoint}']
    if body is not None:
        args += ['--method', 'POST', '--input', '-']
    out = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                         text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def shape(text):
    tree = ast.parse(text)
    # Only module-level import ordering may change; all executable nodes and
    # nested source strings (including child-process tests) must be identical.
    imports = sorted(ast.dump(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    return imports, ast.dump(tree)


def prepare():
    encoded = b''.join(base64.b64decode(api(f'git/blobs/{sha}')['content']) for sha in CHUNKS)
    raw = gzip.decompress(base64.b64decode(encoded, validate=True))
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise RuntimeError('Payload checksum mismatch; no source written')
    data = json.loads(raw)
    if data['base'] != BASE or set(data['appends']) != DOCS or len(data['files']) != 17:
        raise RuntimeError('Unexpected manifest scope')
    grouped = [p for group in data['groups'].values() for p in group]
    if len(grouped) != len(set(grouped)) or set(grouped) != set(data['files']):
        raise RuntimeError('Subtask partition mismatch')
    for name in data['files']:
        path = Path(name)
        if (path.is_absolute() or '..' in path.parts or path.exists() or path.is_symlink()
                or not (name.startswith('src/libs/watch_profiles/') or name == 'config/domain-seeds.json')):
            raise RuntimeError('Refuse unexpected or existing target: ' + name)
    before = {}
    for name, text in data['files'].items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('.py'):
            before[name] = shape(text)
            # AST-equivalent wrapping of two long string lines in the author tests.
            text = text.replace(
                '"INSERT INTO delivery_outbox VALUES',
                '"INSERT INTO delivery_outbox "\n            "VALUES',
            ).replace(
                'from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import '
                'SqliteWatchProfileStoreAdapter\ndef connect():',
                'from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter '
                '\\\nimport SqliteWatchProfileStoreAdapter\ndef connect():',
            )
            if shape(text) != before[name]:
                raise RuntimeError('Wrapping changed Python semantics: ' + name)
        path.write_text(text, encoding='utf-8')
    python_paths = sorted(before)
    subprocess.run(['uv', 'run', '--locked', '--offline', 'ruff', 'check', '--select', 'I', '--fix',
                    *python_paths], check=True)
    subprocess.run(['uv', 'run', '--locked', '--offline', 'ruff', 'format', *python_paths], check=True)
    for name in before:
        if shape(Path(name).read_text(encoding='utf-8')) != before[name]:
            raise RuntimeError('Formatter changed executable AST: ' + name)
    for name, text in data['appends'].items():
        path = Path(name)
        path.write_text(path.read_text(encoding='utf-8') + text.replace('须', '須'), encoding='utf-8')
    MANIFEST.write_bytes(raw)
    print('TASK8_INPUT_SHA256=' + DIGEST)
    print('Prepared 17 new files; no existing source, SQL, tests, or rules modified.')


def export():
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise RuntimeError('Manifest changed')
    data = json.loads(raw)
    changed = subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines()
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines()
    if set(changed + untracked) != set(data['files']) | DOCS:
        raise RuntimeError('Unexpected post-verification diff')
    parent = os.environ['GITHUB_SHA']
    tree = api(f'git/commits/{parent}')['tree']['sha']
    descriptions = {'S1': 'validate domain seeds and immutable configuration fingerprints',
                    'S2': 'publish profile revisions atomically with stale-write protection',
                    'S3': 'preserve lifecycle history and verify replay and concurrency'}
    for group in ('S1', 'S2', 'S3'):
        entries = [{'path': name, 'mode': '100644', 'type': 'blob',
                    'content': Path(name).read_text(encoding='utf-8')}
                   for name in data['groups'][group]]
        if group == 'S3':
            entries += [{'path': name, 'mode': '100644', 'type': 'blob',
                         'content': Path(name).read_text(encoding='utf-8')} for name in sorted(DOCS)]
            entries += [{'path': name, 'mode': '100644', 'type': 'blob', 'sha': None} for name in TEMP]
        tree = api('git/trees', {'base_tree': tree, 'tree': entries})['sha']
        commit = api('git/commits', {'message': f'task-8 {group}: {descriptions[group]}\n\n'
                     'Refs #8, #5. Mechanically materialize the checksum-verified author candidate\n'
                     'after locked ci-fast. No independent ACCEPT or main integration.',
                     'tree': tree, 'parents': [parent]})
        parent = commit['sha']
        print('TASK8_' + group + '_COMMIT=' + parent)
    print('TASK8_FINAL_TREE=' + tree)
    print('TASK8_FINAL_COMMIT=' + parent)
    print('No refs, PRs, Issues, main or Project modified by this job.')


if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/codex/8-watch-profile-revisions':
    raise RuntimeError('Wrong repository or branch')
if sys.argv[1:] == ['prepare']:
    prepare()
elif sys.argv[1:] == ['export']:
    export()
else:
    raise RuntimeError('Unknown bounded operation')
