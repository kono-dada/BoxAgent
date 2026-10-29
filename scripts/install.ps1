$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Invoke-Checked uv -Arguments @('python', 'install', '3.12')
Invoke-Checked uv -Arguments @('sync', '--script', 'scripts/pet.py')
if (-not (Test-Path '.runtime/jev-mem-venv/Scripts/python.exe')) {
    Invoke-Checked uv -Arguments @('venv', '--python', '3.12', '.runtime/jev-mem-venv')
}
Invoke-Checked uv -Arguments @('pip', 'sync', '--python', '.runtime/jev-mem-venv/Scripts/python.exe', 'scripts/requirements/jev-mem.lock.txt')
Invoke-Checked git -Arguments @('lfs', 'pull')
Invoke-Checked pnpm -Arguments @('--dir', 'web/vrm', 'install', '--frozen-lockfile')
Invoke-Checked pnpm -Arguments @('--dir', 'web/vrm', 'build')
Invoke-Checked pnpm -Arguments @('--dir', 'web/panel', 'install', '--frozen-lockfile')
Invoke-Checked pnpm -Arguments @('--dir', 'web/panel', 'build')
Invoke-Checked node -Arguments @('web/panel/scripts/launch.cjs', '--prepare')
Write-Host 'Installed. Start with: ./scripts/run-pet.ps1'

