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
               Its console output is appended to
               runtime\logs\scheduled_runner.log -- see the block above
               $commandLine for why a scheduled run with nowhere to
               print is a run whose failure has no explanation anywhere.

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

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "run_company_ops.py not found at $entrypoint - run this script from the repository's scripts\ directory."
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $python) {
    throw 'python was not found on PATH. Install Python or add it to PATH before registering the task.'
}

# ------------------------------------------------------------------------
# The task name and its log file, asked of the one place that defines them.
#
# Both used to be literals here as well as in `src/schedtask.py`, kept in
# step by a test that read the two and compared them. That test could only
# ever *detect* the drift; it could not stop it, and a repository that keeps
# removing hand-written rosters should not have added one across two
# languages.
#
# `schedtask` is the source rather than these scripts because it is the side
# that reasons about the values -- `ops_status.py` has to know the name to
# query Windows for it, `scheduled_log_name()` maps name to log, and
# `agent_task_name()` builds the per-Desktop form. PowerShell only writes
# them down once, at install time.
#
# No new dependency: this script already requires python on PATH and already
# bakes `$python.Source` into the action it registers.
#
# **Failing here is the right failure.** If `import schedtask` does not work
# from this checkout, the task this script would register runs an
# entrypoint that imports the same tree every morning. Refusing to register
# is better than registering something that cannot run, and the check costs
# one interpreter start.
$srcDir = Join-Path $repoRoot 'src'
$taskProbe = @"
import sys
sys.path.insert(0, r'$srcDir')
import schedtask
name = schedtask.RUNNER_TASK_NAME
print(name)
print(schedtask.scheduled_log_name(name))
"@
$taskFacts = @(& $python.Source -c $taskProbe)
if ($LASTEXITCODE -ne 0 -or $taskFacts.Count -lt 2 -or
    [string]::IsNullOrWhiteSpace($taskFacts[0]) -or
    [string]::IsNullOrWhiteSpace($taskFacts[1])) {
    throw @"
Could not read the task name from src\schedtask.py.

  python  : $($python.Source)
  src     : $srcDir
  output  : $($taskFacts -join ' | ')

That module is where this project defines the scheduled task names and the
log each one writes. If it cannot be imported from this checkout, the task
this script would register would run an entrypoint that imports the same
tree -- so nothing is registered.
"@
}
$taskName = $taskFacts[0].Trim()
$logFileName = $taskFacts[1].Trim()

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

# ------------------------------------------------------------------------
# Where the scheduled run's console output goes.
#
# It went nowhere. The action was `python.exe <entrypoint>` with no
# redirection, so a scheduled task's stdout and stderr were written to
# handles nothing was reading and discarded.
#
# That is not a cosmetic loss, and the repository already knew it: five
# entrypoints carry a measured comment about `line_buffering=True` being
# needed because "under `> log 2>&1`, which is how a scheduled run is
# captured, the two streams reorder against each other". They were
# protecting the ordering of output that no installer ever captured.
#
# What is lost with it is the whole of the diagnosis for the failures that
# happen OUTSIDE the application. Everything `ops_status.py` can tell you is
# derived from files this system wrote; a run that dies before it writes any
# of them leaves nothing but a `LastTaskResult` number:
#
#     python not on PATH             cmd: the system cannot find the path
#     working directory gone         Python: can't open file '...'
#     an import failure              a traceback, and nothing else, anywhere
#     COMPANY_OPS_* unset            [FAILED] <name> is not set  (exit 1)
#
# The last one is the likeliest of the four and the worst: it exits 1 every
# single morning, writes nothing under `runtime/`, and the sentence naming
# the missing variable is printed to a stream that is thrown away.
#
# `cmd.exe /c` rather than a PowerShell wrapper: it is the smallest thing
# that can redirect, and -- measured, because the whole point is that the
# exit code stays meaningful -- it returns the child's exit code unchanged:
#
#     cmd /c ""python.exe" "prog.py" >> "log" 2>&1"   ->  7   (prog exits 7)
#     ...with a python.exe that does not exist         ->  1, and the log
#                                                          says which path
#
# So `LastTaskResult` keeps meaning what docs/14 section 4 says it means, and the
# SCHEDULE block in `ops_status.py` keeps reading it the same way.
#
# Appended, not overwritten. The record of a failure is exactly what this
# exists to stop losing, and `>` would let the next morning's run erase
# yesterday's traceback. The volume is bounded by how often the task fires
# (twice a day at most) times a few dozen lines, which is smaller than
# `collector.log`, whose growth BACKLOG section D measured and found immaterial at
# this scale.
$logDir = Join-Path $repoRoot 'runtime\logs'
$logPath = Join-Path $logDir $logFileName

# `>>` fails if the directory is absent, and `runtime/` is git-ignored -- so
# on a fresh clone it does not exist until something creates it. Without
# this line the first scheduled run on a new machine would fail at the
# redirection itself, which is the opposite of what the redirection is for.
# `-WhatIf` propagates into `New-Item` from this script's CmdletBinding, so
# a preview still creates nothing.
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# `$env:ComSpec` rather than the bare name, so the action does not depend on
# PATH resolution inside a service-started task; the fallback is the one
# fixed location Windows guarantees.
$comspec = $env:ComSpec
if ([string]::IsNullOrWhiteSpace($comspec)) {
    $comspec = Join-Path $env:SystemRoot 'System32\cmd.exe'
}

# The entrypoint still receives NO arguments -- every tool here refuses ones
# it cannot honour (`cli.unexpected_arguments`), so an extra token would
# make the task exit 1 forever. Everything after the entrypoint is cmd's
# redirection, which cmd consumes and never passes on.
$commandLine = '/c ""{0}" "{1}" >> "{2}" 2>&1"' -f $python.Source, $entrypoint, $logPath

$action = New-ScheduledTaskAction `
    -Execute $comspec `
    -Argument $commandLine `
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
    Write-Host 'Set them in the USER environment -- NOT only in this shell.'
    Write-Host 'A scheduled task inherits no interactive shell, which is why this'
    Write-Host 'script persists its own variable that way. A shell export makes the'
    Write-Host 'tool work when YOU run it and changes nothing about the scheduled'
    Write-Host 'run: Notion Sync keeps reporting SKIPPED and the run still exits 0.'
    Write-Host 'docs/13_NOTION_ENVIRONMENT_SETUP.md section 2.1 has the command and'
    Write-Host 'the check that shows process/user/machine scope separately.'
    Write-Host ''
    Write-Host 'Without them the Runner still collects, writes Company History and'
    Write-Host 'backs up; only Notion Sync is skipped. `python ops_status.py` reports'
    Write-Host 'their absence.'
}
