"""
Offline: Freitext-Vorleseort -> Stadt + Koordinaten. Keine API, kein Internet nötig.
Einzige Datenquelle: germany-cities.geojson (GeoDataKit-Export "Germany cities", GeoNames CC BY 4.0).

Trefferarten (resolve()["how"]):
  override        steht in overrides.csv                                   -> sicher
  ignore          steht in ignore.txt (Tests/Quatsch) -> wird nicht gezählt
  exact           ganzer Text ist ein Ortsname, klarer Favorit              -> sicher
  in_text         Stadt steckt im Text, klarer Favorit                      -> sicher
  ambiguous       Ortsname kommt mehrfach vor ("Neudorf", "Halle")          -> prüfen
  contained       mehrere Orte im Text, größter gewählt                     -> prüfen
  klein_im_text   nur ein bundesweit eindeutiges Dorf im Text               -> prüfen
  None            nichts gefunden                                           -> overrides.csv
Dateipfade (Reihenfolge: Argument > Umgebungsvariable > Datei neben diesem Modul).
Alle drei Dateien sind Pflicht - fehlt eine, bricht der Start mit einer Fehlermeldung ab:
  VORLESEORTE_GEOJSON, VORLESEORTE_OVERRIDES, VORLESEORTE_IGNORE
Diagnose der Datei:  python geo_lookup.py
"""
import csv
import difflib
import io
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

STATES = {"Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen",
          "Mecklenburg-Vorpommern", "Niedersachsen", "Nordrhein-Westfalen", "Rheinland-Pfalz",
          "Saarland", "Sachsen", "Sachsen-Anhalt", "Schleswig-Holstein", "Thüringen"}

# Der Export mischt englische und deutsche Regionsnamen -> vereinheitlichen (Schlüssel klein geschrieben)
REGION_MAP = {"land berlin": "Berlin", "free and hanseatic city of hamburg": "Hamburg",
              "free hanseatic city of bremen": "Bremen", "bavaria": "Bayern", "saxony": "Sachsen",
              "saxony-anhalt": "Sachsen-Anhalt", "thuringia": "Thüringen", "lower saxony": "Niedersachsen",
              "mecklenburg-western pomerania": "Mecklenburg-Vorpommern", "north rhine-westphalia": "Nordrhein-Westfalen",
              "rhineland-palatinate": "Rheinland-Pfalz", "hesse": "Hessen", "baden-wurttemberg": "Baden-Württemberg"}

# GeoNames führt einige Großstädte englisch -> deutsche Schreibweise anzeigen und auch so finden
DISPLAY = {"Munich": "München", "Nuremberg": "Nürnberg"}

CITY_STATES = ("Berlin", "Hamburg")     # Bezirke/Ortsteile zählen zur Stadt (Pankow -> Berlin)
BIG = 500                               # ab so vielen Einwohnern gilt ein Ort im Text als "echte" Stadt
CLEAR_POP, DOMINANCE = 5000, 20         # klarer Favorit: >= 5000 Einw. und >= 20x so groß wie der Zweitplatzierte
# Wörter, die im Fließtext nie als Ort gelten sollen (nur für die Suche *im* Text, nicht für ganze Eingaben)
STOPWORDS = {"schule", "grundschule", "kirche", "kita", "park", "haus", "markt", "platz", "ring", "hof",
             "stadt", "land", "berg", "grund", "bibliothek", "buecherei", "zentrum", "mitte", "nord",
             "sued", "ost", "west", "alt", "neu", "gross", "klein", "neustadt", "altstadt"}
_HEAD = re.compile(r"\s*\(|\s+(?:am|an der|an|im|in der|ob der|bei|auf der|vor der)\s+", re.I)


def key(text):
    """Vergleichsschlüssel: klein, ä->ae, ß->ss, Satzzeichen weg, 5-stellige PLZ weg."""
    s = (text or "").casefold()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\b\d{5}\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _configured_path(arg, env_name, default):
    """-> (Pfad, explizit_gesetzt). Absolute Pfade empfohlen; relative gelten ab dem Arbeitsverzeichnis."""
    value = arg or os.environ.get(env_name)
    return (Path(value).expanduser(), True) if value else (default, False)


def short_form(name):
    """'Halle (Saale)' -> 'Halle', 'Frankfurt am Main' -> 'Frankfurt'."""
    head = _HEAD.split(name, maxsplit=1)[0].strip()
    return head if head != name and len(head) >= 4 else None


class Orte:
    def __init__(self, geojson=None, overrides_csv=None, ignore_txt=None):
        base = Path(__file__).parent
        path, _ = _configured_path(geojson, "VORLESEORTE_GEOJSON", base / "germany-cities.geojson")
        self._ov_path, _ = _configured_path(overrides_csv, "VORLESEORTE_OVERRIDES", base / "overrides.csv")
        self._ig_path, _ = _configured_path(ignore_txt, "VORLESEORTE_IGNORE", base / "ignore.txt")
        missing = [f"  - {label} ({env}): {p}" for label, env, p in (
            ("GeoJSON", "VORLESEORTE_GEOJSON", path),
            ("Overrides", "VORLESEORTE_OVERRIDES", self._ov_path),
            ("Ignore-Liste", "VORLESEORTE_IGNORE", self._ig_path)) if not p.is_file()]
        if missing:       # alle drei Dateien sind Pflicht (overrides/ignore dürfen leer sein, aber nicht fehlen)
            raise FileNotFoundError("Benötigte Dateien fehlen:\n" + "\n".join(missing))
        self.places = []
        self.by_name = defaultdict(list)
        self.unknown_regions = defaultdict(int)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for ft in data["features"]:
            pr, geom = ft.get("properties") or {}, ft.get("geometry") or {}
            raw = (pr.get("name") or "").strip()
            if not raw or geom.get("type") != "Point":
                continue
            lng, lat = geom["coordinates"][:2]
            region = (pr.get("region") or "").strip()
            state = REGION_MAP.get(region.casefold(), region)
            if state not in STATES:
                self.unknown_regions[region] += 1
            name = DISPLAY.get(raw, raw)
            p = {"name": name, "state": state, "lat": lat, "lng": lng, "pop": int(pr.get("population") or 0)}
            self.places.append(p)
            keys = {key(raw), key(name)}
            sf = short_form(name)
            if sf:
                keys.add(key(sf))
            for k in keys:
                if k:
                    self.by_name[k].append(p)
        self.city_state_main = {}
        for s in CITY_STATES:
            cands = [p for p in self.by_name.get(key(s), []) if p["state"] == s]
            if cands:
                self.city_state_main[s] = max(cands, key=lambda p: p["pop"])
        self._big_keys = sorted(k for k, ps in self.by_name.items() if any(p["pop"] >= BIG for p in ps))
        self._sugg = {}
        self.reload_overrides()

    def reload_overrides(self):
        """overrides.csv und ignore.txt neu einlesen (im laufenden Betrieb möglich)."""
        for p in (self._ov_path, self._ig_path):
            if not p.is_file():
                raise FileNotFoundError(f"Datei fehlt: {p}")
        new = {}
        if self._ov_path.exists():
            with open(self._ov_path, encoding="utf-8", newline="") as f:
                for row in csv.reader(f):     # Spalten: text,ort,bundesland,lat,lng (Kopfzeile optional)
                    if not any(c.strip() for c in row) or row[0].lstrip().startswith("#"):
                        continue              # leere Zeile / Kommentar
                    if [c.strip().casefold() for c in row[:2]] == ["text", "ort"]:
                        continue              # Kopfzeile
                    try:
                        text, ort, state, lat, lng = (c.strip() for c in row[:5])
                        new[key(text)] = {"name": ort, "state": state, "lat": float(lat), "lng": float(lng)}
                    except ValueError:
                        logging.warning("overrides.csv: Zeile übersprungen (ungültig): %r", row)
        self.overrides = new
        ignored = set()
        if self._ig_path.exists():
            with open(self._ig_path, encoding="utf-8") as f:
                for line in f:                # eine Zeile = ein Freitext; Zeilen mit # davor sind Kommentare
                    line = line.strip()
                    if line and not line.startswith("#"):
                        ignored.add(key(line))
        self.ignored = ignored

    def _canon(self, p):
        return self.city_state_main.get(p["state"], p) if p["state"] in CITY_STATES else p

    @staticmethod
    def _pub(p):
        return {"name": p["name"], "state": p["state"], "lat": p["lat"], "lng": p["lng"]}

    def _pick(self, cands, clear_label, unclear_label):
        """Größten Kandidaten wählen; 'sicher' nur, wenn eindeutig oder klarer Favorit."""
        distinct = {}                      # Rang nach der *eigenen* Einwohnerzahl, Ergebnis ist die (Stadtstaaten-)Stadt
        for p in cands:
            c = self._canon(p)
            k = (c["name"], c["state"], round(c["lat"], 1), round(c["lng"], 1))
            if k not in distinct or p["pop"] > distinct[k][0]:
                distinct[k] = (p["pop"], c)
        ranked = sorted(distinct.values(), key=lambda t: -t[0])
        best_pop, best = ranked[0]
        if len(ranked) == 1 or (best_pop >= CLEAR_POP and best_pop >= DOMINANCE * ranked[1][0]):
            how = clear_label
        else:
            how = unclear_label
        return {**self._pub(best), "how": how}

    def resolve(self, text):
        k = key(text)
        if not k:
            return None
        if k in self.overrides:
            return {**self.overrides[k], "how": "override"}
        if k in self.ignored:
            return {"how": "ignore"}
        c = self.by_name.get(k)                                    # 1. ganzer Text = Ortsname
        if c:
            return self._pick(c, "exact", "ambiguous")
        tokens, hits = k.split(), []                               # 2. Ortsname steckt im Text
        for n in (4, 3, 2, 1):
            for i in range(len(tokens) - n + 1):
                g = " ".join(tokens[i:i + n])
                if n == 1 and (len(g) < 4 or g in STOPWORDS):
                    continue
                if g in self.by_name:
                    hits.append((g, self.by_name[g]))
        big = [p for _, ps in hits for p in ps if p["pop"] >= BIG]
        if big:
            return self._pick(big, "in_text", "contained")
        uniq = [ps[0] for g, ps in hits if len(ps) == 1 and len(g) >= 6]   # Dorf, bundesweit eindeutig
        if uniq:
            return self._pick(uniq, "klein_im_text", "klein_im_text")
        return None

    def suggest(self, text):
        """Ähnlichster Stadtname bei Tippfehlern ("Ostholz-Scharmbeck" -> "Osterholz-Scharmbeck")."""
        k = key(text)
        if k not in self._sugg:
            pool = [n for n in self._big_keys if abs(len(n) - len(k)) <= 3]
            m = difflib.get_close_matches(k, pool, n=1, cutoff=0.85)
            self._sugg[k] = max(self.by_name[m[0]], key=lambda p: p["pop"])["name"] if m else None
        return self._sugg[k]


def to_geojson(texts, orte):
    """texts: ein Freitext pro Vorlesung. -> (GeoJSON, Prüfbericht)"""
    groups, coords, flagged = defaultdict(int), {}, {}
    review = {"zu_pruefen": {}, "nicht_gefunden": defaultdict(int), "override_zeilen": [],
              "ignoriert": 0}
    for t in texts:
        r = orte.resolve(t)
        if r is None:
            review["nicht_gefunden"][t] += 1
            continue
        if r["how"] == "ignore":
            review["ignoriert"] += 1
            continue
        k = (r["name"], r["state"])
        groups[k] += 1
        coords[k] = (r["lng"], r["lat"])                           # GeoJSON: [lng, lat]
        if r["how"] not in ("override", "exact", "in_text") and t not in flagged:
            flagged[t] = r
            review["zu_pruefen"][t] = f'{r["name"]} ({r["how"]})'
    buf = io.StringIO()                                            # fertige Zeilen zum Bestätigen in overrides.csv
    w = csv.writer(buf, lineterminator="")
    for t, r in flagged.items():
        buf.seek(0); buf.truncate()
        w.writerow([t.strip(), r["name"], r["state"], r["lat"], r["lng"]])
        review["override_zeilen"].append(buf.getvalue())
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": list(coords[k])},
              "properties": {"ort": k[0], "anzahl": n}}
             for k, n in sorted(groups.items(), key=lambda kv: -kv[1])]
    review["nicht_gefunden"] = {t: {"anzahl": n, "vorschlag": orte.suggest(t)}
                                for t, n in review["nicht_gefunden"].items()}
    return {"type": "FeatureCollection", "features": feats}, review


if __name__ == "__main__":
    o = Orte()
    print(f"{len(o.places)} Orte geladen.")
    print("Größte:", ", ".join(f'{p["name"]} ({p["pop"]})' for p in sorted(o.places, key=lambda p: -p["pop"])[:8]))
    for name in ("München", "Munich", "Nürnberg", "Köln", "Hannover", "Halle", "Frankfurt", "Freiburg"):
        r = o.resolve(name)
        print(f"  {name:10} ->", f'{r["name"]} / {r["state"]} [{r["how"]}]' if r else "NICHT GEFUNDEN")
    if o.unknown_regions:
        print("Unbekannte Regionsnamen (bitte in REGION_MAP ergänzen):", dict(o.unknown_regions))
