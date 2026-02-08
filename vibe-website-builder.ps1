# vibe Website Builder - Node.js v25 Workaround
# The vibe package uses TypeScript which has compatibility issues with Node 25

param(
    [string]$ProjectName = "simple-website",
    [string]$Prompt = "Create a simple landing page with a header, hero section, and footer"
)

Write-Host "=== Vibe Website Builder ===" -ForegroundColor Cyan
Write-Host ""

# Check Node version
$nodeVersion = node --version
Write-Host "Node.js version: $nodeVersion" -ForegroundColor Yellow

if ($nodeVersion -match "v25\.") {
    Write-Host "WARNING: Node.js v25 has compatibility issues with the vibe package." -ForegroundColor Red
    Write-Host "The vibe package uses TypeScript files that can't be executed directly in Node 25." -ForegroundColor Red
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Cyan
    Write-Host "  1. Use Node.js v20 or v22 (recommended)" -ForegroundColor White
    Write-Host "     Install via: nvm install 22 && nvm use 22" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Use the --experimental-strip-types flag (may not work)" -ForegroundColor White
    Write-Host ""
    Write-Host "  3. Create the website manually with a simple HTML template" -ForegroundColor White
    Write-Host ""
    
    $choice = Read-Host "Choose option (1/2/3)"
    
    if ($choice -eq "3") {
        # Create a simple HTML template
        $ProjectDir = Join-Path $PWD $ProjectName
        New-Item -ItemType Directory -Path $ProjectDir -Force | Out-Null
        
        @"
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Simple Website</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }
        header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 2rem; text-align: center; }
        .hero { padding: 4rem 2rem; text-align: center; background: #f8f9fa; }
        .hero h1 { font-size: 3rem; margin-bottom: 1rem; }
        .hero p { font-size: 1.25rem; color: #666; max-width: 600px; margin: 0 auto; }
        footer { background: #333; color: white; text-align: center; padding: 2rem; margin-top: 4rem; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 2rem; }
    </style>
</head>
<body>
    <header>
        <h1>Welcome to My Website</h1>
    </header>
    
    <section class="hero">
        <div class="container">
            <h1>Simple & Beautiful</h1>
            <p>This is a simple landing page created as a fallback since vibe doesn't work with Node.js v25.</p>
        </div>
    </section>
    
    <footer>
        <p>&copy; 2026 Simple Website. Built with fallback template.</p>
    </footer>
</body>
</html>
"@ | Out-File -FilePath (Join-Path $ProjectDir "index.html") -Encoding UTF8
        
        Write-Host "`n✓ Created fallback website at: $ProjectDir" -ForegroundColor Green
        Write-Host "  Open index.html in your browser to view it." -ForegroundColor White
        return
    }
}

# Create project directory
$ProjectDir = Join-Path $PWD $ProjectName

if (Test-Path $ProjectDir) {
    Write-Host "Directory '$ProjectName' already exists. Removing..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $ProjectDir
}

Write-Host "Creating project directory: $ProjectDir" -ForegroundColor Green
New-Item -ItemType Directory -Path $ProjectDir | Out-Null
Set-Location $ProjectDir

# Try to run vibe with experimental flag
Write-Host "Attempting to run vibe with experimental TypeScript support..." -ForegroundColor Yellow
$env:VIBE_PROMPT = $Prompt

try {
    node --experimental-strip-types (Get-Command vibe).Source 2>&1
} catch {
    Write-Host "Failed to run vibe: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please use Node.js v20 or v22 for full vibe support." -ForegroundColor Yellow
}
