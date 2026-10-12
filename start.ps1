param([int]$Port = 5088)
# Compatibility entry; startup logic is maintained in scripts/.
& (Join-Path $PSScriptRoot 'scripts\start.ps1') -Port $Port
exit $LASTEXITCODE
