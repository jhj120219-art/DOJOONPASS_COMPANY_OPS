<#
.SYNOPSIS
    Registers the Company Ops Runner on Desktop 4.

.DESCRIPTION
    Creates the Windows Task Scheduler task docs/11 section 19 names:

        DOJOONPASS_COMPANY_OPS_DAILY

    Triggers:  Daily at 11:00 (docs/07 section 4's regular run), plus
               At log on (docs/11 section 20's Startup Catch-up, for the
               mornings the PC was off at 11:00).
    Action:    python run_company_ops.py, once, then the process exits.

    The Runner is the one machine-level job that turns collected Events into
    Company History, backs it up, and syncs Notion. Until this script
    existed, Desktop 4's task had to be built by hand from the runbook's
    prose while the Agent -- the secondary, reporting-side job -- had a
    tested installer. Every lesson that installer paid for applies here and
    more so, because there is exactly one Desktop 4:

      -User on the logon trigger   without it the trigger is machine-wide,
                                   which a non-administrator cannot register
                                   at all, and which would fire this Desktop's
                                   Runner at any account's logon
      MultipleInstances IgnoreNew  docs/07 section 55, on top of the
                                   application's own system-wide lock
      StartWhenAvailable           a missed 11:00 (PC off) fires at the next
                                   opportunity instead of being dropped
      ShouldProcess on env writes  -WhatIf must change nothing, including
                                   the user environment

.PARAMETER HistoryStartDate
    COMPANY_OPS_HISTORY_START_DATE (docs/07 section 50). The date Company
    History begins. Never guessed by the system, so it is required here.
    Format: YYYY-MM-DD.

.PARAMETER DailyAt
    The regular run time. Defaults to 11:00 (docs/07 section 4).

.PARAMETER DelayMinutes
    Delay after logon before the catch-up run, so network and git are ready
    (docs/07 section 54). Does not apply to the daily trigger, which already
    names a wall-clock time.

.PARAMETER WhatIf
    Show what would be registered and change nothing -- neither the task nor
    the user environment.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_runner_task.ps1 `
        -HistoryStartDate 2026-08-10 -WhatIf

.NOTES
    This script deliberately does NOT accept or store the Notion
    credentials. NOTION_API_TOKEN is a secret, and a parameter would put it
    in the command line, the process list, and PowerShell history. The
    operator sets those separately; the script prints what is still needed
    and the Runner reports their absence on every run
    (`ops_status.py` raises it as ATTENTION).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$HistoryStartDate,

    [string]$DailyAt = '11:00',

    [int]$DelayMinutes = 2
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$entrypoint = Join-Path $repoRoot 'run_company_ops.py'
$taskName = 'DOJOONPASS_COMPANY_OPS_DAILY'

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "run_company_ops.py not found at $entrypoint - run this script from the repository's scripts\ directory."
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $python) {
    throw 'python was not found on PATH. Install Python or add it to PATH before registering the task.'
}

# Refused rather than warned about, unlike the Agent's -SyncFolder. A date
# the Runner cannot read stops the run at its configuration check every
# single morning, and the value is fully checkable here.
try {
    [void][datetime]::ParseExact($HistoryStartDate, 'yyyy-MM-dd', $null)
}
catch {
    throw "HistoryStartDate '$HistoryStartDate' is not a real date (YYYY-MM-DD)."
}

# The Runner reads its configuration from the environment and a scheduled
# task inherits no interactive shell, so the one non-secret variable is
# persisted here.
#
# Guarded by ShouldProcess: -WhatIf exists to change nothing, and the Agent
# installer's equivalent lines once rewrote three user variables during a
# preview.
if ($PSCmdlet.ShouldProcess('user environment', 'Set COMPANY_OPS_HISTORY_START_DATE')) {
    [Environment]::SetEnvironmentVariable('COMPANY_OPS_HISTORY_START_DATE', $HistoryStartDate, 'User')
}

$action = New-ScheduledTaskAction `
    -Execute $python.Source `
    -Argument "`"$entrypoint`"" `
    -WorkingDirectory $repoRoot

# docs/07 section 4: the regular run. No delay -- it already names a
# wall-clock time, and delaying it would make 11:00 mean something else.
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyAt

# docs/11 section 20 / docs/07 section 53: Startup Catch-up, for the morning
# the PC was off at 11:00. `-User` is mandatory -- see the .DESCRIPTION note
# and the Agent installer, where its absence meant the task had never
# registered on any non-administrator machine.
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$logonTrigger.Delay = "PT$($DelayMinutes)M"

$triggers = @($dailyTrigger, $logonTrigger)

# docs/07 section 55. The Runner already holds a system-wide lock and a
# second instance would exit as SKIPPED_ALREADY_RUNNING, so this is the
# Windows-level half of the same protection rather than the only one.
# ExecutionTimeLimit is generous because a first run can catch up many days
# and the git push alone allows 300 s.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if ($PSCmdlet.ShouldProcess($taskName, 'Register scheduled task')) {
    try {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $triggers `
            -Settings $settings `
            -Description "DOJOONPASS Company Ops Runner (Desktop 4). Daily at $DailyAt, plus a catch-up run at logon for the mornings the PC was off." `
            -Force -ErrorAction Stop | Out-Null
    }
    catch {
        throw @"
Scheduled task registration was refused by Windows: $($_.Exception.Message)

The task's definition is valid; Windows refused to write it. A
non-administrator CAN normally register a per-user task, so this is usually
a policy or scope problem rather than a missing privilege.

Things to check, in order:

  1. Task Scheduler service is running:
     Get-Service Schedule
  2. Your account may create tasks in the root folder -- some managed
     machines restrict it. Test with a throwaway task:
     Register-ScheduledTask -TaskName Probe1 ``
       -Action (New-ScheduledTaskAction -Execute cmd.exe -Argument '/c exit') ``
       -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(1))
     If that succeeds, the restriction is not a blanket one.
  3. Only if both of the above are fine: run from an elevated PowerShell.

Nothing was registered. COMPANY_OPS_HISTORY_START_DATE was set before this
point and is harmless on its own; re-running after fixing the above is safe
and idempotent (-Force replaces any half-registered task).
"@
    }

    $registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $registered) {
        # Reporting success on a task that is not there would send the
        # operator away believing Company History is scheduled when nothing
        # is - the silent failure this whole step exists to avoid.
        throw "Register-ScheduledTask reported success but '$taskName' does not exist. Nothing is scheduled."
    }

    Write-Host "Registered: $taskName"
    Write-Host "  entrypoint  : $entrypoint"
    Write-Host "  history from: $HistoryStartDate"
    Write-Host "  triggers    : Daily $DailyAt + AtLogOn (delay ${DelayMinutes}m)"
    Write-Host ''
    Write-Host 'Still required, and NOT set by this script (they are secrets):'
    Write-Host '  NOTION_API_TOKEN'
    Write-Host '  NOTION_PROJECTS_DATABASE_ID'
    Write-Host '  NOTION_OPS_RUNS_DATABASE_ID   (optional - Operations Dashboard)'
    Write-Host ''
    Write-Host 'Without them the Runner still collects, writes Company History and'
    Write-Host 'backs up; only Notion Sync is skipped. `python ops_status.py` reports'
    Write-Host 'their absence.'
}
