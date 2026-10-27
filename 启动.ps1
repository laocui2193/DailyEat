$env:ELECTRON_RUN_AS_NODE = ''
Set-Location $PSScriptRoot
& "$PSScriptRoot\node_modules\electron\dist\electron.exe" $PSScriptRoot
