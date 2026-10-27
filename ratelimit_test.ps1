$base = "http://127.0.0.1:8000/login/"
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$r = Invoke-WebRequest $base -WebSession $s -UseBasicParsing
$m = [regex]::Match($r.Content, 'name="csrfmiddlewaretoken" value="([^"]+)"')
if (-not $m.Success) { Write-Host "CSRF token nahi mila, login page ka HTML check karein"; exit 1 }
$token = $m.Groups[1].Value
Write-Host "CSRF token mil gaya"

1..13 | ForEach-Object {
  $code = 0
  $txt = ""
  $body = @{ username = "testuser123"; password = "wrong$_"; csrfmiddlewaretoken = $token }
  try {
    $resp = Invoke-WebRequest $base -Method Post -Body $body -WebSession $s -UseBasicParsing -Headers @{ Referer = $base }
    $code = [int]$resp.StatusCode
    $txt = $resp.Content
  } catch {
    $code = [int]$_.Exception.Response.StatusCode
    $txt = "$($_.ErrorDetails.Message)"
  }
  if ($txt -match "Too many login attempts") { $label = "BLOCKED (rate limit laga)" }
  elseif ($txt -match "correct username and password") { $label = "invalid (normal galat-login error)" }
  else { $label = "UNKNOWN (kuch aur hua)" }
  Write-Host "$_ : HTTP $code : $label"
}