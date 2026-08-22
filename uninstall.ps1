$ErrorActionPreference = "Stop"

$skillTarget = Join-Path $HOME ".agents\skills\adversarial-review"
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$agentTarget = Join-Path $codexRoot "agents\adversarial-reviewer.toml"

if (Test-Path $skillTarget) { Remove-Item -Recurse -Force $skillTarget }
if (Test-Path $agentTarget) { Remove-Item -Force $agentTarget }

Write-Host "Adversarial Review removed from the global Codex configuration."
