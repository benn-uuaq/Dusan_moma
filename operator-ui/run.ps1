$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$projectRoot\.deps;$projectRoot\src"
python -m smr_operator_ui
