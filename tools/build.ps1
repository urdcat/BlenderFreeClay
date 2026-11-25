[CmdletBinding()]
param([string]$Blender='C:/Program Files/Blender Foundation/Blender 5.2/blender.exe')
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dist=Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'verification') | Out-Null
$files=@('__init__.py','reducer.py','grab.py','blender_manifest.toml','LICENSE.txt') | ForEach-Object { Join-Path (Join-Path $projectRoot 'freeclay') $_ }
$archive=Join-Path $dist 'freeclay-0.1.2.zip'
Compress-Archive -LiteralPath $files -DestinationPath $archive -Force
& $Blender --background --factory-startup --command extension validate $archive
if($LASTEXITCODE -ne 0){throw 'Blender extension validation failed'}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip=[IO.Compression.ZipFile]::OpenRead($archive)
$entries=@()
try {
    foreach($entry in $zip.Entries){
        if($entry.FullName -notin @('__init__.py','reducer.py','grab.py','blender_manifest.toml','LICENSE.txt')){throw 'Unexpected ZIP entry'}
        $stream=$entry.Open()
        $sha=[Security.Cryptography.SHA256]::Create()
        try {$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','')} finally {$sha.Dispose();$stream.Dispose()}
        $source=Join-Path (Join-Path $projectRoot 'freeclay') $entry.FullName
        if($hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash){throw 'ZIP differs from verified source'}
        $entries+=[ordered]@{name=$entry.FullName;sha256=$hash;bytes=$entry.Length}
    }
    if($entries.Count -ne 5){throw 'Incomplete ZIP'}
} finally {$zip.Dispose()}
[ordered]@{passed=$true;archive=$archive;sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash;entries=$entries} |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $projectRoot 'verification/package.json') -Encoding utf8
Get-FileHash -Algorithm SHA256 -LiteralPath $archive | Format-List
