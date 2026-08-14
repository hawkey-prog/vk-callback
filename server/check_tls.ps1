$host_ = '89-108-78-99.sslip.io'
$port  = 443

$cert = $null
$policyErrors = 'нет данных'

try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect($host_, $port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(10000)) { throw "порт $port не отвечает (таймаут TCP)" }
    $tcp.EndConnect($iar)
    Write-Output "TCP 443: соединение установлено"

    $callback = {
        param($sender, $certificate, $chain, $errors)
        $script:cert = $certificate
        $script:policyErrors = $errors.ToString()
        return $true   # принимаем любой, чтобы рассмотреть
    }
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $callback)
    try {
        $ssl.AuthenticateAsClient($host_)
        Write-Output "TLS: рукопожатие прошло, протокол $($ssl.SslProtocol)"
    } catch {
        Write-Output "TLS: рукопожатие не удалось — $($_.Exception.InnerException.Message)"
    }
    $ssl.Dispose(); $tcp.Close()
} catch {
    Write-Output "Ошибка подключения: $($_.Exception.Message)"
}

if ($cert) {
    $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $cert
    Write-Output ""
    Write-Output "Кому выдан (Subject) : $($c.Subject)"
    Write-Output "Кем выдан  (Issuer)  : $($c.Issuer)"
    Write-Output "Действует с          : $($c.NotBefore)"
    Write-Output "Действует по         : $($c.NotAfter)"
    Write-Output "Просрочен            : $(if ($c.NotAfter -lt (Get-Date)) {'ДА'} else {'нет'})"
    Write-Output "Самоподписанный      : $(if ($c.Subject -eq $c.Issuer) {'ДА'} else {'нет'})"
    try {
        $san = ($c.Extensions | Where-Object { $_.Oid.FriendlyName -match 'Subject Alternative Name' })
        if ($san) { Write-Output "Имена в сертификате  : $($san.Format($false))" }
    } catch {}
    Write-Output "Претензии проверки   : $policyErrors"
} else {
    Write-Output "Сертификат получить не удалось — сервер не начал TLS."
}

Write-Output ""
Write-Output "--- порт 80 ---"
try {
    $r = Invoke-WebRequest "http://$host_/" -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 0 -ErrorAction Stop
    Write-Output "HTTP 80 -> $($r.StatusCode): $($r.Content.Substring(0,[Math]::Min(60,$r.Content.Length)))"
} catch {
    $resp = $_.Exception.Response
    if ($resp) { Write-Output "HTTP 80 -> $([int]$resp.StatusCode) $($resp.Headers['Location'])" }
    else { Write-Output "HTTP 80 -> $($_.Exception.Message)" }
}
