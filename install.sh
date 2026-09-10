#!/usr/bin/env bash
# Make the skill discoverable to Claude Code. Re-runnable.
#
# Not needed for a plugin install, which Claude Code discovers on its own.
# There is nothing else to install: jest_lens.py is invoked by path.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$HOME/.claude/skills"
ln -sfn "$repo" "$HOME/.claude/skills/jest-lens"

echo "skill -> ~/.claude/skills/jest-lens"
echo "run   -> python3 $repo/jest_lens.py"
