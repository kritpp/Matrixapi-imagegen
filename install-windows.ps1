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
Write-Host "Installed version: 1.9.0"
Write-Host "Current model: $model"
Write-Host "Supported models: gpt-image-2, gpt-image-2.5-flare, gpt-image-2.5-sunburst, gpt-image-2.6-flare, gpt-image-2.6-sunburst, gemini-3-pro-image-preview, midjourney-v8.2, midjourney-v8.2-fast, midjourney-v8.2-turbo"
Write-Host "Shortcuts anywhere in prompt: -f2.5 / -s2.5 / -f2.6 / -s2.6 / -m8 / -m8f / -m8t"
Write-Host "MJ group default: midjourney-v8.2; Fast/Turbo require -m8f/-m8t. MJ: 4 images per request; ratio 16:9 or --ar 16:9; native pixels, no exact 4K size parameter."
Read-Host "Press Enter to close"

