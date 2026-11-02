param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $projectPython) {
    & $projectPython -m soundsketch --port $Port
} else {
    python -m soundsketch --port $Port
}
