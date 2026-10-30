"""Read-only distribution integrity check. Runs no solver."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'SHA256_MANIFEST.json').read_text(encoding='utf-8'))
bad = []
for name, expected in manifest.items():
    path = root / name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        bad.append(name)
if bad:
    raise SystemExit('Missing or changed files: ' + ', '.join(bad))
print(f'PASS: {len(manifest)} distributed files match their recorded SHA-256 hashes.')
