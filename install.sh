#!/usr/bin/env bash
# Symlink the `jl` command and the Claude Code skill. Re-runnable.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bin=$HOME/.local/bin
mkdir -p "$bin" "$HOME/.claude/skills"

# A plugin install lives under a version-stamped cache directory, so a symlink
# to this copy would dangle on the next plugin update. Resolve the newest
# version at run time instead.
if [[ $repo =~ /plugins/cache/[^/]+/[^/]+/[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  versions=$(dirname "$repo")
  cat > "$bin/jl" <<SHIM
#!/usr/bin/env bash
exec python3 "\$(ls -d "$versions"/*/jest_lens.py | sort -V | tail -1)" "\$@"
SHIM
  chmod +x "$bin/jl"
  echo "jl     -> newest version under $versions"
else
  ln -sfn "$repo/jest_lens.py" "$bin/jl"
  echo "jl     -> $repo/jest_lens.py"
  ln -sfn "$repo" "$HOME/.claude/skills/jest-lens"
  echo "skill  -> ~/.claude/skills/jest-lens"
fi

case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "warning: $bin is not on PATH" >&2 ;;
esac
