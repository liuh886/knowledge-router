# Thin user commands. All path selection, formatting, writes and retries belong to Router.
function global:kr {
    $entry = if ($env:KNOWLEDGE_ROUTER_ENTRYPOINT) { $env:KNOWLEDGE_ROUTER_ENTRYPOINT } else {
        Join-Path $HOME '.codex/skills/knowledge-router/scripts/knowledge_router.py'
    }
    if (-not [System.IO.Path]::IsPathFullyQualified($entry) -or -not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        throw "Knowledge Router entrypoint unavailable: $entry"
    }
    & python $entry @args
}

function global:Invoke-PersonalCapture {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('todo','memo','idea','biography','investment')][string]$Kind,
        [Parameter(Mandatory=$true)][string]$Text,
        [string]$Tier
    )
    $vaultName = if ($env:KNOWLEDGE_ROUTER_VAULT) { $env:KNOWLEDGE_ROUTER_VAULT } else { 'zhihaol' }
    $intentKey = 'shell-' + [guid]::NewGuid().ToString('N')
    $captureArgs = @('streams', 'append', $Kind, $Text, '--vault', $vaultName, '--idempotency-key', $intentKey)
    if ($Tier) { $captureArgs += @('--tier', $Tier) }
    kr @captureArgs
    if ($LASTEXITCODE -ne 0) {
        $retryTier = if ($Tier) { " --tier $Tier" } else { '' }
        Write-Warning "Capture did not finish successfully. Inspect Router output; do not repeat with a new ID. Retry: kr streams append $Kind <same text> --vault $vaultName --idempotency-key $intentKey$retryTier"
    }
}

function global:todo {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Message)
    Invoke-PersonalCapture -Kind todo -Text ($Message -join ' ')
}
function global:memo {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Message)
    Invoke-PersonalCapture -Kind memo -Text ($Message -join ' ')
}
function global:idea {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Message)
    Invoke-PersonalCapture -Kind idea -Text ($Message -join ' ')
}
function global:milestone {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Message)
    Invoke-PersonalCapture -Kind biography -Text ($Message -join ' ')
}
Set-Alias -Name bio -Value milestone -Scope Global -ErrorAction SilentlyContinue
function global:invest {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Message)
    Invoke-PersonalCapture -Kind investment -Text ($Message -join ' ')
}
