#!/bin/sh
# Render op.env -> stdout, resolving op:// references via `op read`.
# Unlike `op inject` (all-or-nothing), a reference that fails to resolve is
# SKIPPED with a warning on stderr — optional keys (e.g. OPENAI_API_KEY, whose
# absence just puts the judge in stub mode) must not block required ones.
# Literal (non-op://) assignments pass through unchanged.
set -eu
ENV_FILE="${1:-op.env}"
missing=0
resolved=0
while IFS= read -r line; do
  case "$line" in
    ''|\#*) continue ;;                       # blanks + comments
  esac
  var=${line%%=*}
  case "$line" in
    *op://*)
      ref=$(printf '%s' "$line" | grep -oE 'op://[A-Za-z0-9._/ -]+' | head -1)
      if val=$(op read "$ref" 2>/dev/null); then
        printf '%s=%s\n' "$var" "$val"
        resolved=$((resolved + 1))
      else
        printf 'render-secrets: SKIP %s (%s does not resolve — see make secrets-doctor)\n' "$var" "$ref" >&2
        missing=$((missing + 1))
      fi
      ;;
    *)
      printf '%s\n' "$line"                   # literal default
      ;;
  esac
done < "$ENV_FILE"
printf 'render-secrets: %s resolved, %s skipped\n' "$resolved" "$missing" >&2
[ "$resolved" -gt 0 ] || { printf 'render-secrets: nothing resolved — is op signed in?\n' >&2; exit 1; }
