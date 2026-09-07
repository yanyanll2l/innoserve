param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue

if (Test-Path -LiteralPath $bundledPython) {
    $pythonExecutable = $bundledPython
} elseif ($pythonCommand) {
    $pythonExecutable = $pythonCommand.Source
} else {
    $pythonExecutable = $bundledPython
}

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Python 3.11 or later was not found. Install Python or add python.exe to PATH."
}

Push-Location $scriptRoot
try {
    if ($Command -eq "help") {
        & $pythonExecutable -m welfare_backend.cli --help
    } elseif ($Command -eq "test") {
        & $pythonExecutable -m unittest discover -s tests -v @Rest
    } elseif ($Command -eq "match") {
        $profileText = $Rest -join " "
        $profileBytes = [System.Text.Encoding]::UTF8.GetBytes($profileText)
        $profileArgument = "base64:" + [Convert]::ToBase64String($profileBytes)
        & $pythonExecutable -m welfare_backend.cli match $profileArgument
    } else {
        & $pythonExecutable -m welfare_backend.cli $Command @Rest
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
