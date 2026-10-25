# Drives the desktop app through basic interactions so the GraalVM tracing agent records the
# code paths they exercise, then closes the window so the JVM shuts down cleanly -- the agent
# only writes its configuration on a clean shutdown.
#
# Metadata collected from a run that merely opens a window covers startup and rendering but not
# input, and the resulting native image renders correctly while silently ignoring every click.
param(
    [string]$WindowTitle = "Gemstone AI",
    [int]$TimeoutSec = 300
)

Add-Type @"
using System; using System.Runtime.InteropServices;
public class Drive {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern IntPtr PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004, WM_CLOSE = 0x0010;
}
"@
[Drive]::SetProcessDPIAware() | Out-Null

$deadline = (Get-Date).AddSeconds($TimeoutSec)
do {
    $proc = Get-Process | Where-Object { $_.MainWindowTitle -like "*$WindowTitle*" } | Select-Object -First 1
    if (-not $proc) { Start-Sleep -Milliseconds 1000 }
} while (-not $proc -and (Get-Date) -lt $deadline)
if (-not $proc) { Write-Output "no window matching '$WindowTitle' appeared"; exit 1 }

Start-Sleep -Seconds 3
[Drive]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 1000

$r = New-Object Drive+RECT
[Drive]::GetWindowRect($proc.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.R - $r.L; $h = $r.B - $r.T
Write-Output "driving '$($proc.MainWindowTitle)' (${w}x${h})"

function MoveTo($fx, $fy) {
    [Drive]::SetCursorPos($r.L + [int]($w * $fx), $r.T + [int]($h * $fy)) | Out-Null
    Start-Sleep -Milliseconds 250
}
function ClickAt($fx, $fy) {
    MoveTo $fx $fy
    [Drive]::mouse_event([Drive]::LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 90
    [Drive]::mouse_event([Drive]::LEFTUP, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 600
}

# Pointer movement is a separate code path from press/release, so sweep before clicking.
foreach ($p in @(0.2, 0.4, 0.6, 0.8)) { MoveTo $p 0.5 }
foreach ($p in @(0.3, 0.5, 0.7)) { MoveTo 0.5 $p }

# Neutral areas only -- enough to exercise hit testing and hover state without driving the app
# into anything destructive.
ClickAt 0.5 0.5
ClickAt 0.15 0.3
ClickAt 0.5 0.5

Start-Sleep -Seconds 2
Write-Output "closing window for a clean shutdown"
[Drive]::PostMessage($proc.MainWindowHandle, [Drive]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null

if ($proc.WaitForExit(60000)) { Write-Output "app exited cleanly" }
else { Write-Output "app did not exit; agent configuration may be incomplete" }
