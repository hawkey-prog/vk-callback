# Вшивает vk-bridge.js в index.html между метками BRIDGE:BEGIN и BRIDGE:END.
# Запускать после обновления библиотеки: pwsh build.ps1
# Повторный запуск безопасен — метки остаются на месте.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$bridge = Get-Content 'vk-bridge.js' -Raw
# Ссылка на карту исходников тянется за несуществующим файлом — она не нужна.
$bridge = ($bridge -replace '(?m)^\s*//# sourceMappingURL=.*$', '').Trim()

if ($bridge -match '</script') {
    throw 'В библиотеке есть закрывающий тег script — встраивать нельзя.'
}

$html = Get-Content 'index.html' -Raw
$pattern = '(?s)(<!-- BRIDGE:BEGIN -->).*?(<!-- BRIDGE:END -->)'
if ($html -notmatch $pattern) { throw 'В index.html не найдены метки BRIDGE:BEGIN/END.' }

$replacement = "`$1`n<script>`n$bridge`n</script>`n`$2"
$html = [regex]::Replace($html, $pattern, $replacement)
Set-Content -Path 'index.html' -Value $html -Encoding UTF8 -NoNewline

$size = [Math]::Round((Get-Item 'index.html').Length / 1KB, 1)
Write-Output "vk-bridge встроен, index.html теперь $size КБ"
