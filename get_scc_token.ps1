$ErrorActionPreference = 'Stop'

$kraBase = 'https://api.kra.sk'
$sccBase = 'https://stream-cinema.online/kodi'
$outputPath = Join-Path $PSScriptRoot 'secrets\sc.json'
$uuid = [guid]::NewGuid().ToString()

function Get-AuthToken([string]$Payload) {
    $text = $Payload.Trim()
    if ($text -match '^[A-Za-z0-9]+$') { return $text }

    try { $value = $text | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
    if ($value -is [string] -and $value -match '^[A-Za-z0-9]+$') { return $value }
    foreach ($name in @('token', 'auth_token', 'system.auth_token')) {
        $property = $value.PSObject.Properties[$name]
        if ($property -and [string]$property.Value -match '^[A-Za-z0-9]+$') { return [string]$property.Value }
    }
    if ($value.data) { return Get-AuthToken ($value.data | ConvertTo-Json -Depth 10 -Compress) }
    return $null
}

if (Test-Path -LiteralPath $outputPath) {
    Write-Host 'secrets\sc.json already exists; leaving it unchanged.' -ForegroundColor Yellow
    exit 0
}

$user = Read-Host 'Kra.sk username'
$securePassword = Read-Host 'Kra.sk password' -AsSecureString
$pointer = [IntPtr]::Zero
$plainPassword = $null

try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $loginBody = @{
        data = @{ username = $user; password = $plainPassword }
    } | ConvertTo-Json -Depth 5

    $login = Invoke-RestMethod -Uri "$kraBase/api/user/login" -Method Post -ContentType 'application/json' -Body $loginBody -TimeoutSec 30
    $session = [string]$login.session_id
    if (-not $session) { throw 'Authentication failed.' }

    # Reuse the account's existing backup token when one is available.
    $listBody = @{
        session_id = $session
        data = @{ parent = $null; filter = 'sc.json' }
    } | ConvertTo-Json -Depth 5
    $listing = Invoke-RestMethod -Uri "$kraBase/api/file/list" -Method Post -ContentType 'application/json' -Body $listBody -TimeoutSec 30
    $backup = @($listing.data) | Where-Object { $_.name -eq 'sc.json' } | Select-Object -First 1

    if ($backup) {
        $downloadBody = @{
            session_id = $session
            data = @{ ident = $backup.ident }
        } | ConvertTo-Json -Depth 5
        $download = Invoke-RestMethod -Uri "$kraBase/api/file/download" -Method Post -ContentType 'application/json' -Body $downloadBody -TimeoutSec 30
        if (-not $download.data.link) { throw 'Token download was unavailable.' }
        $response = Invoke-WebRequest -Uri $download.data.link -TimeoutSec 30
        $token = Get-AuthToken ([string]$response.Content)
    }
    else {
        $params = @{
            krt = $session
            ver = '2.0'
            uid = $uuid
            skin = 'estuary'
            lang = 'sk'
            pro = 'kraska'
        }
        $query = ($params.GetEnumerator() | ForEach-Object {
            "$([uri]::EscapeDataString($_.Key))=$([uri]::EscapeDataString([string]$_.Value))"
        }) -join '&'
        $response = Invoke-RestMethod -Uri "$sccBase/auth/token?$query" -Method Post -Headers @{
            'X-Uuid' = $uuid
            'User-Agent' = 'Kodi Stream Cinema'
        } -TimeoutSec 30
        $token = [string]$response.token
    }

    if ($token -notmatch '^[A-Za-z0-9]+$') { throw 'The service did not return a valid token.' }
    $directory = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    [System.IO.File]::WriteAllText($outputPath, $token, [System.Text.UTF8Encoding]::new($false))
    Write-Host 'Your token was saved to secrets\sc.json. Its value was not printed.' -ForegroundColor Green
}
catch {
    Write-Error 'Could not obtain the token. Check your account and connection; sensitive response details were suppressed.'
    exit 1
}
finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $plainPassword = $null
    $loginBody = $null
    $session = $null
    $token = $null
}
