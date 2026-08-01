#!/bin/bash
set -Eeuo pipefail
umask 077

tmpdir=""
MAX_BUNDLE_BYTES=${VERIFY_MAX_BUNDLE_BYTES:-274877906944}
MAX_DUMP_BYTES=${VERIFY_MAX_DUMP_BYTES:-274877906944}
MAX_CONFIG_BYTES=${VERIFY_MAX_CONFIG_BYTES:-1048576}
MAX_METADATA_BYTES=${VERIFY_MAX_METADATA_BYTES:-65536}

cleanup() {
    local rc=$?
    [[ -z "$tmpdir" ]] || rm -rf -- "$tmpdir"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fail() { printf 'verification error: %s\n' "$1" >&2; exit "${2:-1}"; }

extract_dir=""
if [[ ${1:-} == --extract-dir ]]; then
    extract_dir=${2:?missing extraction directory}
    shift 2
fi
artifact=${1:?usage: verify_backup.sh [--extract-dir DIR] ARTIFACT}
(( $# == 1 )) || fail 'unexpected verification arguments' 2
for command in age pg_restore sha256sum tar python3 stat; do command -v "$command" >/dev/null || fail "required command is unavailable: $command"; done
for limit in "$MAX_BUNDLE_BYTES" "$MAX_DUMP_BYTES" "$MAX_CONFIG_BYTES" "$MAX_METADATA_BYTES"; do
    [[ "$limit" =~ ^[1-9][0-9]*$ ]] || fail 'verification size limits must be positive integers'
done
[[ -f "$artifact" && ! -L "$artifact" ]] || fail 'artifact is missing or is a symlink'
mode=$(stat -c '%a' "$artifact")
[[ "$mode" == 600 ]] || fail 'artifact permissions must be 0600'
sidecar="$artifact.sha256"
[[ -f "$sidecar" && ! -L "$sidecar" ]] || fail 'external checksum is missing or unsafe'
(( $(stat -c '%s' "$sidecar") <= MAX_METADATA_BYTES )) || fail 'external checksum is too large'
ARTIFACT="$artifact" SIDECAR="$sidecar" python3 - <<'PY' || exit $?
import hashlib
import os
import pathlib
import re
import sys

artifact = pathlib.Path(os.environ['ARTIFACT'])
try:
    text = pathlib.Path(os.environ['SIDECAR']).read_text(encoding='ascii')
    match = re.fullmatch(r'([0-9A-Fa-f]{64})  ([^\r\n]+)\n?', text)
    if not match or match.group(2) != artifact.name:
        raise ValueError('invalid external checksum schema')
    name = pathlib.PurePosixPath(match.group(2))
    if name.is_absolute() or '..' in name.parts or len(name.parts) != 1:
        raise ValueError('unsafe external checksum filename')
    digest = hashlib.sha256()
    with artifact.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest().lower() != match.group(1).lower():
        raise ValueError('external checksum mismatch')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(4)
PY
[[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] || fail 'AGE_IDENTITY_FILE is missing or unsafe'
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-verify.XXXXXX")
max_blocks=$(( (MAX_BUNDLE_BYTES + 511) / 512 ))
(
    ulimit -f "$max_blocks"
    exec age -d -i "$AGE_IDENTITY_FILE" -o "$tmpdir/bundle.tar" "$artifact"
) || fail 'decryption failed or exceeded the bundle size limit' 5
(( $(stat -c '%s' "$tmpdir/bundle.tar") <= MAX_BUNDLE_BYTES )) || fail 'decrypted bundle exceeds the size limit' 5

# Inspect metadata before extraction. No links, devices, extra names, absolute or
# parent paths are accepted, even if a tar implementation would sanitize them.
BUNDLE="$tmpdir/bundle.tar" MAX_DUMP_BYTES="$MAX_DUMP_BYTES" \
MAX_CONFIG_BYTES="$MAX_CONFIG_BYTES" MAX_METADATA_BYTES="$MAX_METADATA_BYTES" \
python3 - <<'PY' || exit $?
import os
import pathlib
import sys
import tarfile

allowed = {'manifest.json', 'checksums.sha256', 'dump.custom', 'config.env'}
limits = {
    'manifest.json': int(os.environ['MAX_METADATA_BYTES']),
    'checksums.sha256': int(os.environ['MAX_METADATA_BYTES']),
    'dump.custom': int(os.environ['MAX_DUMP_BYTES']),
    'config.env': int(os.environ['MAX_CONFIG_BYTES']),
}
try:
    with tarfile.open(os.environ['BUNDLE'], 'r:') as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if set(names) != allowed or len(names) != len(allowed):
            raise ValueError('archive members do not match the allowlist')
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not member.isfile():
                raise ValueError('unsafe archive member')
            if member.size < 0 or member.size > limits[member.name]:
                raise ValueError(f'archive member exceeds size limit: {member.name}')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(6)
PY
mkdir "$tmpdir/extracted"
tar -xf "$tmpdir/bundle.tar" -C "$tmpdir/extracted" --no-same-owner --no-same-permissions
ROOT="$tmpdir/extracted" MAX_CONFIG_BYTES="$MAX_CONFIG_BYTES" MAX_METADATA_BYTES="$MAX_METADATA_BYTES" python3 - <<'PY' || exit $?
import hashlib
import json
import os
import pathlib
import re
import sys

root = pathlib.Path(os.environ['ROOT'])

def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

try:
    manifest_path = root / 'manifest.json'
    checksum_path = root / 'checksums.sha256'
    config = root / 'config.env'
    if manifest_path.stat().st_size > int(os.environ['MAX_METADATA_BYTES']):
        raise ValueError('manifest is too large')
    if checksum_path.stat().st_size > int(os.environ['MAX_METADATA_BYTES']):
        raise ValueError('internal checksum file is too large')
    if config.is_symlink() or config.stat().st_size > int(os.environ['MAX_CONFIG_BYTES']):
        raise ValueError('unsafe configuration component')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    required = {'format_version', 'created_at_utc', 'database_name', 'postgresql_version',
                'alembic_revision', 'git_commit_sha', 'files'}
    if set(manifest) != required or manifest['format_version'] != 1:
        raise ValueError('unsupported or invalid manifest schema')
    if manifest['files'] != ['dump.custom', 'config.env']:
        raise ValueError('invalid manifest file list')
    revision = manifest['alembic_revision']
    if not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', revision):
        raise ValueError('invalid Alembic revision')

    keys = set()
    for line in config.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            keys.add(line.split('=', 1)[0].strip())
    missing = {'DATABASE_URL', 'DB_ENCRYPTION_KEY', 'REDIS_URL', 'BOT_TOKEN'} - keys
    if missing:
        raise ValueError('configuration is missing required keys')

    checksum_lines = checksum_path.read_text(encoding='ascii').splitlines()
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
        if sha256_file(root / name) != expected:
            raise ValueError('internal checksum mismatch')
except Exception as exc:
    print(f'verification error: {exc}', file=sys.stderr)
    sys.exit(7)
PY
pg_restore --list "$tmpdir/extracted/dump.custom" >/dev/null || fail 'PostgreSQL dump is unreadable'
if [[ -n "$extract_dir" ]]; then
    [[ "$extract_dir" == /* ]] || fail 'extraction destination must be absolute'
    [[ ! -e "$extract_dir" && ! -L "$extract_dir" ]] || fail 'extraction destination already exists'
    mkdir -m 700 "$extract_dir"
    install -m 600 "$tmpdir/extracted/manifest.json" "$extract_dir/manifest.json"
    install -m 600 "$tmpdir/extracted/dump.custom" "$extract_dir/dump.custom"
    install -m 600 "$tmpdir/extracted/config.env" "$extract_dir/config.env"
fi
printf 'timestamp=%s artifact=%s size=%s result=success checksum=%s offsite=not-checked\n' \
    "$(date -u +%FT%TZ)" "$(basename -- "$artifact")" "$(stat -c %s "$artifact")" "$(sha256sum "$artifact" | awk '{print $1}')"
