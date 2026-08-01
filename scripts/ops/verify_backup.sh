#!/bin/bash
set -Eeuo pipefail
umask 077
tmpdir=""
cleanup() { rc=$?; [[ -z "$tmpdir" ]] || rm -rf -- "$tmpdir"; exit "$rc"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fail() { printf 'verification error: %s\n' "$1" >&2; exit "${2:-1}"; }

extract_dir=""
if [[ ${1:-} == --extract-dir ]]; then extract_dir=${2:?missing extraction directory}; shift 2; fi
artifact=${1:?usage: verify_backup.sh [--extract-dir DIR] ARTIFACT}
for command in age pg_restore sha256sum tar python3 stat; do command -v "$command" >/dev/null || fail "required command is unavailable: $command"; done
[[ -f "$artifact" && ! -L "$artifact" ]] || fail 'artifact is missing or is a symlink'
mode=$(stat -c '%a' "$artifact")
[[ "$mode" == 600 ]] || fail 'artifact permissions must be 0600'
sidecar="$artifact.sha256"
[[ -f "$sidecar" && ! -L "$sidecar" ]] || fail 'external checksum is missing or unsafe'
ARTIFACT="$artifact" SIDECAR="$sidecar" python3 - <<'PY' || exit $?
import hashlib, os, pathlib, re, sys

artifact = pathlib.Path(os.environ['ARTIFACT'])
sidecar = pathlib.Path(os.environ['SIDECAR'])

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

try:
    if sidecar.stat().st_size > 4096:
        raise ValueError('external checksum sidecar is too large')
    text = sidecar.read_text(encoding='ascii')
    match = re.fullmatch(r'([0-9A-Fa-f]{64})  ([^\r\n]+)\n?', text)
    if not match or match.group(2) != artifact.name:
        raise ValueError('invalid external checksum schema')
    name = pathlib.PurePosixPath(match.group(2))
    if name.is_absolute() or '..' in name.parts or len(name.parts) != 1:
        raise ValueError('unsafe external checksum filename')
    actual = sha256_file(artifact)
    if actual.lower() != match.group(1).lower():
        raise ValueError('external checksum mismatch')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(4)
PY
[[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] || fail 'AGE_IDENTITY_FILE is missing or unsafe'
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-verify.XXXXXX")
age -d -i "$AGE_IDENTITY_FILE" -o "$tmpdir/bundle.tar" "$artifact" || fail 'decryption failed' 5

# Inspect metadata before extraction. No links, devices, extra names, absolute or
# parent paths are accepted, even if a tar implementation would sanitize them.
BUNDLE="$tmpdir/bundle.tar" python3 - <<'PY' || exit $?
import os, pathlib, sys, tarfile
allowed = {'manifest.json', 'checksums.sha256', 'dump.custom', 'config.env'}
try:
    with tarfile.open(os.environ['BUNDLE'], 'r:') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if set(names) != allowed or len(names) != len(allowed):
            raise ValueError('archive members do not match the allowlist')
        for member in members:
            p = pathlib.PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not member.isfile():
                raise ValueError('unsafe archive member')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(6)
PY
mkdir "$tmpdir/extracted"
tar -xf "$tmpdir/bundle.tar" -C "$tmpdir/extracted" --no-same-owner --no-same-permissions
ROOT="$tmpdir/extracted" python3 - <<'PY' || exit $?
import hashlib, json, os, pathlib, re, sys

root = pathlib.Path(os.environ['ROOT'])

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

try:
    limits = {
        'manifest.json': 1024 * 1024,
        'checksums.sha256': 64 * 1024,
        'config.env': 1024 * 1024,
    }
    for name, limit in limits.items():
        component = root / name
        if component.is_symlink() or component.stat().st_size > limit:
            raise ValueError(f'unsafe or oversized component: {name}')

    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    required = {'format_version', 'created_at_utc', 'database_name', 'postgresql_version',
                'alembic_revision', 'git_commit_sha', 'files'}
    if set(manifest) != required or manifest['format_version'] != 1:
        raise ValueError('unsupported or invalid manifest schema')
    if manifest['files'] != ['dump.custom', 'config.env']:
        raise ValueError('invalid manifest file list')
    revision = manifest['alembic_revision']
    if not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', revision):
        raise ValueError('invalid Alembic revision')
    config = root/'config.env'
    keys = set()
    for line in config.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            keys.add(line.split('=', 1)[0].strip())
    missing = {'DATABASE_URL', 'DB_ENCRYPTION_KEY', 'REDIS_URL', 'BOT_TOKEN'} - keys
    if missing:
        raise ValueError('configuration is missing required keys')
    checksum_lines = (root/'checksums.sha256').read_text(encoding='ascii').splitlines()
    if len(checksum_lines) != 2 or any(not line for line in checksum_lines):
        raise ValueError('invalid internal checksum entry count')
    entries = {}
    for line in checksum_lines:
        match = re.fullmatch(r'([0-9A-Fa-f]{64})  ([^\r\n]+)', line)
        if not match:
            raise ValueError('invalid internal checksum schema')
        name = pathlib.PurePosixPath(match.group(2))
        if name.is_absolute() or '..' in name.parts or len(name.parts) != 1:
            raise ValueError('unsafe internal checksum filename')
        if match.group(2) in entries:
            raise ValueError('duplicate internal checksum entry')
        entries[match.group(2)] = match.group(1).lower()
    if set(entries) != {'dump.custom', 'config.env'}:
        raise ValueError('internal checksum names do not match allowlist')
    for name, expected in entries.items():
        if sha256_file(root/name) != expected:
            raise ValueError('internal checksum mismatch')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(7)
PY
pg_restore --list "$tmpdir/extracted/dump.custom" >/dev/null || fail 'PostgreSQL dump is unreadable'
if [[ -n "$extract_dir" ]]; then
    [[ ! -e "$extract_dir" ]] || fail 'extraction destination already exists'
    mkdir -m 700 "$extract_dir"
    install -m 600 "$tmpdir/extracted/manifest.json" "$extract_dir/manifest.json"
    install -m 600 "$tmpdir/extracted/dump.custom" "$extract_dir/dump.custom"
    install -m 600 "$tmpdir/extracted/config.env" "$extract_dir/config.env"
fi
printf 'timestamp=%s artifact=%s size=%s result=success checksum=%s offsite=not-checked\n' \
    "$(date -u +%FT%TZ)" "$(basename -- "$artifact")" "$(stat -c %s "$artifact")" "$(sha256sum "$artifact" | awk '{print $1}')"
