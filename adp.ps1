<#
.SYNOPSIS
    Run the ADP Trust Engine using this repository's virtual environment,
    whether or not the venv is currently activated.

.DESCRIPTION
    The common footgun with a Python venv is running a command through a bare
    'python' that PATH resolves to the wrong interpreter (a system Python without
    this project's dependencies), which then fails with misleading errors such as
    "Can't load plugin: sqlalchemy.dialects:teradatasql".

    This wrapper always invokes '.venv\Scripts\python.exe' next to itself, by full
    path, so the interpreter is never in doubt. It forwards every argument through
    to the Trust Engine unchanged.

    Note: this is deliberately a SIMPLE script (no [CmdletBinding()]). That is what
    lets $args capture GNU-style long options such as --prefix verbatim; an
    advanced-function param block would try to bind '--prefix' and fail.

.EXAMPLE
    .\adp.ps1 validate --prefix CallCentre --output reports\CallCentre-validation.json --html-output reports\CallCentre-validation.html

.EXAMPLE
    .\adp.ps1 mcp-server --transport streamable-http --host 127.0.0.1 --port 8002
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The venv Python is resolved relative to THIS script, so the wrapper works from
# any current directory and for anyone who clones the repo.
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Error ("[AdpVenvMissing] Virtual environment not found at '$venvPython'. " +
        "Create it and install the project, e.g.: " +
        "py -3.13 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e `".[mcp,teradata]`"")
    exit 1
}

# Forward all arguments to the Trust Engine under the venv interpreter.
& $venvPython -m ai_native_data_product_trust_engine @args

# Surface the underlying exit code to the caller (so CI / scripts can react).
exit $LASTEXITCODE
