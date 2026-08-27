$ErrorActionPreference = "Stop"

$source = Join-Path $PSScriptRoot "skills\Matrixapi-imagegen"
$target = Join-Path $env:USERPROFILE ".codex\skills\Matrixapi-imagegen"
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

New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $target -Recurse -Force

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

$model = "gpt-image-2"
[Environment]::SetEnvironmentVariable("IMAGEGEN_API_KEY", $apiKey, "User")
[Environment]::SetEnvironmentVariable("IMAGEGEN_MODEL", $model, "User")
$env:IMAGEGEN_API_KEY = $apiKey
$env:IMAGEGEN_MODEL = $model

& $pythonCommand.Source @pythonArgs $script --check-config
if ($LASTEXITCODE -ne 0) {
    throw "The local configuration check failed."
}

Write-Host ""
Write-Host "Matrixapi-imagegen was installed for Codex. Restart Codex before using it."
Write-Host "Install location: $target"
Write-Host "API URL is fixed inside the Skill: https://matrixapii.com"
Write-Host "Installed version: 1.8.11"
Write-Host "Current model: $model"
Write-Host "Supported models: gpt-image-2, gpt-image-2-pro"
Read-Host "Press Enter to close"
