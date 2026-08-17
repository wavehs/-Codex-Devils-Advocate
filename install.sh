#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$HOME/.agents/skills"
AGENTS_ROOT="$HOME/.codex/agents"
SKILL_TARGET="$SKILLS_ROOT/adversarial-review"
AGENT_TARGET="$AGENTS_ROOT/adversarial-reviewer.toml"

mkdir -p "$SKILLS_ROOT" "$AGENTS_ROOT"
rm -rf "$SKILL_TARGET"
cp -R "$ROOT/.agents/skills/adversarial-review" "$SKILL_TARGET"
cp "$ROOT/.codex/agents/adversarial-reviewer.toml" "$AGENT_TARGET"

echo "Installed globally:"
echo "  $SKILL_TARGET"
echo "  $AGENT_TARGET"
echo
echo 'Use in any Codex project: $adversarial-review'
echo "If Codex is already open and the skill is not visible, restart Codex."
