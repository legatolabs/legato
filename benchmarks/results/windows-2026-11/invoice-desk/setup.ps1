# Installs "Invoice Desk": a small Windows Forms program of the kind a company's back office runs, with no API and no
# command line. It only saves what is entered through its window. Also puts three invoices to enter on the Desktop.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force C:\InvoiceDesk | Out-Null
Remove-Item C:\InvoiceDesk\invoices.csv -ErrorAction SilentlyContinue
@'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$store = 'C:\InvoiceDesk\invoices.csv'
function Seal($s) { $h = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes('desk-7Q|' + $s)); ($h[0..5] | ForEach-Object { $_.ToString('x2') }) -join '' }
$f = New-Object System.Windows.Forms.Form
$f.Text = 'Invoice Desk 2.4'; $f.Size = New-Object System.Drawing.Size(560, 520); $f.StartPosition = 'CenterScreen'; $f.Font = New-Object System.Drawing.Font('Segoe UI', 10)
function Label($t, $y) { $l = New-Object System.Windows.Forms.Label; $l.Text = $t; $l.Location = New-Object System.Drawing.Point(20, $y); $l.Size = New-Object System.Drawing.Size(130, 24); $f.Controls.Add($l) }
function Box($y) { $b = New-Object System.Windows.Forms.TextBox; $b.Location = New-Object System.Drawing.Point(160, $y); $b.Size = New-Object System.Drawing.Size(360, 26); $f.Controls.Add($b); $b }
Label 'Customer' 22; $cust = Box 20
Label 'Invoice number' 62; $num = Box 60
Label 'Amount (USD)' 102; $amt = Box 100
Label 'Category' 142
$cat = New-Object System.Windows.Forms.ComboBox; $cat.DropDownStyle = 'DropDownList'; $cat.Location = New-Object System.Drawing.Point(160, 140); $cat.Size = New-Object System.Drawing.Size(200, 26)
[void]$cat.Items.AddRange(@('Hardware', 'Software', 'Services', 'Travel')); $f.Controls.Add($cat)
$paid = New-Object System.Windows.Forms.CheckBox; $paid.Text = 'Paid'; $paid.Location = New-Object System.Drawing.Point(160, 180); $paid.Size = New-Object System.Drawing.Size(100, 26); $f.Controls.Add($paid)
$save = New-Object System.Windows.Forms.Button; $save.Text = 'Save invoice'; $save.Location = New-Object System.Drawing.Point(160, 220); $save.Size = New-Object System.Drawing.Size(140, 34); $f.Controls.Add($save)
$clear = New-Object System.Windows.Forms.Button; $clear.Text = 'Clear'; $clear.Location = New-Object System.Drawing.Point(310, 220); $clear.Size = New-Object System.Drawing.Size(90, 34); $f.Controls.Add($clear)
$status = New-Object System.Windows.Forms.Label; $status.Location = New-Object System.Drawing.Point(20, 266); $status.Size = New-Object System.Drawing.Size(500, 24); $f.Controls.Add($status)
$list = New-Object System.Windows.Forms.ListView; $list.View = 'Details'; $list.FullRowSelect = $true; $list.Location = New-Object System.Drawing.Point(20, 296); $list.Size = New-Object System.Drawing.Size(500, 160)
foreach ($c in @(@('Customer', 170), @('Invoice', 90), @('Amount', 80), @('Category', 90), @('Paid', 50))) { [void]$list.Columns.Add($c[0], $c[1]) }
$f.Controls.Add($list)
function Reset { $cust.Text = ''; $num.Text = ''; $amt.Text = ''; $cat.SelectedIndex = -1; $paid.Checked = $false; $cust.Focus() | Out-Null }
$clear.Add_Click({ Reset; $status.Text = '' })
$save.Add_Click({
  $a = 0.0
  if (-not $cust.Text.Trim() -or -not $num.Text.Trim() -or $cat.SelectedIndex -lt 0 -or -not [double]::TryParse($amt.Text.Trim(), [ref]$a)) {
    $status.ForeColor = 'Firebrick'; $status.Text = 'Fill in customer, invoice number, a numeric amount and a category.'; return }
  $row = @($cust.Text.Trim(), $num.Text.Trim(), ('{0:F2}' -f $a), [string]$cat.SelectedItem, $(if ($paid.Checked) { 'yes' } else { 'no' })) -join '|'
  Add-Content -Path $store -Value ($row + '|' + (Seal $row)) -Encoding UTF8
  $it = New-Object System.Windows.Forms.ListViewItem($cust.Text.Trim()); foreach ($v in @($num.Text.Trim(), ('{0:F2}' -f $a), [string]$cat.SelectedItem, $(if ($paid.Checked) { 'yes' } else { 'no' }))) { [void]$it.SubItems.Add($v) }
  [void]$list.Items.Add($it); $status.ForeColor = 'DarkGreen'; $status.Text = "Saved invoice $($num.Text.Trim())."; Reset })
[void]$f.ShowDialog()
'@ | Set-Content -Path C:\InvoiceDesk\InvoiceDesk.ps1 -Encoding UTF8
$d = [Environment]::GetFolderPath('Desktop')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$d\Invoice Desk.lnk")
$s.TargetPath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$s.Arguments = '-WindowStyle Hidden -ExecutionPolicy Bypass -File C:\InvoiceDesk\InvoiceDesk.ps1'
$s.IconLocation = 'C:\Windows\System32\shell32.dll,1'; $s.Save()
@'
Invoices received today - enter each one into Invoice Desk.

1. Customer: Northwind Traders    Invoice number: NW-2041   Amount: 1840.50   Category: Hardware   Paid: no
2. Customer: Contoso Ltd          Invoice number: CT-7730   Amount: 320.00    Category: Software   Paid: yes
3. Customer: Fabrikam Inc         Invoice number: FB-1182   Amount: 4275.25   Category: Services   Paid: no
'@ | Set-Content -Path "$d\invoices.txt" -Encoding ASCII
'READY'
