"""Decode a checksummed, privately staged bundle without executing its contents."""
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text())
parts = []
for part in manifest['parts']:
    data = (root / part['filename']).read_bytes()
    if len(data) != part['size'] or hashlib.sha256(data).hexdigest() != part['sha256']:
        raise RuntimeError('Transport part failed checksum: ' + part['filename'])
    parts.append(data)
compressed = b''.join(parts)
if hashlib.sha256(compressed).hexdigest() != manifest['payload_sha256']:
    raise RuntimeError('Bundle checksum mismatch')
decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
raw = decoder.decompress(compressed, max_length=2 * 1024 * 1024)
if not decoder.eof or decoder.unused_data or len(raw) != manifest['uncompressed_size']:
    raise RuntimeError('Unexpected decompression size or trailer')
entries = json.loads(raw)
allowed_links = {'repository/AGENTS.md': 'CLAUDE.md',
                 'repository/GEMINI.md': 'CLAUDE.md',
                 'repository/.agents/rules': '../.claude/rules'}
seen = set()
for name, mode, content in entries:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or name in seen or mode not in ('100644', '120000'):
        raise RuntimeError('Unsafe or repeated bundle path')
    if mode == '120000' and allowed_links.get(name) != content:
        raise RuntimeError('Unexpected symlink')
    seen.add(name)
output = root / 'bundle'
if output.exists():
    raise RuntimeError('Refuse to replace existing unpacked workspace')
for mode in ('100644', '120000'):
    for name, entry_mode, content in entries:
        if entry_mode != mode:
            continue
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == '120000':
            path.symlink_to(content)
        else:
            path.write_text(content, encoding='utf-8')
print(f'Validated and unpacked {len(entries)} input files; no publication performed.')
