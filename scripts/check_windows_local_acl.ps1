# Windows-only, read-only ACL diagnostic for the local application directory.
# Does not read file contents or modify permissions; no admin rights required.
# Exit codes: 0 no broad-read ACL detected, 2 exposure, 3 incomplete check,
# 4 not Windows. Output is file names and SID classification, never secrets.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    Write-Output 'NOT_WINDOWS: this check only supports Windows ACLs.'
    exit 4
}
$folder = Join-Path $HOME '.ai-automation-studio'
$paths = @(
    $folder,
    (Join-Path $folder 'operator.json'),
    (Join-Path $folder 'public-github-sources.json'),
    (Join-Path $folder 'security-audit.sqlite3'),
    (Join-Path $folder 'local-workflows.sqlite3')
)
# SIDs with access that is not exclusive to the current owner/Admins/System:
$untrusted = @(
    'S-1-1-0',       # Everyone
    'S-1-5-11',      # Authenticated Users
    'S-1-5-32-545',  # BUILTIN Users
    'S-1-5-32-546',  # Guests
    'S-1-5-7'        # Anonymous logon
)
$foundRisk = $false
$incomplete = $false
foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Output ("NOT_PRESENT: " + (Split-Path -Leaf $path))
        if ($path -eq $folder -or $path -eq (Join-Path $folder 'operator.json')) {
            $incomplete = $true
        }
        continue
    }
    $name = Split-Path -Leaf $path
    try {
        $acl = Get-Acl -LiteralPath $path
    } catch {
        Write-Output ("INCOMPLETE: cannot inspect ACL for " + $name)
        $incomplete = $true
        continue
    }
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        try {
            $sid = $ace.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]).Value
        } catch {
            Write-Output ("INCOMPLETE: unresolved ACL identity on " + $name)
            $incomplete = $true
            continue
        }
        if ($untrusted -contains $sid) {
            Write-Output ("RISK: broad access on " + $name + " via SID " + $sid)
            $foundRisk = $true
        }
    }
    if (-not ($acl.Access | Where-Object {
        $_.AccessControlType -eq 'Allow'
    })) {
        Write-Output ("INCOMPLETE: no readable allow-ACE on " + $name)
        $incomplete = $true
    }
}
if ($foundRisk) {
    Write-Output 'RESULT: broad Windows ACL exposure detected. Investigate before storing personal data.'
    exit 2
}
if ($incomplete) {
    Write-Output 'RESULT: incomplete ACL assurance. Review as current Windows account.'
    exit 3
}
Write-Output 'RESULT: no broad well-known SID allow entries detected; not a full security certification.'
exit 0
