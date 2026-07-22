# Buat shortcut Dave di Desktop + Start Menu + Taskbar
# Jalankan: powershell -ExecutionPolicy Bypass -File create_shortcuts.ps1

$daveDir = $PSScriptRoot
$launcher = Join-Path $daveDir "Run Dave.bat"
$icon     = Join-Path $daveDir "assets\dave.ico"

if (-not (Test-Path $launcher)) {
    Write-Host "[!] Run Dave.bat tidak ditemukan di $daveDir" -ForegroundColor Red
    exit 1
}

function New-DaveShortcut($linkPath) {
    $dir = Split-Path $linkPath -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # Pakai cmd.exe supaya shortcut .bat selalu jalan dengan working directory benar.
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($linkPath)
    $sc.TargetPath = "$env:ComSpec"
    $sc.Arguments  = "/c `"cd /d `"$daveDir`" && `"$launcher`"`""
    $sc.WorkingDirectory = $daveDir
    $sc.Description = "Dave - Quiz Suggest Assistant (20 soal)"
    $sc.WindowStyle = 1  # Normal window
    if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }
    $sc.Save()
}

$desktop  = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$taskBarPin = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"

$paths = @(
    (Join-Path $desktop "Dave.lnk"),
    (Join-Path $startMenu "Dave.lnk")
)

foreach ($p in $paths) {
    New-DaveShortcut $p
    Write-Host "[OK] $p" -ForegroundColor Green
}

# Pin ke taskbar (Windows 10/11) - salin shortcut ke folder pinned taskbar.
if (Test-Path $taskBarPin) {
    $tbLink = Join-Path $taskBarPin "Dave.lnk"
    New-DaveShortcut $tbLink
    Write-Host "[OK] Taskbar: $tbLink" -ForegroundColor Green
    Write-Host "     Jika belum muncul di taskbar, restart Explorer atau pin manual dari Start Menu." -ForegroundColor Yellow
} else {
    Write-Host "[i] Folder taskbar pin tidak ditemukan - pin manual dari Start Menu" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Selesai. Double-klik ikon Dave di Desktop atau taskbar." -ForegroundColor Cyan
