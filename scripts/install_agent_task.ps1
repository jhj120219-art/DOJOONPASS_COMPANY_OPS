<#
.SYNOPSIS
    Registers the Multi-Desktop Agent to run when this user logs on.

.DESCRIPTION
    Creates one Windows Task Scheduler task per Desktop:

        DOJOONPASS_COMPANY_OPS_AGENT_<DESKTOP_ID>

    Trigger:  At log on (docs/07_SCHEDULER_CATCHUP_SPEC.md section 53's
              STARTUP task shape, applied to the Agent).
    Action:   python run_agent.py, once, then the process exits. Its
              console output is appended to
              runtime\logs\scheduled_agent.log -- see the block above
              $commandLine for why a scheduled run with nowhere to
              print is a run whose failure has no explanation anywhere.

    This deliberately does NOT keep the PC awake, wake it on a schedule, or
    require it to stay on. docs/07 section 58 settles that question for
    Company Ops -- allowing the PC to be OFF, with catch-up, is the better
    shape -- and the Agent is built the same way: whenever the machine next
    comes on, one run catches up every date since
    `last_successful_collection_date`. Missing a day costs nothing but a
    slightly longer catch-up.

    A daily trigger is offered too (-DailyAt) for a machine that stays on
    across midnight and would otherwise not log on again for days. It is
    optional precisely because it is not the safety mechanism -- catch-up is.

    Both Windows-level and application-level duplicate protection are used,
    per docs/07 section 55: the task is registered with
    MultipleInstances=IgnoreNew, and the Agent additionally takes its own
    lock file (src/scheduler/lock.py) so an overlapping manual run is
    refused as well.

.PARAMETER DesktopId
    DESKTOP_1 | DESKTOP_2 | DESKTOP_3 | DESKTOP_4. Sets COMPANY_OPS_PROFILE.
    The role is NOT set here -- it comes from src/reporter/profiles.py, which
    is docs/02 section 8's own source->role table.

.PARAMETER SyncFolder
    The OneDrive Sync Folder shared with Desktop 4. Sets
    COMPANY_OPS_AGENT_SYNC_FOLDER.

.PARAMETER StartDate
    YYYY-MM-DD. The first date this Desktop should ever collect. Never
    guessed (docs/07 section 50). Sets COMPANY_OPS_AGENT_START_DATE.

.PARAMETER DelayMinutes
    Startup delay, default 2. docs/07 section 54: right after logon, network
    and OneDrive may not be ready yet; a short delay avoids a guaranteed
    first failure. Kept short on purpose -- the section also warns against
    padding it without evidence, and a failed run is recoverable anyway.

.PARAMETER DailyAt
    Optional extra trigger, e.g. "11:00". Omit for logon-only.

.PARAMETER WhatIf
    Show what would be registered without registering it.

.EXAMPLE
    .\install_agent_task.ps1 -DesktopId DESKTOP_1 `
        -SyncFolder "C:\Users\me\OneDrive\CompanyOpsEvents" `
        -StartDate 2026-08-10

.NOTES
    No secret is read, written, or stored by this script. The three
    environment values it persists are an identifier, a folder path, and a
    date -- NOTION_API_TOKEN and friends are Desktop 4's business and are
    never needed by an Agent.

    Uninstall:  Unregister-ScheduledTask -TaskName DOJOONPASS_COMPANY_OPS_AGENT_<ID>
    Inspect:    Get-ScheduledTask -TaskName DOJOONPASS_COMPANY_OPS_AGENT_*
    Run now:    Start-ScheduledTask -TaskName DOJOONPASS_COMPANY_OPS_AGENT_<ID>
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('DESKTOP_1', 'DESKTOP_2', 'DESKTOP_3', 'DESKTOP_4')]
    [string]$DesktopId,

    [Parameter(Mandatory = $true)]
    [string]$SyncFolder,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$StartDate,

    [int]$DelayMinutes = 2,

    [string]$DailyAt
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$entrypoint = Join-Path $repoRoot 'run_agent.py'

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "run_agent.py not found at $entrypoint - run this script from the repository's scripts\ directory."
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
name = schedtask.agent_task_name('$DesktopId')
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

# A warning, not a throw: `transport.onedrive._write_atomic()` creates
# whatever directory it is given (`mkdir(parents=True, exist_ok=True)`), so
# a typo'd -SyncFolder does not fail the Agent's first run -- it silently
# succeeds into a directory nothing actually syncs to Desktop 4. Every run
# after that reports COLLECTED, the outbox stays empty, and nothing on this
# machine is ever wrong-looking; only Desktop 4's generic "this Desktop has
# been silent" alarm (app/desktop_activity.py) would eventually catch it,
# days later, with no hint that the cause is a path typo rather than the
# machine being off. Not requiring the folder to already exist on purpose:
# OneDrive may not have created/synced it yet at install time, which is a
# legitimate "install now, folder appears shortly after" sequence -- see
# this script's own -DelayMinutes precedent for the same "warn, do not
# block" stance toward timing the installer cannot control.
if (-not (Test-Path -LiteralPath $SyncFolder)) {
    Write-Warning "SyncFolder '$SyncFolder' does not exist yet. If this is a fresh OneDrive share still syncing, that is normal and this can be ignored. If it is a typo, the Agent will start writing Events into a folder Desktop 4 never sees, and every run will still report success."
}

# The Agent reads its configuration from the environment. A scheduled task
# does not inherit an interactive shell's variables, so they are persisted
# to the user's environment here. All three are non-secret by construction.
#
# Guarded by ShouldProcess like the registration below. These three lines
# used to run unconditionally, above the only ShouldProcess check in the
# script, so `-WhatIf` -- the flag whose entire purpose is "show me what
# would happen and change nothing" -- permanently rewrote three user
# environment variables. Previewing an install with the wrong -DesktopId
# therefore repointed that machine's Agent identity for real, while
# reporting that nothing had been done.
if ($PSCmdlet.ShouldProcess('user environment', 'Set COMPANY_OPS_* variables')) {
    [Environment]::SetEnvironmentVariable('COMPANY_OPS_PROFILE', $DesktopId, 'User')
    [Environment]::SetEnvironmentVariable('COMPANY_OPS_AGENT_SYNC_FOLDER', $SyncFolder, 'User')
    [Environment]::SetEnvironmentVariable('COMPANY_OPS_AGENT_START_DATE', $StartDate, 'User')
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

# docs/07 section 54: a short delay after logon so network and OneDrive are
# ready. The delay belongs on the TRIGGER, not on the settings set --
# New-ScheduledTaskSettingsSet exposes no RandomDelay parameter and its CIM
# instance has no such property, so the previous
# `$settings.CimInstanceProperties.Item('RandomDelay').Value = ...` resolved
# to $null and threw PropertyNotFound. That happened before
# Register-ScheduledTask was ever reached, which means the installer could
# never have registered anything on any machine.
# `-User` is not optional here, for two independent reasons.
#
# Correctness: without it, `-AtLogOn` means "when ANY user logs on". The
# Agent reads COMPANY_OPS_PROFILE, COMPANY_OPS_AGENT_SYNC_FOLDER and
# COMPANY_OPS_AGENT_START_DATE from the *user* environment (set above), and
# writes into that user's OneDrive folder. Firing it at a different
# account's logon would run this Desktop's Agent under an identity that has
# none of that configuration.
#
# Registrability: an any-user logon trigger is machine-wide, so Windows
# requires elevation to create it. Measured on a non-elevated session:
#
#     New-ScheduledTaskTrigger -AtLogOn                -> Access is denied
#     New-ScheduledTaskTrigger -AtLogOn -User <me>     -> registers
#     New-ScheduledTaskTrigger -Daily -At 09:00        -> registers
#     New-ScheduledTaskTrigger -AtStartup              -> Access is denied
#
# That single missing argument is why this installer had never registered a
# task on any non-administrator machine -- and why the failure was recorded
# as "this environment cannot register tasks at all", which is not true:
# every other trigger shape registers fine in the very same session.
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$logonTrigger.Delay = "PT$($DelayMinutes)M"

$triggers = @($logonTrigger)
if ($DailyAt) {
    # No delay on the daily trigger: it already fires at a chosen wall-clock
    # time, so delaying it would only make that time mean something else.
    $triggers += New-ScheduledTaskTrigger -Daily -At $DailyAt
}

# docs/07 section 55: Windows-level duplicate protection on top of the
# application's own lock. StartWhenAvailable is what makes a missed trigger
# (PC was off) fire on the next opportunity instead of being dropped.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# An Agent task already registered for a DIFFERENT Desktop.
#
# `-Force` below replaces a task of the SAME name. It cannot touch one with
# a different name, and this task's name carries the Desktop id -- so
# re-running this script with a different -DesktopId leaves the old task in
# place and adds a second one. Both then fire at logon.
#
# What that costs is not hypothetical, and `run_agent.py` spends twenty
# lines on it: only one of them matches this machine's agent_state.json, and
# the other is refused by ensure_desktop() every single run. The refusal is
# correct -- accepting it would let one Desktop inherit another's
# last_successful_collection_date and skip every date up to it with no error
# anywhere. So the safety holds and nobody learns that a scheduled job is
# failing daily.
#
# Warned about, not fixed. Removing a scheduled task is destructive and this
# script did not create that one; the operator may also be mid-migration and
# want both for an hour. The exact command to remove it is printed instead.
#
# `Get-ScheduledTask` is read-only, so this also runs under -WhatIf -- which
# is where an operator previewing an install should learn it.
# The same prefix, from the same place -- `$taskName` above is
# `agent_task_name(<id>)`, and this is every id.
$agentTaskWildcard = (& $python.Source -c @"
import sys
sys.path.insert(0, r'$srcDir')
import schedtask
print(schedtask.AGENT_TASK_PREFIX + '*')
"@).Trim()
if ([string]::IsNullOrWhiteSpace($agentTaskWildcard)) {
    throw 'Could not read AGENT_TASK_PREFIX from src\schedtask.py.'
}
$otherAgentTasks = @(
    Get-ScheduledTask -TaskPath '\' -TaskName $agentTaskWildcard `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -ne $taskName }
)
if ($otherAgentTasks.Count -gt 0) {
    $names = ($otherAgentTasks | ForEach-Object { $_.TaskName }) -join ', '
    Write-Warning @"
This machine already has an Agent task for a different Desktop: $names

Registering '$taskName' does NOT remove it -- -Force only replaces a task of
the same name. Both would fire at logon, and the one that does not match
this machine's agent_state.json is refused on every run (by design: taking
another Desktop's collection watermark would skip uncollected dates
silently). Nothing else reports that daily failure except
`python ops_status.py`.

If this machine is changing identity, remove the old one afterwards:

  Unregister-ScheduledTask -TaskName '$($otherAgentTasks[0].TaskName)' -Confirm:`$false

Not removed here: this script did not create it, and deleting a scheduled
task is not something an installer should decide.
"@
}

if ($PSCmdlet.ShouldProcess($taskName, 'Register scheduled task')) {
    try {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $triggers `
            -Settings $settings `
            -Description "DOJOONPASS Company Ops Multi-Desktop Agent ($DesktopId). Runs once at logon and catches up every uncollected date." `
            -Force -ErrorAction Stop | Out-Null
    }
    catch {
        # Register-ScheduledTask reports a bare, localised "Access is denied"
        # and nothing else, so this block has to supply the diagnosis.
        #
        # The previous version of this message was WRONG, and wrong in the
        # expensive direction: it asserted that even a bare
        # `Register-ScheduledTask -TaskName X -Action (cmd.exe /c exit)`
        # fails identically, concluded the problem was "where the task is
        # being written", and sent the operator to find an administrator.
        # Re-measured on a non-elevated session: that bare call *succeeds*,
        # and so does every trigger shape except a machine-wide one. The
        # real cause was one missing argument on this script's own trigger
        # (see the -User note above), and elevation was never required.
        #
        # An operator who followed the old advice would have gone looking
        # for admin rights they did not need, to fix a bug that was in this
        # file. So the ordering below now puts the checks that are actually
        # likely first, and no longer claims to know the answer.
        throw @"
Scheduled task registration was refused by Windows: $($_.Exception.Message)

The task's definition is valid; Windows refused to write it. Note that a
non-administrator CAN normally register a per-user task on Windows, so this
is usually a policy or scope problem rather than a missing privilege.

Things to check, in order:

  1. Task Scheduler service is running:
     Get-Service Schedule
  2. Your account may create tasks in the root folder -- some managed or
     hardened machines restrict it. Test with a throwaway task:
     Register-ScheduledTask -TaskName Probe1 ``
       -Action (New-ScheduledTaskAction -Execute cmd.exe -Argument '/c exit') ``
       -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(1))
     If that succeeds, the restriction is not a blanket one and this
     script's trigger scope is the thing to look at.
  3. Only if both of the above are fine: run from an elevated PowerShell.

Nothing was registered. The COMPANY_OPS_* environment variables were set
before this point and are harmless on their own; re-running the script
after fixing the above is safe and idempotent (-Force replaces any
half-registered task).
"@
    }

    $registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $registered) {
        # Register-ScheduledTask returned without throwing but the task is
        # not there. Reporting success on that would send the operator away
        # believing the Agent is scheduled when it is not -- the exact
        # silent failure this whole deployment step exists to avoid.
        throw "Register-ScheduledTask reported success but '$taskName' does not exist. Nothing is scheduled."
    }

    Write-Host "Registered: $taskName"
    Write-Host "  profile     : $DesktopId"
    Write-Host "  sync folder : $SyncFolder"
    Write-Host "  start date  : $StartDate"
    Write-Host "  triggers    : AtLogOn$(if ($DailyAt) { " + Daily $DailyAt" })"
    Write-Host ''
    Write-Host 'The PC does not need to stay on. Whenever it next starts, one run'
    Write-Host 'catches up every date since the last successful collection.'
}
