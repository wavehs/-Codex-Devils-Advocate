$ErrorActionPreference = "Stop"

$skillTarget = Join-Path $HOME ".agents\skills\adversarial-review"
$agentTarget = Join-Path $HOME ".codex\agents\adversarial-reviewer.toml"

if (Test-Path $skillTarget) { Remove-Item -Recurse -Force $skillTarget }
if (Test-Path $agentTarget) { Remove-Item -Force $agentTarget }

Write-Host "Adversarial Review removed from the global Codex configuration."
