$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
uv run --script scripts/pet.py @args
exit $LASTEXITCODE
