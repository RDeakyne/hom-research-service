"""Preprocess the IRS SOI ZIP-code file into a compact per-ZIP wealth/income lookup.
Input: /tmp/soi22.csv (22zpallagi.csv, tax year 2022). Output: research-service/soi_zip.json
Run once per IRS vintage. Amounts in the SOI file are in THOUSANDS of dollars.
"""
import csv, json

# 0-based column indices (from header inspection)
C_ZIP, C_STUB, C_N1 = 2, 3, 4
C_AGI, C_INT, C_DIV, C_CAPGAIN, C_IRA, C_PENS = 20, 26, 30, 38, 40, 42

agg = {}  # zip -> totals
with open("/tmp/soi22.csv", newline="") as f:
    r = csv.reader(f)
    next(r)  # header
    for row in r:
        try:
            z = row[C_ZIP].strip().zfill(5)
            stub = int(row[C_STUB])
        except (IndexError, ValueError):
            continue
        if z in ("00000", "99999") or stub < 1:  # state totals / non-zip aggregates
            continue
        def num(i):
            try:
                return float(row[i] or 0)
            except ValueError:
                return 0.0
        a = agg.setdefault(z, {"n": 0.0, "n200k": 0.0, "agi": 0.0, "inv": 0.0, "ret": 0.0})
        n1 = num(C_N1)
        a["n"] += n1
        if stub == 6:                      # agi_stub 6 = $200k+
            a["n200k"] += n1
        a["agi"] += num(C_AGI)
        a["inv"] += num(C_INT) + num(C_DIV) + num(C_CAPGAIN)   # interest + dividends + capital gains
        a["ret"] += num(C_IRA) + num(C_PENS)                   # IRA + pension distributions

out = {}
for z, a in agg.items():
    n = a["n"]
    if n < 1:
        continue
    out[z] = {
        "p200k": round(100 * a["n200k"] / n, 1),       # % of returns at $200k+ AGI
        "agi": int(a["agi"] * 1000 / n),               # mean AGI ($)
        "inv": int(a["inv"] * 1000 / n),               # investment income $ per return (net-worth signal)
        "ret": int(a["ret"] * 1000 / n),               # retirement distributions $ per return
    }

with open("/Users/rickydeakyne/Desktop/HOM Systems/research-service/soi_zip.json", "w") as f:
    json.dump(out, f, separators=(",", ":"))
print("zips:", len(out))
import os
print("file MB:", round(os.path.getsize("/Users/rickydeakyne/Desktop/HOM Systems/research-service/soi_zip.json") / 1e6, 2))
# sanity sample
for z in ("78633", "78739", "78758", "78746", "30068"):
    print(z, out.get(z))
