param(
    [Parameter(Mandatory = $true)]
    [string]$TargetIp,

    [ValidateSet("udp", "tcp", "mixed")]
    [string]$Mode = "mixed",

    [ValidateSet("low", "medium", "high")]
    [string]$Profile = "medium",

    [string]$Ports = "1883"
)

$profileMap = @{
    low = @{ Duration = 20; Workers = 4; Rate = 80; PayloadSize = 96 }
    medium = @{ Duration = 30; Workers = 8; Rate = 200; PayloadSize = 128 }
    high = @{ Duration = 45; Workers = 16; Rate = 350; PayloadSize = 256 }
}

$p = $profileMap[$Profile]
$scriptPath = Join-Path $PSScriptRoot "simulate_ddos.py"

Write-Host "[SIM] Running profile '$Profile' mode '$Mode' against $TargetIp on ports $Ports"
Write-Host "[SIM] duration=$($p.Duration)s workers=$($p.Workers) rate=$($p.Rate) payload=$($p.PayloadSize)"

python $scriptPath `
    --target-ip $TargetIp `
    --ports $Ports `
    --mode $Mode `
    --duration $($p.Duration) `
    --workers $($p.Workers) `
    --rate $($p.Rate) `
    --payload-size $($p.PayloadSize) `
    --i-understand-lab-only
