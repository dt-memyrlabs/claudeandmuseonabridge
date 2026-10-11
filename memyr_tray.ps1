# memyr_tray.ps1 — a tray icon for the bridge processes on this PC.
# Shows what is running and lets the user start or kill each one.
# Pure PowerShell + WinForms: no installs, no admin.
# Run: powershell -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File memyr_tray.ps1

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class IcoLib {
    [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr h);
}
"@

$PyW  = "C:\Python314\pythonw.exe"
$Py   = "C:\Python314\python.exe"
$UsrHome = $env:USERPROFILE

$script:Services = @(
    @{ Name = "Memyr PC Agent"; Kind = "Port"; Port = 8766
       Exe = $PyW; Args = "`"$UsrHome\.memyr-agent\server.py`""
       WorkDir = "$UsrHome\.memyr-agent" },
    @{ Name = "Claude MCP server"; Kind = "Port"; Port = 8765
       Exe = $Py; Args = "`"$UsrHome\.muse-bridge\claude_mcp_server.py`""
       WorkDir = "$UsrHome\.muse-bridge" },
    @{ Name = "windows-mcp server"; Kind = "Task"; Task = "windows-mcp-server" },
    @{ Name = "Foculet"; Kind = "Match"; Match = "foculet.py"
       Exe = $PyW; Args = "`"$UsrHome\Documents\foculet\foculet.py`""
       WorkDir = "$UsrHome\Documents\foculet" },
    @{ Name = "Chrome bridge"; Kind = "Match"; Match = "bridge-server.py" },
    @{ Name = "Claude browser relay"; Kind = "Match"; Match = "muse-bridge-v2" }
)

function Get-PortPid($port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

function Get-MatchPids($match) {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and
                       $_.CommandLine -like "*$match*" -and
                       $_.ProcessId -ne $PID } |
        ForEach-Object { $_.ProcessId })
}

function Test-Service($s) {
    switch ($s.Kind) {
        "Port"  { return [bool](Get-PortPid $s.Port) }
        "Match" { return (Get-MatchPids $s.Match).Count -gt 0 }
        "Task"  { $t = Get-ScheduledTask -TaskName $s.Task `
                      -ErrorAction SilentlyContinue
                  return ($t -and $t.State -eq "Running") }
    }
    return $false
}

function Stop-Service2($s) {
    switch ($s.Kind) {
        "Port"  { $p = Get-PortPid $s.Port
                  if ($p) { Stop-Process -Id $p -Force `
                      -ErrorAction SilentlyContinue } }
        "Match" { foreach ($p in Get-MatchPids $s.Match) {
                      Stop-Process -Id $p -Force `
                      -ErrorAction SilentlyContinue } }
        "Task"  { Stop-ScheduledTask -TaskName $s.Task `
                      -ErrorAction SilentlyContinue }
    }
}

function Start-Service2($s) {
    if ($s.Kind -eq "Task") {
        Start-ScheduledTask -TaskName $s.Task `
            -ErrorAction SilentlyContinue
        return
    }
    if (-not $s.Exe) { return }
    Start-Process -FilePath $s.Exe -ArgumentList $s.Args `
        -WorkingDirectory $s.WorkDir -WindowStyle Hidden `
        -ErrorAction SilentlyContinue
}

function Make-IconHandle($color) {
    $bmp = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.SmoothingMode = "AntiAlias"
    $brush = New-Object System.Drawing.SolidBrush $color
    $g.FillEllipse($brush, 1, 1, 14, 14)
    $brush.Dispose(); $g.Dispose()
    $h = $bmp.GetHicon()
    $bmp.Dispose()
    return $h
}

$script:LastIconHandle = [IntPtr]::Zero

function Refresh-Icon {
    $agentUp = Test-Service $script:Services[0]
    $mcpUp   = Test-Service $script:Services[1]
    if ($agentUp -and $mcpUp) {
        $color = [System.Drawing.Color]::LimeGreen; $word = "all up"
    } elseif ($agentUp -or $mcpUp) {
        $color = [System.Drawing.Color]::Gold; $word = "partly up"
    } else {
        $color = [System.Drawing.Color]::Tomato; $word = "bridge down"
    }
    $h = Make-IconHandle $color
    $script:Notify.Icon = [System.Drawing.Icon]::FromHandle($h)
    if ($script:LastIconHandle -ne [IntPtr]::Zero) {
        [IcoLib]::DestroyIcon($script:LastIconHandle) | Out-Null
    }
    $script:LastIconHandle = $h
    $script:Notify.Text = "Memyr bridge: $word"
}

function Rebuild-Menu {
    $script:Menu.Items.Clear()
    foreach ($svc in $script:Services) {
        $s = $svc
        $up = Test-Service $s
        $dot = if ($up) { [char]0x25CF } else { [char]0x25CB }
        $verb = if ($up) { "click to stop" } else { "click to start" }
        if (-not $up -and -not $s.Exe -and $s.Kind -ne "Task") {
            $verb = "not running"
        }
        $item = New-Object System.Windows.Forms.ToolStripMenuItem
        $item.Text = "$dot $($s.Name)  ($verb)"
        if ($up -or $s.Exe -or $s.Kind -eq "Task") {
            $handler = {
                if (Test-Service $s) { Stop-Service2 $s }
                else { Start-Service2 $s }
                Start-Sleep -Milliseconds 700
                Refresh-Icon
                Rebuild-Menu
            }.GetNewClosure()
            $item.add_Click($handler)
        }
        $script:Menu.Items.Add($item) | Out-Null
    }
    $script:Menu.Items.Add(
        (New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
    $folder = New-Object System.Windows.Forms.ToolStripMenuItem
    $folder.Text = "Open agent folder"
    $folder.add_Click({ Start-Process explorer.exe `
        "$env:USERPROFILE\.memyr-agent" })
    $script:Menu.Items.Add($folder) | Out-Null
    $exit = New-Object System.Windows.Forms.ToolStripMenuItem
    $exit.Text = "Exit tray icon"
    $exit.add_Click({
        $script:Notify.Visible = $false
        $script:Notify.Dispose()
        [System.Windows.Forms.Application]::Exit()
    })
    $script:Menu.Items.Add($exit) | Out-Null
}

$script:Notify = New-Object System.Windows.Forms.NotifyIcon
$script:Menu = New-Object System.Windows.Forms.ContextMenuStrip
$script:Menu.add_Opening({ Rebuild-Menu })
$script:Notify.ContextMenuStrip = $script:Menu
$script:Notify.Visible = $true
Refresh-Icon
Rebuild-Menu

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({ Refresh-Icon })
$timer.Start()

$ctx = New-Object System.Windows.Forms.ApplicationContext
[System.Windows.Forms.Application]::Run($ctx)
