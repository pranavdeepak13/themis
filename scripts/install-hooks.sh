#!/bin/sh
# Install the transcript guard as a git pre-commit hook.
set -e
root=$(git rev-parse --show-toplevel)
cat > "$root/.git/hooks/pre-commit" <<'HOOK'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/scripts/check_no_transcripts.py"
HOOK
chmod +x "$root/.git/hooks/pre-commit"
echo "pre-commit hook installed"
