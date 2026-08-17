#!/usr/bin/env bash
set -euo pipefail

rm -rf "$HOME/.agents/skills/adversarial-review"
rm -f "$HOME/.codex/agents/adversarial-reviewer.toml"
echo "Adversarial Review removed from the global Codex configuration."
