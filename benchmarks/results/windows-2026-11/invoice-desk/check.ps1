$want = @('Northwind Traders|NW-2041|1840.50|Hardware|no', 'Contoso Ltd|CT-7730|320.00|Software|yes', 'Fabrikam Inc|FB-1182|4275.25|Services|no')
function Seal($s) { $h = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes('desk-7Q|' + $s)); ($h[0..5] | ForEach-Object { $_.ToString('x2') }) -join '' }
$rows = @(Get-Content C:\InvoiceDesk\invoices.csv -ErrorAction SilentlyContinue | Where-Object { $_.Trim() })
$ok = ($rows.Count -eq 3)
foreach ($w in $want) { if ($rows -notcontains ($w + '|' + (Seal $w))) { $ok = $false } }
if ($ok) { 'PASS' } else { 'FAIL rows=' + $rows.Count }
