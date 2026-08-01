# Immutable-input wrapper loaded after the base restore implementation.

_base_extract_definition=$(declare -f extract_and_verify_backup)
_base_extract_definition=${_base_extract_definition/#"extract_and_verify_backup ()"/"base_extract_and_verify_backup ()"}
eval "$_base_extract_definition"
unset _base_extract_definition

validate_restore_input_file() {
    local path=$1 label=$2 exact_mode=${3:-} owner mode
    [[ -f "$path" && ! -L "$path" ]] || fail "$label is missing, not regular, or is a symlink: $path"
    owner=$(stat -c '%u' "$path") || return 1
    mode=$(stat -c '%a' "$path") || return 1
    [[ "$owner" == 0 ]] || fail "$label is not root-owned: $path"
    (( (8#$mode & 8#077) == 0 )) || fail "$label permissions are too broad: $path mode=$mode"
    [[ -z "$exact_mode" || "$mode" == "$exact_mode" ]] || fail "$label must have mode $exact_mode: $path mode=$mode"
}

extract_and_verify_backup() {
    validate_restore_input_file "$ARTIFACT" 'backup artifact' 600
    validate_restore_input_file "$ARTIFACT.sha256" 'backup checksum sidecar'
    validate_restore_input_file "${AGE_IDENTITY_FILE:-}" 'age identity'
    base_extract_and_verify_backup
}
