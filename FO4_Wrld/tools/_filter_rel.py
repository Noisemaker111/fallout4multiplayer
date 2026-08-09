import csv
from pathlib import Path

rows = list(csv.DictReader(Path("tools/addresslib/commonlib_rel_ids.csv").open(encoding="utf-8")))
print(len(rows), "ids")
keys = (
    "placeatme", "lock", "graph", "fixedstring", "handle", "loadgame", "save",
    "additem", "removeitem", "getcomponent", "motion", "resolve", "lookup",
    "setvalue", "allocate", "singleton", "playercharacter", "playercamera",
    "getform", "allforms", "memorymanager", "bsfixedstring", "refhandle",
    "objectreference", "papyrus",
)
for r in rows:
    blob = (r["class"] + " " + r["func"] + " " + r["file"] + " " + r["text"]).lower()
    if any(k in blob for k in keys):
        print(
            f"{r['id']:8} {r['class'][:22]:22} {r['func'][:30]:30} {r['file']}"
        )
