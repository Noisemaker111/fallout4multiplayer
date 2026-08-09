# wait_for_fo4_and_probe.ps1 — run while FO4 is at main menu
$ErrorActionPreference = "Continue"
$fo4 = "C:\Games\Steam\steamapps\common\Fallout 4"
$py = "C:\Users\Jk101\Projects\fallout4multiplayer\FO4_Wrld\.venv\Scripts\python.exe"
Write-Host "Waiting for Fallout4.exe ..."
for ($i=0; $i -lt 120; $i++) {
  $p = Get-Process Fallout4 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($p -and $p.WorkingSet64 -gt 200MB) {
    Write-Host "Found pid=$($p.Id) ws=$([math]::Round($p.WS/1MB))MB"
    if (Test-Path "$fo4\fw_native.log") {
      $raw = Get-Content "$fo4\fw_native.log" -Raw
      if ($raw -match 'fallout4_base=(0x[0-9A-Fa-f]+)') {
        $base = $Matches[1]
        Write-Host "base=$base"
        & $py -c @"
import ctypes, struct, sys
k=ctypes.WinDLL('kernel32', use_last_error=True)
h=k.OpenProcess(0x0410, False, $($p.Id))
base=int('$base',16)
def read(a,n):
  b=(ctypes.c_char*n)(); g=ctypes.c_size_t(0)
  if not k.ReadProcessMemory(h, ctypes.c_void_p(a), b, n, ctypes.byref(g)): return None
  return bytes(b[:g.value])
def u64(b,o=0): return struct.unpack_from('<Q',b,o)[0]
vt=base+0x2D37D30
print('vt',hex(vt))
hits=[]
for rva in range(0x02C00000,0x03A00000,8):
  b=read(base+rva,8)
  if b and u64(b)==vt:
    hits.append(rva)
    if len(hits)>=10: break
print('objects', [hex(x) for x in hits])
for hr in hits[:6]:
  obj=base+hr
  pts=[]
  for rva in range(0x02C00000,0x03A00000,8):
    b=read(base+rva,8)
    if b and u64(b)==obj:
      pts.append(hex(rva))
      if len(pts)>=5: break
  print(f'obj 0x{hr:X} singleton_candidates {pts}')
for name,rva in [('SAVE_DEV',0x031E5A90),('SAVE_MGR',0x0329D508)]:
  b=read(base+rva,8)
  print(name, hex(u64(b)) if b else None)
"@
        break
      }
    }
  }
  Start-Sleep 2
}
if (Test-Path "$fo4\fw_native.log") { Get-Content "$fo4\fw_native.log" -Tail 50 }
