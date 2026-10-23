# Banquet Concierge launcher (Windows PowerShell).
#
# Always start the app through this script: it keeps Python's bytecode cache and the
# Playwright browser INSIDE the project folder (.cache\), as CLAUDE.md requires.
#
#   .\run.ps1 serve [--db data\other.db] [--simulated]   web app on http://127.0.0.1:8000
#   .\run.ps1 cli [--verbose] [--db ...]                    terminal chat
#   .\run.ps1 test                                          offline test suite
#   .\run.ps1 scenarios [--live --max-cost 5]               harness scenarios (simulated by default)
#   .\run.ps1 install-browser                               Chromium for the PDF guide, into .cache\
#   .\run.ps1 guide                                         screenshots + docs\how-it-works.pdf
param([Parameter(Position = 0)][string]$Command = "help",
      [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)

$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$env:PYTHONPYCACHEPREFIX = Join-Path $Root ".cache\pycache"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root ".cache\ms-playwright"
# Temporary files (e.g. Chromium's throwaway profile while building the PDF) also stay in the project.
$Tmp = Join-Path $Root ".cache\tmp"
New-Item -ItemType Directory -Force $Tmp | Out-Null
$env:TEMP = $Tmp
$env:TMP = $Tmp
Set-Location $Root

switch ($Command) {
    "serve"           { & $Py -m banquet.serve @Rest }
    "cli"             { & $Py -m banquet.cli @Rest }
    "test"            { & $Py -m pytest -q @Rest }
    "scenarios"       { & $Py -m banquet.harness.run_scenarios @Rest }
    "install-browser" { & $Py -m playwright install chromium }
    "guide"           { & $Py -m banquet.guide @Rest }
    default           { Get-Content (Join-Path $Root "run.ps1") -TotalCount 11 }
}
exit $LASTEXITCODE
