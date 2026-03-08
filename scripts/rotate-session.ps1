# Open new Windows Terminal tab with fresh Claude session
# Use after /session-save when context is high
param(
    [string]$ProjectPath = (Get-Location).Path,
    [switch]$Force
)

# Safety: check if session-notes.md exists (means session was saved)
$notesPath = Join-Path $ProjectPath "session-notes.md"
if ((-not $Force) -and (-not (Test-Path $notesPath))) {
    Write-Warning "No session-notes.md found. Run /session-save first, or use -Force to skip."
    exit 1
}

# Check freshness (< 5 min)
if (-not $Force) {
    $age = (Get-Date) - (Get-Item $notesPath).LastWriteTime
    if ($age.TotalMinutes -gt 5) {
        $ageMin = [int]$age.TotalMinutes
        Write-Warning "session-notes.md is $ageMin min old. Run /session-save again or use -Force."
        exit 1
    }
}

# Open new tab
$escapedPath = $ProjectPath -replace "'", "''"
wt new-tab --title "Claude (rotated)" -d "$escapedPath" -- claude
Write-Host "New session opened. Close this tab when ready."
