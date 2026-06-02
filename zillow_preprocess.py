import csv, json, os
out = {}
with open("/tmp/zhvi.csv", newline="") as f:
    r = csv.reader(f); next(r)  # header: 9 metadata cols then monthly
    for row in r:
        if len(row) < 10: continue
        z = row[2].strip().zfill(5)
        val = None
        for c in reversed(row[9:]):          # latest non-empty monthly value
            if c.strip():
                try: val = float(c); break
                except ValueError: pass
        if val and val > 0:
            out[z] = int(round(val))
p = "/Users/rickydeakyne/Desktop/HOM Systems/research-service/zillow_zhvi.json"
with open(p, "w") as f: json.dump(out, f, separators=(",", ":"))
print("zips:", len(out), "| MB:", round(os.path.getsize(p)/1e6, 2))
for z in ("78739","78746","70726","02492","30068"): print(z, out.get(z))
