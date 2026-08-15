#!/usr/bin/env bash
set -euo pipefail

mode="${1:---all}"
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

files_list="$(mktemp)"
hits_list="$(mktemp)"
trap 'rm -f "$files_list" "$hits_list"' EXIT

case "$mode" in
  --all)
    git ls-files -co --exclude-standard -z >"$files_list"
    ;;
  --staged)
    git diff --cached --name-only -z --diff-filter=ACMR >"$files_list"
    ;;
  *)
    echo "usage: $0 [--all|--staged]" >&2
    exit 2
    ;;
esac

if [ ! -s "$files_list" ]; then
  exit 0
fi

failed=0

while IFS= read -r -d '' path; do
  [ -e "$path" ] || continue

  case "$path" in
    .env|.env.*|*.env|*.env.*|*keys*.env|generated-consumer-keys.env)
      if [ "$path" != ".env.example" ]; then
        echo "blocked secret-like environment file: $path" >&2
        failed=1
      fi
      ;;
  esac

  case "$path" in
    *kubeconfig*|*.kubeconfig|*.kubeconfig.yaml|*.kubeconfig.yml)
      echo "blocked kubeconfig-like file path: $path" >&2
      failed=1
      ;;
    *.tar|*.tar.*|*.tgz|*.zip|*.docx|*.pdf)
      echo "blocked archive or handover binary: $path" >&2
      failed=1
      ;;
    runs/*|run-*/*|generated/*|rendered/*|tmp/*|temp/*)
      echo "blocked generated run path: $path" >&2
      failed=1
      ;;
  esac
done <"$files_list"

check_content() {
  local label="$1"
  local pattern="$2"

  : >"$hits_list"
  if xargs -0 -r -a "$files_list" rg --files-with-matches --pcre2 --no-messages -e "$pattern" -- >"$hits_list"; then
    if [ -s "$hits_list" ]; then
      echo "blocked $label in:" >&2
      sed 's/^/  /' "$hits_list" >&2
      failed=1
    fi
  fi
}

check_content "API-key-like token" '(^|[^A-Za-z0-9_])sk-[A-Za-z0-9][A-Za-z0-9_-]{16,}'
check_content "consumer API key material" 's[k]-consumer-[A-Za-z0-9._-]{6,}'
check_content "kubeconfig credential marker" '(client[-]certificate[-]data|client[-]key[-]data|certificate[-]authority[-]data|current[-]context:)'
check_content "private key block" '-----BEGIN [A-Z ]*PRIVATE KEY-----'
check_content "private registry name" 'registry.example.internal:5000'
check_content "private environment domain" 'example.internal'
check_content "Kubernetes secret manifest content" '(^kind:[[:space:]]*Secret[[:space:]]*$|^string[D]ata:)'

if [ "$failed" -ne 0 ]; then
  echo "secret boundary scan failed" >&2
  exit 1
fi
