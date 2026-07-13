#!/bin/sh
# Secrets inventory: which op.env references actually resolve in 1Password.
# Prints only STATUS per reference — never a secret value. This is the answer
# to "what keys do I have set up?": op.env is the manifest, this checks it.
#   make secrets-doctor
set -eu
ENV_FILE="${1:-op.env}"
missing=0
printf '%-24s %-38s %s\n' VARIABLE REFERENCE STATUS
printf '%-24s %-38s %s\n' '--------' '---------' '------'
while IFS= read -r line; do
  case "$line" in
    ''|\#*) continue ;;          # skip blanks and comments
  esac
  var=${line%%=*}
  case "$line" in
    *op://*)
      ref=$(printf '%s' "$line" | grep -oE 'op://[A-Za-z0-9._/-]+' | head -1)
      if op read "$ref" >/dev/null 2>&1; then
        st=ok
      else
        st=MISSING; missing=$((missing + 1))
      fi
      printf '%-24s %-38s %s\n' "$var" "$ref" "$st"
      ;;
    *)
      printf '%-24s %-38s %s\n' "$var" '(literal default)' 'ok'
      ;;
  esac
done < "$ENV_FILE"
echo
if [ "$missing" -eq 0 ]; then
  echo "all references resolve — every key is set up in 1Password"
else
  echo "$missing reference(s) MISSING — create them in your 1Password vault (see the paths above)"
  exit 1
fi
