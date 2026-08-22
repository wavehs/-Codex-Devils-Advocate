#!/usr/bin/env bash
set -euo pipefail

CODEX_ROOT="${CODEX_HOME:-$HOME/.codex}"
rm -rf "$HOME/.agents/skills/adversarial-review"
rm -f "$CODEX_ROOT/agents/adversarial-reviewer.toml"
echo "Adversarial Review removed from the global Codex configuration."
