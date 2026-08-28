<#
.SYNOPSIS
    Registers the Multi-Desktop Agent to run when this user logs on.

.DESCRIPTION
    Creates one Windows Task Scheduler task per Desktop:

        DOJOONPASS_COMPANY_OPS_AGENT_<DESKTOP_ID>

    Trigger:  At log on (docs/07_SCHEDULER_CATCHUP_SPEC.md section 53's
              STARTUP task shape, applied to the Agent).
    Action:   python run_agent.py, once, then the process exits.

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
$taskName = "DOJOONPASS_COMPANY_OPS_AGENT_$DesktopId"

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "run_agent.py not found at $entrypoint - run this script from the repository's scripts\ directory."
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $python) {
    throw 'python was not found on PATH. Install Python or add it to PATH before registering the task.'
}

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

$action = New-ScheduledTaskAction `
    -Execute $python.Source `
    -Argument "`"$entrypoint`"" `
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
