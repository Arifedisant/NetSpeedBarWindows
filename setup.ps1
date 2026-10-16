# Pasang Net Speed Bar di komputer baru.
# Pakai (dari folder ini): powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = 'Stop'

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw 'python.exe tidak ditemukan. Pasang Python 3.13 dulu (centang Add to PATH).' }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
$app = Join-Path $PSScriptRoot 'netspeedbar.pyw'

Write-Host '1/3 Memasang psutil dan Pillow...'
& $py -m pip install --user --upgrade -r (Join-Path $PSScriptRoot 'requirements.txt')

Write-Host '2/3 Mendaftarkan autostart...'
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' 'NetSpeedBar' ('"{0}" "{1}"' -f $pyw, $app)

Write-Host '3/3 Menyalakan widget...'
Start-Process $pyw ('"{0}"' -f $app)
Write-Host 'Selesai.'
