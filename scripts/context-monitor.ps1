# PostToolUse hook -- monitors context window usage
# Reads stdin (tool result), counts chars/4 as token estimate
# Warns on stderr at 70% and 85% thresholds
param(
    [int]$MaxTokens = 200000,
    [int]$WarnPercent = 70,
    [int]$CriticalPercent = 85
)
$input_text = $input | Out-String
$tokens = [math]::Floor($input_text.Length / 4)
# Accumulate in env var (persists within session)
$current = [int]$env:CLAUDE_TOKEN_COUNT + $tokens
$env:CLAUDE_TOKEN_COUNT = $current
$pct = [math]::Floor($current / $MaxTokens * 100)
if ($pct -ge $CriticalPercent) {
    [Console]::Error.WriteLine("!! Context $pct% ($current tokens). Save session NOW: /session-save then /clear")
} elseif ($pct -ge $WarnPercent) {
    [Console]::Error.WriteLine("! Context $pct% ($current tokens). Consider saving soon.")
}
# Pass through stdin unchanged
$input_text
