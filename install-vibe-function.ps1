# Add vibe function to PowerShell profile
$profileContent = @'

# vibe wrapper for Node.js v25 compatibility
function vibe {
    tsx C:\Users\matei\AppData\Roaming\npm\node_modules\vibe-cli\index.ts @args
}

# Alias for convenience
Set-Alias -Name v -Value vibe
'@

$profilePath = $PROFILE
if (-not $profilePath) {
    $profilePath = Join-Path $env:USERPROFILE "Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1"
}

# Ensure directory exists
$profileDir = Split-Path -Parent $profilePath
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

# Add to profile
Add-Content -Path $profilePath -Value $profileContent -Force

Write-Host "✓ vibe function added to: $profilePath" -ForegroundColor Green
Write-Host "  Restart PowerShell or run: . '$profilePath'" -ForegroundColor Yellow
