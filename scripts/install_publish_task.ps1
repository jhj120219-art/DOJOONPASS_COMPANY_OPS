<#
.SYNOPSIS
    Registers the Control Tower publish on Desktop 4.

.DESCRIPTION
    Creates the Windows Task Scheduler task:

        DOJOONPASS_COMPANY_OPS_PUBLISH

    Triggers:  Daily at 11:30 (after the Runner's 11:00 default, so the
               page shows the day's run), plus At log on with a delay, for
               the mornings the PC was off.
    Action:    python publish_control_tower.py, once, then the process
               exits. Its console output is appended to
               runtime\logs\scheduled_publish.log.

    This registration was already a documented operational step and had no
    script. AGENT.md section 6c tells the operator to register this tool "in
    Task Scheduler beside run_company_ops.py", and publish_control_tower.py
    was given exit code 3 (DEGRADED) for exactly that deployment -- its
    docstring says so: "Task Scheduler's only automatic health signal is the
    exit code". So the tool is built for a schedule that nothing creates,
    and every operator had to build it by hand from prose. That is the same
    gap the Runner installer closed: "Desktop 4's task had to be built by
    hand from the runbook's prose".

    Why it matters more than a convenience. The Notion Control Tower page is
    the seat for everyone who does NOT open a terminal -- the whole point of
    publishing it. Until it is scheduled, that page only ever refreshes when
    somebody opens a terminal, and dashboard_server.py says so on its own
    face: "automatic execution: none -- it does not refresh itself".

    Ordering against the Runner is best-effort, and safe that way. publish
    reads local evidence and rewrites its own Notion page; it writes no
    Event, takes no lock, and touches nothing under runtime\. Publishing
    before the Runner has finished shows slightly older numbers for a few
    minutes and the next trigger corrects them. So the default times sequence
    the two, and nothing depends on the sequence holding.

.PARAMETER DailyAt
    The regular publish time. Defaults to 11:30 -- half an hour after the
    Runner installer's 11:00 default. If you moved the Runner, move this.

.PARAMETER DelayMinutes
    Delay after logon before the catch-up publish. Longer than the Runner's
    (docs/07 section 54 uses 2) so a startup catch-up run has usually
    finished first: same best-effort sequencing, same reason it is safe.

.PARAMETER WhatIf
    Show what would be registered and change nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_publish_task.ps1 -WhatIf

.NOTES
    This script writes NO environment variable, unlike the Runner and Agent
    installers. publish_control_tower.py reads exactly NOTION_API_TOKEN and
    NOTION_PROJECTS_DATABASE_ID, and both are secrets: a parameter would put
    them in the command line, the process list and PowerShell history. The
    operator sets those; the script prints what is still needed.

    Without them the task runs and exits 1 every day. That is now visible
    rather than silent -- the exit code reaches LastTaskResult, and
    ops_status.py's SCHEDULE block reports it together with the last lines
    of this task's log.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$DailyAt = '11:30',

    [int]$DelayMinutes = 10
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$entrypoint = Join-Path $repoRoot 'publish_control_tower.py'

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "publish_control_tower.py not found at $entrypoint - run this script from the repository's scripts\ directory."
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
name = schedtask.PUBLISH_TASK_NAME
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

# ------------------------------------------------------------------------
# Where the scheduled run's console output goes.
#
# The same reasoning the Runner and Agent installers carry, and it applies
# here with one extra edge: this tool's normal report is four lines of
# "which surface was written", and its DEGRADED path names the surfaces that
# were not. All of that is stdout and stderr. Without a redirection the only
# thing that survives a publish is the exit code, so "3" would tell an
# operator that a surface failed and nothing would tell them which.
#
# Appended, not overwritten: the record of a failure is what this exists to
# keep, and this task fires daily.
$logDir = Join-Path $repoRoot 'runtime\logs'
$logPath = Join-Path $logDir $logFileName

# `>>` fails if the directory is absent, and runtime\ is git-ignored -- on a
# fresh clone it does not exist until something creates it. -WhatIf
# propagates into New-Item from this script's CmdletBinding, so a preview
# still creates nothing.
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# $env:ComSpec rather than the bare name, so the action does not depend on
# PATH resolution inside a service-started task.
$comspec = $env:ComSpec
if ([string]::IsNullOrWhiteSpace($comspec)) {
    $comspec = Join-Path $env:SystemRoot 'System32\cmd.exe'
}

# The entrypoint receives NO arguments -- publish_control_tower.py refuses
# any it cannot honour (cli.unexpected_arguments) and would exit 1 forever.
# Everything after the entrypoint is cmd's redirection, which cmd consumes.
$commandLine = '/c ""{0}" "{1}" >> "{2}" 2>&1"' -f $python.Source, $entrypoint, $logPath

$action = New-ScheduledTaskAction `
    -Execute $comspec `
    -Argument $commandLine `
    -WorkingDirectory $repoRoot

# docs/07 section 4's shape, half an hour later. No delay on a trigger that
# already names a wall-clock time.
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyAt

# -User is not optional. Without it -AtLogOn means "when ANY user logs on",
# which is machine-wide and which Windows refuses to register from a
# non-elevated session -- the single missing argument that meant the Agent
# installer had never registered a task on any non-administrator machine.
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$logonTrigger.Delay = "PT$($DelayMinutes)M"

$triggers = @($dailyTrigger, $logonTrigger)

# docs/07 section 55's shape. This tool takes no lock of its own -- it is
# read-only except for its own Notion page -- so IgnoreNew is the only thing
# stopping two publishes rewriting the same page at once.
# ExecutionTimeLimit is one hour: this is a handful of Notion writes, and a
# publish still running after an hour is one that has stopped making
# progress.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

if ($PSCmdlet.ShouldProcess($taskName, 'Register scheduled task')) {
    try {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $triggers `
            -Settings $settings `
            -Description "DOJOONPASS Company Ops Control Tower publish (Desktop 4). Daily at $DailyAt, plus a catch-up publish at logon." `
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

Nothing was registered. This script writes no environment variable, so there
is nothing to undo; re-running after fixing the above is safe and idempotent
(-Force replaces any half-registered task).
"@
    }

    $registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $registered) {
        # Reporting success on a task that is not there would send the
        # operator away believing the Control Tower refreshes itself while
        # nothing does - the silent failure this whole step exists to avoid.
        throw "Register-ScheduledTask reported success but '$taskName' does not exist. Nothing is scheduled."
    }

    Write-Host "Registered: $taskName"
    Write-Host "  entrypoint : $entrypoint"
    Write-Host "  log        : $logPath"
    Write-Host "  triggers   : Daily $DailyAt + AtLogOn (delay ${DelayMinutes}m)"
    Write-Host ''
    Write-Host 'Required, and NOT set by this script (they are secrets):'
    Write-Host '  NOTION_API_TOKEN'
    Write-Host '  NOTION_PROJECTS_DATABASE_ID'
    Write-Host ''
    Write-Host 'Set them in the USER environment -- NOT only in this shell.'
    Write-Host 'A scheduled task inherits no interactive shell, which is why this'
    Write-Host 'script persists its own variable that way. A shell export makes the'
    Write-Host 'tool work when YOU run it and changes nothing about the scheduled'
    Write-Host 'run: Notion Sync keeps reporting SKIPPED and the run still exits 0.'
    Write-Host 'docs/13_NOTION_ENVIRONMENT_SETUP.md section 2.1 has the command and'
    Write-Host 'the check that shows process/user/machine scope separately.'
    Write-Host ''
    Write-Host 'Without them this task exits 1 every day. That is visible, not silent:'
    Write-Host '`python ops_status.py` reports the exit code and the end of the log above.'
}
