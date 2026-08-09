# Getting the right Fallout4.exe (1.11.191)

The native client pins **383 hardcoded addresses** to `Fallout4.exe` **1.11.191**
— 155 named constants in `fw_native/src/offsets.h` plus 228 more written inline
across the hook sources. Those numbers are raw RVAs. They are meaningful only
for the exact build they were reverse-engineered from.

Run the wrong build and MinHook writes a 5-byte jump into the *middle of an
instruction* in a function that isn't the one we meant to detour. That is not a
graceful failure; it is arbitrary code corruption in the game's combat and
inventory paths. The DLL's version gate (`fw_native/src/version.h`,
`EXPECTED = "1.11.191.0"`) exists to make this impossible by accident. **Do not
relax that gate to "make it run".**

## Check what you have

```
python tools/offset_audit.py --auto --include-inline
```

The tool resolves every pinned RVA against a real binary and asks whether code
addresses land on a **function start**. It doesn't guess: on x64 MSVC every
unwindable function has a `RUNTIME_FUNCTION` entry in the PE exception
directory, so this is an exact set-membership test.

Reference numbers from this machine (2026-07-28):

| Binary | Version | Code RVAs on a function start | Verdict |
|---|---|---|---|
| `Fallout4.exe` | 1.10.163 | 25/362 — 6.9% | wrong build |
| `Fallout4_downgradeBackup.exe` | 1.11.221 | 37/362 — 10.2% | wrong build |

Both fail. Note the second one is *next-gen but the wrong patch* — close enough
that misses are small (`KILL_ENGINE_RVA` is +0x11E into the enclosing function),
which is exactly why eyeballing "looks about right" is not a safe test and this
tool exists.

**A correct binary scores ≥95%.** Anything less, stop.

## Get 1.11.191

Steam only serves the newest build through the normal UI, so pull the older
depot manifest directly from the Steam console.

1. Find the manifest. Open SteamDB for app **377160**, go to **Depots**, open
   the depot that carries `Fallout4.exe`, and find the manifest whose build
   date matches the 1.11.191 release. Note the **depot ID** and **manifest ID**.

   (Manifest IDs are deliberately not written down here — they change as
   Bethesda re-publishes, and a stale ID copied from a doc is worse than no ID.
   The audit step below is what actually confirms you got the right thing, so
   you never have to trust the ID itself.)

2. In the Steam client, open `steam://open/console` and run:

   ```
   download_depot 377160 <depot_id> <manifest_id>
   ```

3. Steam drops the files under
   `Steam/steamapps/content/app_377160/depot_<depot_id>/`.

4. **Verify before installing it** — point the audit at the downloaded exe:

   ```
   python tools/offset_audit.py "<path>/Fallout4.exe"
   ```

   Expect ≥95%. If you get single digits you pulled the wrong manifest; go back
   to step 1. This is the whole point of the loop — a wrong guess costs you one
   command, not a corrupted install.

5. Once it verifies, back up your current `Fallout4.exe` and swap the verified
   one in.

## Keeping Steam from overwriting it

Steam re-patches on launch. The project already sidesteps this: the client is a
`dxgi.dll` proxy launched via `fw_launcher` / the Python launcher, not through
Steam's Play button. Keep launching that way and set the app to
"Only update this game when I launch it".

## Why not just target what's installed

Considered and rejected for now:

- **1.11.221** (your backup) — needs every address re-derived. Feasible *with*
  1.11.191 in hand, since you can extract byte signatures from the known-good
  build and scan for them in the new one. Without a reference build it is a
  manual re-RE of 383 addresses.
- **1.10.163** (currently active, matches your F4SE 0.6.23) — a total re-RE.
  The skin pipeline (`re/M8P3_skin_instance_dossier.txt`) and the combat hook
  set are the expensive parts and would both have to be redone from scratch.

## The durable fix

Hardcoded RVAs are why a routine Bethesda patch bricks the whole client. The
long-term answer is to resolve addresses at runtime instead of at compile time,
via byte-pattern signatures or F4SE **Address Library** IDs (which map a stable
ID to the right address per build, so a new game version needs a new database
rather than new code).

That work needs one known-good build to bootstrap from — which is another
reason to get 1.11.191 first. Once it's in place, `tools/offset_audit.py`
already provides the harness for auto-porting: extract a signature at each known
RVA in 1.11.191, scan for it in the target build, and emit the ported table.
