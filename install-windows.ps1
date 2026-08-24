$ErrorActionPreference = "Stop"

$source = Join-Path $PSScriptRoot "skills\Matrixapi-imagegen"
$skillsRoot = Join-Path $env:USERPROFILE ".codex\skills"
$target = Join-Path $skillsRoot "Matrixapi-imagegen"
$legacy = Join-Path $skillsRoot "api-imagegen"
$script = Join-Path $target "scripts\generate.py"

if (-not (Test-Path -LiteralPath $source)) {
    throw "The Matrixapi-imagegen Skill directory was not found."
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonArgs = @()
if (-not $pythonCommand) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    $pythonArgs = @("-3")
}
if (-not $pythonCommand) {
    throw "Python 3 was not found. Install Python 3 and run this installer again."
}

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
$installId = [Guid]::NewGuid().ToString("N")
$stage = Join-Path $skillsRoot ".Matrixapi-imagegen.install-$installId"
$backup = Join-Path (Split-Path -Parent $skillsRoot) ".Matrixapi-imagegen.backup-$installId"
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $stage -Recurse -Force

$movedOld = $false
try {
    if (Test-Path -LiteralPath $target) {
        Move-Item -LiteralPath $target -Destination $backup
        $movedOld = $true
    }
    Move-Item -LiteralPath $stage -Destination $target
    if (-not (Test-Path -LiteralPath $script)) {
        throw "The replacement Skill failed validation."
    }
}
catch {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    if ($movedOld -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $target
    }
    throw
}
if (Test-Path -LiteralPath $backup) {
    Remove-Item -LiteralPath $backup -Recurse -Force
}

if (Test-Path -LiteralPath $legacy) {
    $legacySkill = Join-Path $legacy "SKILL.md"
    $legacyScript = Join-Path $legacy "scripts\generate.py"
    $recognizedLegacy = (
        (Test-Path -LiteralPath $legacySkill) -and
        (Test-Path -LiteralPath $legacyScript) -and
        ((Get-Content -LiteralPath $legacySkill -Raw) -match "name:\s*api-imagegen") -and
        ((Get-Content -LiteralPath $legacyScript -Raw) -match "api-imagegen-skill/")
    )
    if ($recognizedLegacy) {
        Remove-Item -LiteralPath $legacy -Recurse -Force
    }
    else {
        Write-Warning "The existing api-imagegen directory is not a recognized legacy Skill and was left unchanged."
    }
}

$secureKey = Read-Host "Enter your MatrixAI API key (input is hidden)" -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
}

if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "An API key is required."
}

$baseUrl = "https://eos.manyuvip.com"
$model = "gpt-image-2"
[Environment]::SetEnvironmentVariable("IMAGEGEN_BASE_URL", $baseUrl, "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_API_KEY", $apiKey, "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_MODEL", $model, "User")
$env:IMAGEGEN_BASE_URL = $baseUrl
$env:IMAGEGEN_API_KEY = $apiKey
$env:IMAGEGEN_MODEL = $model

& $pythonCommand.Source @pythonArgs $script --check-config
if ($LASTEXITCODE -ne 0) {
    throw "The local configuration check failed."
}

Write-Host ""
Write-Host "Matrixapi-imagegen was installed for Codex. Restart Codex before using it."
Write-Host "Install location: $target"
Write-Host "Supported models: gpt-image-2, gpt-image-2-pro. Current model: $model"
Write-Host "The Skill accepts only https://eos.manyuvip.com."
Read-Host "Press Enter to close"
