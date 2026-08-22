$ErrorActionPreference = "Stop"

$skillSource = Join-Path $PSScriptRoot "skills\adversarial-review"
$agentSource = Join-Path $PSScriptRoot ".codex\agents\adversarial-reviewer.toml"

$skillsRoot = Join-Path $HOME ".agents\skills"
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
if ($env:CODEX_HOME -and -not (Test-Path -LiteralPath $codexRoot -PathType Container)) {
    throw "CODEX_HOME does not exist or is not a directory: $codexRoot"
}
$agentsRoot = Join-Path $codexRoot "agents"
$skillTarget = Join-Path $skillsRoot "adversarial-review"
$agentTarget = Join-Path $agentsRoot "adversarial-reviewer.toml"

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $agentsRoot | Out-Null

if (Test-Path $skillTarget) {
    Remove-Item -Recurse -Force $skillTarget
}

Copy-Item -Recurse -Force $skillSource $skillTarget
Copy-Item -Force $agentSource $agentTarget

Write-Host "Installed globally:"
Write-Host "  $skillTarget"
Write-Host "  $agentTarget"
Write-Host ""
Write-Host "Use in any Codex project: `$adversarial-review"
Write-Host "If Codex is already open and the skill is not visible, restart Codex."
