$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
function Await-WinRT($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(2000)) { throw 'Media session query timed out' }
    return $task.Result
}
$manager = Await-WinRT ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$music = $false
foreach ($session in $manager.GetSessions()) {
    $info = $session.GetPlaybackInfo()
    if ($info.PlaybackStatus.ToString() -ne 'Playing') { continue }
    $properties = Await-WinRT ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType=WindowsRuntime])
    $kind = [string]$info.PlaybackType
    $description = [string]$properties.Title
    $musicApp = $session.SourceAppUserModelId -match '(?i)spotify|cloudmusic|qqmusic|music\.ui|applemusic'
    if ($kind -eq 'Music' -or $musicApp -or $properties.AlbumTitle -or
        $description -match '(?i)音乐|歌曲|歌单|纯音乐|演唱|演奏|翻唱|music|song|playlist|\bBGM\b|\bMV\b') {
        $music = $true
        break
    }
}
@{ music = $music } | ConvertTo-Json -Compress
