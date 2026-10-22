#!/usr/bin/env bash
#
# Bootstrap the codebase-memory-mcp code index for this repository (macOS / Linux).
#
# Makes the code knowledge graph available on any machine, for any agent.
#
# What it does, in order:
#   1. Ensures the codebase-memory-mcp binary exists (downloads + verifies SHA256 if missing).
#   2. Sets CBM_RUNTIME_DIR / CBM_CACHE_DIR.
#      These are required, not cosmetic: CBM refuses to start when any parent
#      directory of its coordination directory grants write access to another
#      identity. Pointing both at a clean per-user directory avoids that class
#      of failure (common with sandbox tooling or migrated profiles).
#   3. Registers the MCP server in each agent's PROJECT-level config file.
#   4. Indexes this repository into the knowledge graph.
#
# Idempotent: safe to run repeatedly.

set -euo pipefail

REPO_SLUG="DeusData/codebase-memory-mcp"
VERSION="${CBM_VERSION:-v0.12.0}"
FORCE=0
SKIP_INDEX=0
REINDEX=0

for arg in "$@"; do
  case "$arg" in
    --force)      FORCE=1 ;;
    --skip-index) SKIP_INDEX=1 ;;
    --reindex)    REINDEX=1 ;;
    --help|-h)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      echo "Options: --force  --skip-index  --reindex"
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m    %s\033[0m\n' "$1"; }
info() { printf '\033[90m    %s\033[0m\n' "$1"; }
warn() { printf '\033[33m    %s\033[0m\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

INSTALL_TO="$HOME/.codebase-memory-mcp"
BIN_DIR="$INSTALL_TO/bin"
EXE="$BIN_DIR/codebase-memory-mcp"
DATA_DIR="$INSTALL_TO/data"
RUNTIME_DIR="$INSTALL_TO/runtime"

# ---------------------------------------------------------------- 1. binary

say "Checking binary at $EXE"

if [ -x "$EXE" ] && [ "$FORCE" -eq 0 ]; then
  ok "already present, skipping download"
else
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux)  os=linux ;;
    *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    x86_64|amd64)  arch=amd64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
  esac
  # static Linux builds are published as "portable"; macOS has a single flavour
  if [ "$os" = "linux" ]; then asset="codebase-memory-mcp-linux-${arch}-portable.tar.gz"
  else                           asset="codebase-memory-mcp-${os}-${arch}.tar.gz"; fi
  base="https://github.com/$REPO_SLUG/releases/download/$VERSION"

  mkdir -p "$BIN_DIR"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  echo "    downloading $asset ($VERSION)..."
  if   command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$tmp/$asset" "$base/$asset"
    curl -fsSL --retry 3 -o "$tmp/checksums.txt" "$base/checksums.txt"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$tmp/$asset" "$base/$asset"
    wget -q -O "$tmp/checksums.txt" "$base/checksums.txt"
  else
    echo "need curl or wget" >&2; exit 1
  fi

  # integrity check: never execute an unverified third-party binary
  expected="$(grep -F "$asset" "$tmp/checksums.txt" | head -1 | awk '{print $1}')"
  [ -n "$expected" ] || { echo "no checksum published for $asset" >&2; exit 1; }
  if   command -v sha256sum >/dev/null 2>&1; then actual="$(sha256sum "$tmp/$asset" | awk '{print $1}')"
  elif command -v shasum    >/dev/null 2>&1; then actual="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"
  else echo "need sha256sum or shasum" >&2; exit 1; fi
  if [ "$actual" != "$expected" ]; then
    echo "SHA256 mismatch for $asset: expected $expected, got $actual" >&2; exit 1
  fi
  ok "SHA256 verified: $actual"

  tar -xzf "$tmp/$asset" -C "$tmp"
  found="$(find "$tmp" -name 'codebase-memory-mcp' -type f | head -1)"
  [ -n "$found" ] || { echo "archive did not contain the binary" >&2; exit 1; }
  cp "$found" "$EXE"
  chmod +x "$EXE"
  # macOS quarantines downloaded executables; clear it so the MCP client can launch it
  if [ "$os" = "darwin" ] && command -v xattr >/dev/null 2>&1; then
    xattr -d com.apple.quarantine "$EXE" 2>/dev/null || true
  fi
  ok "installed to $EXE"
fi

mkdir -p "$DATA_DIR" "$RUNTIME_DIR"

# ------------------------------------------------- 2. environment overrides

export CBM_RUNTIME_DIR="$RUNTIME_DIR"
export CBM_CACHE_DIR="$DATA_DIR"

# ------------------------------------------------- 3. project MCP configs

say "Registering project-level MCP configuration"

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g'; }

write_mcp_json() {
  # $1 = path ; merges the server into mcpServers, preserving what is there
  path="$1"
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<JSON
{
  "mcpServers": {
    "codebase-memory-mcp": {
      "command": "$(json_escape "$EXE")",
      "args": [],
      "env": {
        "CBM_RUNTIME_DIR": "$(json_escape "$RUNTIME_DIR")",
        "CBM_CACHE_DIR": "$(json_escape "$DATA_DIR")"
      }
    }
  }
}
JSON
}

write_mcp_json "$PROJECT_ROOT/.mcp.json"
ok ".mcp.json (Claude Code and compatible agents)"
write_mcp_json "$PROJECT_ROOT/.cursor/mcp.json"
ok ".cursor/mcp.json (Cursor)"

# Codex CLI - project-scoped TOML; only honoured for trusted projects
CODEX_TOML="$PROJECT_ROOT/.codex/config.toml"
MARKER='# --- codebase-memory-mcp (managed by scripts/cbm/bootstrap.sh) ---'
mkdir -p "$(dirname "$CODEX_TOML")"
[ -f "$CODEX_TOML" ] || echo "# Codex CLI project-scoped configuration." > "$CODEX_TOML"
if ! grep -qF "$MARKER" "$CODEX_TOML"; then
  cat >> "$CODEX_TOML" <<TOML

$MARKER
[mcp_servers.codebase-memory-mcp]
command = "$EXE"
args = []
env = { CBM_RUNTIME_DIR = "$RUNTIME_DIR", CBM_CACHE_DIR = "$DATA_DIR" }
# --- end codebase-memory-mcp ---
TOML
  ok ".codex/config.toml (Codex CLI, block appended)"
else
  info ".codex/config.toml already configured"
fi

# ---------------------------------------------------------------- 4. index

if [ "$SKIP_INDEX" -eq 1 ]; then
  say "Skipping index (--skip-index)"
else
  say "Indexing repository (this is the step that builds the graph)"
  cd "$PROJECT_ROOT"
  "$EXE" cli index_repository --repo-path "$PROJECT_ROOT" --persistence true 2>&1 \
    | grep -v '^level=warn' | sed 's/^/    /' || true
fi

echo
echo "Done."
echo
echo "  Binary : $EXE"
echo "  Data   : $DATA_DIR"
echo
echo "  Next: restart your coding agent so it picks up the MCP server."
echo "  CLI  : scripts/cbm/cbm cli list_projects"
echo
