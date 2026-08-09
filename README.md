# FalloutWorld

Unofficial **Fallout 4 multiplayer** — walk around together, see each other move and animate, share the world.

> Early / buggy. Back up your saves.  
> Needs Fallout 4 **classic 1.10.163** (not next-gen).  
> **Demo:** [YouTube](https://www.youtube.com/watch?v=Qs3dNzXnnko)

---

## Play with a friend (this is the whole guide)

### What you need

1. Fallout 4 on Steam (**version 1.10.163** — classic).
2. The player pack: **[download FoM_PlayerPack.zip](https://github.com/Noisemaker111/fallout4multiplayer/releases/latest)**  
   (if there is no release yet, the host sends you that zip — one file, nothing else.)

That’s it. **No building. No editing configs. No ports to open for Steam play.**

---

### First time (each PC, once)

1. Unzip `FoM_PlayerPack.zip` anywhere.
2. Double-click **`FoM.exe`**.
3. Let it find Fallout 4 and finish setup.

---

### Same house / same Wi‑Fi (LAN)

| You (host) | Friend |
|------------|--------|
| Double-click **`FoM.exe`** | Double-click **`FoM.exe`** |
| Press **`3`** (LAN) → **Host** | Press **`3`** (LAN) → **Join** |
| Read the IP on screen, tell them | Type that IP |
| Keep the FoM window open | Keep the FoM window open |

Load into the game on both PCs. Done.

If Windows Firewall pops up on the host, click **Allow** (private network).

---

### Different houses / internet (easiest)

| You (host) | Friend |
|------------|--------|
| Double-click **`FoM.exe`**, press **`1`** | Click **Accept** on the Steam invite |
| Pick them in Steam’s friend list | Game opens by itself |

Friend can also right‑click you in Steam → **Join Game**.  
They don’t need to open FoM first.

**Keep FoM open while you play** — close the game when you’re done; FoM exits shortly after.

Steam may say you’re playing **Spacewar**. That’s normal (how invites work). Fallout 4 is still Fallout 4.

---

### Host only — one install if FoM asks for Python

If hosting says it needs Python: install [Python 3](https://www.python.org/downloads/) and check **“Add python.exe to PATH”**, then run FoM again.  
**Joiners never need this.**

---

### Troubleshooting (short)

| Problem | Fix |
|---------|-----|
| Game is next-gen / won’t load multiplayer | Use classic **1.10.163** |
| LAN join fails | Same Wi‑Fi; host allows Firewall; IP is the host’s LAN IP (e.g. `192.168.x.x`) |
| Steam invite never arrives | Friend runs FoM once; in FoM press **`5`** so background invites are on. Or run `FoM.exe --steam-check` |
| Updating the pack fails | Run `FoM.exe --quit-agent`, then replace files |

---

## For developers only

Source lives in **`FO4_Wrld/`**. Players should never need the steps below.

Build a new player pack:

```text
FO4_Wrld\fw_native\build.bat --minimal
FO4_Wrld\fw_launcher\build.bat
powershell -File FO4_Wrld\tools\player_setup\Build-PlayerPack.ps1
```

Zip lands at `FO4_Wrld\tools\player_setup\dist\FoM_PlayerPack.zip`.

Dev docs: [FO4_Wrld/README.md](FO4_Wrld/README.md) · [MP runbook](FO4_Wrld/docs/MP_TEST_RUNBOOK.md) · [player pack notes](FO4_Wrld/tools/player_setup/README.md)

---

**License:** [PolyForm Noncommercial](FO4_Wrld/LICENSE) · Not affiliated with Bethesda / Microsoft.
