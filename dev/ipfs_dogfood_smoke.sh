#!/usr/bin/env bash
set -euo pipefail

repo="${1:-$(pwd)}"
peer_hint="${2:-}"

cd "$repo"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: $repo is not inside a git worktree" >&2
    exit 2
fi

payload_dpath=".git-well-ipfs-smoke"
sidecar_fpath="${payload_dpath}.ipfs"

if [[ -e "$payload_dpath" || -e "$sidecar_fpath" ]]; then
    echo "error: refusing to overwrite existing $payload_dpath or $sidecar_fpath" >&2
    exit 2
fi

cat <<'EOF'
This smoke test modifies the current working tree. Run it on a scratch branch
or in a disposable clone. It creates .git-well-ipfs-smoke/ and its .ipfs sidecar.
EOF

mkdir -p "$payload_dpath"
printf 'hello from git ipfs smoke test\n' > "$payload_dpath/payload.txt"

git ipfs doctor

add_args=(git ipfs add "$payload_dpath" --name git-well-ipfs-smoke)
if [[ -n "$peer_hint" ]]; then
    add_args+=(--suggested-peers "$peer_hint")
fi
"${add_args[@]}"

git status --short .gitignore "$sidecar_fpath" "$payload_dpath" || true
rm -rf "$payload_dpath"

git ipfs pull "$sidecar_fpath"
test -f "$payload_dpath/payload.txt"
git ipfs status "$sidecar_fpath"

cat <<EOF
Smoke test succeeded.

Review the sidecar with:
  cat $sidecar_fpath

Clean up with:
  git reset -- $sidecar_fpath .gitignore
  rm -rf $payload_dpath $sidecar_fpath
EOF
