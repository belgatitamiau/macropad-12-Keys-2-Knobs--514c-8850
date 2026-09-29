"""Cambia en layer 2 el binding '3' -> '4' y verifica (514c:8850)."""

import led_gui as g
import dump as d

PAD = g.Pad()
err = PAD.connect()
if err:
    print("ERR:", err)
    raise SystemExit(1)

keys, knobs = d.identity(PAD.h)
print("identity:", keys, knobs)

recs = d.gather(PAD.h, keys, knobs, 2)
print(f"layer 2: {len(recs)} records")
for raw in recs:
    codes = [hex(b) for b in raw if b in (0x1E, 0x1F, 0x20, 0x21)]
    if codes:
        print(f"  slot {raw[2]:2d}: codes {codes}   raw {raw[:16].hex()}")
    else:
        print(f"  slot {raw[2]:2d}: (sin 1-4)     raw {raw[:16].hex()}")

# El '3' = 0x20 deberia estar en K3 = slot 12 (SLOT_BY_POS[2]); si no, buscar
prefer = [12, 4, 8]  # K3, K1, K2 por si la numeracion difiere
target = None
for slot in prefer:
    for raw in recs:
        if raw[2] == slot and 0x20 in raw:
            target = slot
            break
    if target:
        break
if target is None:
    for raw in recs:
        if 0x20 in raw:
            target = raw[2]
            break
if target is None:
    print("No hay ningun '3' (0x20) en layer 2")
    raise SystemExit(1)

print(f"\n-> slot con '3' (0x20) = {target} ; lo cambio a '4' (0x21) y guardo")
PAD.write_binding(target, 2, "4")

recs = d.gather(PAD.h, keys, knobs, 2)
verif = next((raw for raw in recs if raw[2] == target), None)
if verif is None:
    print("no volvio el slot en la relectura")
    raise SystemExit(1)
print("releido:", verif[:16].hex())
ok = 0x21 in verif and 0x20 not in verif
print("GUARDADO OK" if ok else "algo raro, reviso a mano")
PAD.close()
raise SystemExit(0 if ok else 2)