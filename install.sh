#!/usr/bin/env bash
# Install the ci-failure-explainer skill for Claude Code, OpenAI Codex, Cursor and Google Antigravity.
#
#   ./install.sh                         # all agents, user scope (every project on this machine)
#   ./install.sh --agent claude          # one agent: claude | codex | cursor | antigravity | all
#   ./install.sh --project .             # project scope: commit the skill with your repo
#   ./install.sh --link                  # symlink instead of copy (auto-updates with git pull)
#   ./install.sh --uninstall
#
# Remote one-liner (downloads the repo to a temp dir first):
#   curl -fsSL https://raw.githubusercontent.com/hasanat-ahmad/CI-Failure-Explainer/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/hasanat-ahmad/CI-Failure-Explainer/main/install.sh | bash -s -- --agent codex

set -euo pipefail

SKILL="ci-failure-explainer"
REPO_URL="https://github.com/hasanat-ahmad/CI-Failure-Explainer"
AGENT="all"
PROJECT=""
MODE="copy"
ACTION="install"

usage() { sed -n '2,13p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }
say()   { printf '  %s\n' "$*"; }
die()   { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --agent)     AGENT="${2:-}"; shift 2 ;;
    --project)   PROJECT="${2:-.}"; shift 2 ;;
    --link)      MODE="link"; shift ;;
    --uninstall) ACTION="uninstall"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           die "unknown option: $1 (see --help)" ;;
  esac
done

case "$AGENT" in claude|codex|cursor|antigravity|all) ;; *) die "--agent must be claude, codex, cursor, antigravity or all" ;; esac

# Locate the skill source: this checkout, or a fresh download when piped from curl.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
SRC="$SCRIPT_DIR/skills/$SKILL"
if [ ! -f "$SRC/SKILL.md" ]; then
  [ "$ACTION" = "uninstall" ] || {
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    say "Downloading $REPO_URL ..."
    if command -v git >/dev/null 2>&1; then
      git clone --depth 1 --quiet "$REPO_URL.git" "$TMP/repo"
    else
      curl -fsSL "$REPO_URL/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP"
      mv "$TMP"/CI-Failure-Explainer-* "$TMP/repo"
    fi
    SRC="$TMP/repo/skills/$SKILL"
    if [ "$MODE" = "link" ]; then say "--link needs a local checkout; copying instead."; MODE="copy"; fi
  }
fi

command -v python3 >/dev/null 2>&1 || say "warning: python3 not found - the skill's scripts need Python 3.9+."
command -v gh >/dev/null 2>&1 || say "note: GitHub CLI (gh) not found - install it to fetch runs (pasted logs still work)."

# Target directories (documented skill locations for each agent).
targets() {
  local a="$1"
  if [ -n "$PROJECT" ]; then
    case "$a" in
      claude)      echo "$PROJECT/.claude/skills" ;;
      codex|antigravity) echo "$PROJECT/.agents/skills" ;;   # shared open-standard location
      cursor)      echo "$PROJECT/.cursor/skills" ;;
    esac
  else
    case "$a" in
      claude)      echo "$HOME/.claude/skills" ;;
      codex)       echo "$HOME/.agents/skills" ;;
      cursor)      echo "$HOME/.cursor/skills" ;;
      antigravity) echo "$HOME/.gemini/config/skills" ;;
    esac
  fi
}

if [ "$AGENT" = "all" ]; then AGENTS="claude codex cursor antigravity"; else AGENTS="$AGENT"; fi

DONE=""
for a in $AGENTS; do
  dir="$(targets "$a")"
  case " $DONE " in *" $dir "*) say "✓ $a  → $dir/$SKILL (shared)"; continue ;; esac
  DONE="$DONE $dir"
  dest="$dir/$SKILL"
  if [ "$ACTION" = "uninstall" ]; then
    if [ -e "$dest" ] || [ -L "$dest" ]; then rm -rf "$dest"; say "✗ removed $dest"; fi
    continue
  fi
  mkdir -p "$dir"
  rm -rf "$dest"
  if [ "$MODE" = "link" ]; then
    ln -s "$SRC" "$dest"
  else
    cp -R "$SRC" "$dest"
    find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  fi
  say "✓ $a  → $dest"
done

if [ "$ACTION" = "install" ]; then
  echo
  say "Done. Restart your agent (or run /skills reload), then ask: \"Why did CI fail?\""
  if [ -n "$PROJECT" ]; then say "Commit the skill folder(s) above so your whole team gets it."; fi
fi
