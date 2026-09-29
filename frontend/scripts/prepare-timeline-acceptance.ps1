param([string]$Ffmpeg = '')
$ErrorActionPreference = 'Stop'
$frontendDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDirectory = Join-Path $frontendDirectory '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
if (!$Ffmpeg) {
    $portable = Join-Path $frontendDirectory '../backend/.tools/ffmpeg/local/bin/ffmpeg.exe'
    if (Test-Path -LiteralPath $portable) { $Ffmpeg = (Resolve-Path $portable).Path }
    else { $Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'timeline-fixture.html') -Destination (Join-Path $runtimeDirectory 'timeline-editor.html')
foreach ($fixture in @(@{Name='timeline-fixture.mp4';Seconds=3}, @{Name='timeline-long-fixture.mp4';Seconds=12})) {
    & $Ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=size=640x360:rate=30:duration=$($fixture.Seconds)" -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=$($fixture.Seconds)" -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest (Join-Path $runtimeDirectory $fixture.Name)
    if ($LASTEXITCODE -ne 0) { throw 'Timeline video fixture generation failed.' }
}
& $Ffmpeg -hide_banner -loglevel error -y -i (Join-Path $runtimeDirectory 'timeline-fixture.mp4') -frames:v 1 (Join-Path $runtimeDirectory 'timeline-poster.jpg')
if ($LASTEXITCODE -ne 0) { throw 'Timeline poster generation failed.' }
Write-Output 'Timeline fixtures ready in frontend/.runtime (synthetic test imagery).'
& $Ffmpeg -hide_banner -loglevel error -y -i (Join-Path $runtimeDirectory 'timeline-fixture.mp4') -vf 'fps=1,scale=160:90,tile=3x1' -frames:v 1 (Join-Path $runtimeDirectory 'timeline-filmstrip.jpg')
if ($LASTEXITCODE -ne 0) { throw 'Timeline filmstrip generation failed.' }
