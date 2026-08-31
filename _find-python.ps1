$ErrorActionPreference = 'SilentlyContinue'

foreach ($version in @('3.12', '3.13', '3.11', '3.10')) {
    $candidate = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $candidate) {
        Write-Output $candidate
        exit 0
    }
}

$fallback = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if ($fallback) {
    $supported = & $fallback -c "import sys; print(int((3,10) <= sys.version_info[:2] <= (3,13)))" 2>$null
    if ($supported -eq '1') {
        Write-Output $fallback
        exit 0
    }
}

exit 1

# Created by FajkPes with assistance from ChatGPT Codex and Claude Code.
