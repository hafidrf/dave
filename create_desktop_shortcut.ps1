# Membuat shortcut "Dave" di Desktop yang menjalankan Run Dave.bat
# Jalankan sekali:  powershell -ExecutionPolicy Bypass -File create_desktop_shortcut.ps1

$daveDir  = $PSScriptRoot
$target   = Join-Path $daveDir "Run Dave.bat"
$icon     = Join-Path $daveDir "assets\dave.ico"
$desktop  = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "Dave.lnk"

if (-not (Test-Path $target)) {
    Write-Host "[!] Tidak menemukan 'Run Dave.bat' di $daveDir" -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($linkPath)
$sc.TargetPath       = $target
$sc.WorkingDirectory = $daveDir
$sc.Description       = "Dave - Quiz Suggest Assistant"
$sc.WindowStyle       = 1
if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }
$sc.Save()

Write-Host "[OK] Shortcut dibuat: $linkPath" -ForegroundColor Green
Write-Host "     Target : $target"
Write-Host "     Icon   : $icon"
