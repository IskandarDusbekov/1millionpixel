# Serverga jo'natish uchun toza arxiv tayyorlaydi.
#
# Ishlatish (loyiha papkasida, Windows PowerShell):
#     .\deploy\package.ps1
#
# Nimalar KIRMAYDI: .venv (Windows binarlari, serverda ishlamaydi),
# __pycache__, dev.sqlite3, media, staticfiles, .git va eng muhimi -
# lokal .env (unda sinov kalitlari va DEBUG=1 bor, serverda ular xavfli).
#
# IZOH: bu fayl ataylab faqat ASCII belgilardan iborat. PowerShell 5.1
# BOM'siz UTF-8 skriptni ANSI deb o'qiydi va uzun tire kabi belgilar satrni
# buzib, "string is missing the terminator" xatosini beradi.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path $root "millionpixel.zip"
$tmp  = Join-Path $env:TEMP ("mp-pack-" + [guid]::NewGuid().ToString("N"))

$include = @(
    "backend", "frontend", "deploy",
    "Dockerfile", "docker-compose.yml", "docker-compose.prod.yml",
    ".env.example", "README.md", ".gitignore"
)

$skipDirs  = @(".venv", "__pycache__", "staticfiles", "media", ".git",
               "node_modules", ".pytest_cache")
$skipFiles = @("dev.sqlite3", "dev.sqlite3-journal", "dev.sqlite3-wal",
               "dev.sqlite3-shm", ".env", "millionpixel.zip")

Write-Host "Toza nusxa tayyorlanmoqda..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

foreach ($item in $include) {
    $src = Join-Path $root $item
    if (-not (Test-Path $src)) {
        Write-Host "  o'tkazib yuborildi (yo'q): $item" -ForegroundColor DarkGray
        continue
    }
    if (Test-Path $src -PathType Container) {
        Get-ChildItem $src -Recurse -File -Force | ForEach-Object {
            $rel = $_.FullName.Substring($root.Length + 1)
            $parts = $rel -split '\\'
            if ($parts | Where-Object { $skipDirs -contains $_ }) { return }
            if ($skipFiles -contains $_.Name) { return }
            $dest = Join-Path $tmp $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
            Copy-Item $_.FullName $dest
        }
    } else {
        if ($skipFiles -contains (Split-Path $src -Leaf)) { continue }
        Copy-Item $src (Join-Path $tmp (Split-Path $src -Leaf))
    }
}

if (Test-Path $out) { Remove-Item $out -Force }
Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $out -CompressionLevel Optimal
Remove-Item $tmp -Recurse -Force

$size = (Get-Item $out).Length / 1MB
$count = (Get-ChildItem $root -Recurse -File -Force | Measure-Object).Count

Write-Host ""
Write-Host ("Tayyor: {0}  ({1:N1} MB)" -f $out, $size) -ForegroundColor Green
Write-Host ""
Write-Host "Serverga jo'natish:" -ForegroundColor Cyan
Write-Host "  scp `"$out`" root@SERVER_IP:/root/"
Write-Host ""
Write-Host "DIQQAT: .env arxivga KIRMADI. Serverda .env.example dan" -ForegroundColor Yellow
Write-Host "        yangisini yaratasiz (yangi kalitlar bilan)." -ForegroundColor Yellow
