#!/usr/bin/env python3
"""Election Night 2026 site: read and safely update the results JSON embedded in the page.

  python3 -I refresh_tool.py extract PAGE.html            -> prints the current data JSON
  python3 -I refresh_tool.py apply PAGE.html UPDATE.json BY -> validates UPDATE, merges it into PAGE.html in place,
                                                             prints a report; exit code 0 = page changed, 3 = nothing to publish

UPDATE.json shape (every key optional):
{
  "updated_label": "10:48 PM ET, Nov 3",          # time the source pages say they were updated, Eastern
  "updated_iso": "2026-11-04T03:48:00Z",
  "source": "ABC News live results (AP data)",
  "tally": {"Senate": {"dem": 46, "rep": 38}, "House": {"dem": 170, "rep": 171}},   # seats decided incl. holdovers
  "senate":   [{"abbr": "TX", "status": "D leads", "winner": null, "dem_pct": 58, "rep_pct": 40, "other_pct": 2, "pct_in": 61}],
  "governor": [{"abbr": "GA", "status": "Projected D", "winner": "Keisha Lance Bottoms", "dem_pct": 54, "rep_pct": 46, "other_pct": null, "pct_in": 85}],
  "house_flips": [{"district": "NJ-7", "winner": "Rebecca Bennett", "party": "D", "flipped_from": "R", "note": "Defeats Rep. Tom Kean Jr."}],
  "calls": [{"what": "Tom Cotton (R) wins Arkansas Senate", "p": "R"}],
  "headline": {"lead": "Democrats", "rest": "projected to win the House as the Senate stays in play", "dek": "..."}
}
status is one of: Projected D, Projected R, Projected I, D leads, R leads, I leads, Counting, Too early, Awaiting results, Not called.
For a race whose main challenger is an independent (challenger_party "I" in the page data), give the independent's share as other_pct.
"""
import json, re, sys, html as _html

START = '<script type="application/json" id="election-data">'
END = '</script>'
STATUSES = {"Projected D", "Projected R", "Projected I", "D leads", "R leads", "I leads",
            "Counting", "Too early", "Awaiting results", "Not called"}
PROJ = {"Projected D", "Projected R", "Projected I"}


def load(page_path):
    text = open(page_path, encoding="utf-8").read()
    i = text.find(START)
    if i < 0:
        sys.exit("data block not found in page")
    j = text.find(END, i + len(START))
    raw = text[i + len(START):j]
    return text, i, j, json.loads(raw)


def dump_block(data):
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def num(v, lo=0.0, hi=100.0):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("not a number")
    if not (lo <= v <= hi):
        raise ValueError("out of range")
    return round(float(v), 1) if not float(v).is_integer() else int(v)


def clean_text(v, limit):
    if v is None:
        return None
    v = str(v).strip()
    if not v or len(v) > limit or "<" in v or ">" in v:
        raise ValueError("bad text")
    return v


def opt_text(v, limit):
    return clean_text(v, limit) if v not in (None, "") else ""


def merge_races(cur_rows, upd_rows, kind, label, report):
    by = {r["abbr"]: r for r in cur_rows}
    changed = 0
    for u in upd_rows or []:
        a = str(u.get("abbr", "")).upper()
        r = by.get(a)
        if not r:
            report["rejected"].append(f"{kind} {a or '?'}: unknown state")
            continue
        try:
            st = u.get("status", r["status"])
            if st not in STATUSES:
                raise ValueError(f"unknown status {st!r}")
            d, rp, o, pin = (num(u.get("dem_pct", r.get("dem_pct"))), num(u.get("rep_pct", r.get("rep_pct"))),
                             num(u.get("other_pct", r.get("other_pct"))), num(u.get("pct_in", r.get("pct_in"))))
            if sum(x or 0 for x in (d, rp, o)) > 101.5:
                raise ValueError("vote shares add up to more than 100%")
            prev_in = r.get("pct_in") or 0
            if pin is not None and pin < prev_in - 3:
                raise ValueError(f"share counted fell from {prev_in}% to {pin}% (stale source?)")
            if r["status"] in PROJ and st != r["status"]:
                raise ValueError(f"would change a projected race ({r['status']} -> {st})")
            winner = u.get("winner", r.get("winner"))
            if st in PROJ:
                winner = clean_text(winner, 60)
                if not winner:
                    raise ValueError("projected without a winner name")
            else:
                winner = None
        except ValueError as e:
            report["rejected"].append(f"{kind} {a}: {e}")
            continue
        new = dict(r, status=st, winner=winner, dem_pct=d, rep_pct=rp, other_pct=o, pct_in=pin)
        party = st[-1] if st in PROJ else None
        new["flip"] = bool(party and party != r["held_by"])
        if any(new.get(k) != r.get(k) for k in ("status", "winner", "dem_pct", "rep_pct", "other_pct", "pct_in")):
            new["as_of"] = label.split(",")[0]
            if r["status"] not in PROJ and st in PROJ:
                report["new_projections"].append(f"{kind} {r['state']}: {winner} ({party})")
            by[a].clear(); by[a].update(new)
            changed += 1
    return changed


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, page = sys.argv[1], sys.argv[2]
    text, i, j, data = load(page)
    if cmd == "extract":
        print(json.dumps(data, indent=1, ensure_ascii=False))
        return
    if cmd != "apply" or len(sys.argv) < 5:
        sys.exit(__doc__)
    upd = json.load(open(sys.argv[3], encoding="utf-8"))
    by_who = clean_text(sys.argv[4], 60)
    report = {"changes": 0, "rejected": [], "new_projections": []}
    label = clean_text(upd.get("updated_label") or data["meta"]["updated_label"], 40)
    if not re.search(r"\d{1,2}:\d{2}\s?[AP]M ET", label):
        sys.exit("updated_label must look like '10:48 PM ET, Nov 3'")

    report["changes"] += merge_races(data["senate"], upd.get("senate"), "Senate", label, report)
    report["changes"] += merge_races(data["governor"], upd.get("governor"), "Governor", label, report)

    for ch, t in (upd.get("tally") or {}).items():
        row = next((r for r in data["tally"] if r["chamber"] == ch), None)
        try:
            if not row:
                raise ValueError("unknown chamber")
            dem, rep = int(t["dem"]), int(t["rep"])
            if dem + rep > row["total"] or dem < 0 or rep < 0:
                raise ValueError("more seats than exist")
            if dem < row["dem"] or rep < row["rep"]:
                raise ValueError(f"seat count went backwards ({row['dem']}-{row['rep']} -> {dem}-{rep})")
            if (dem, rep) != (row["dem"], row["rep"]):
                row.update(dem=dem, rep=rep, as_of=label, source=opt_text(upd.get("source") or row.get("source"), 80))
                report["changes"] += 1
        except (ValueError, KeyError, TypeError) as e:
            report["rejected"].append(f"tally {ch}: {e}")

    have = {f["district"] for f in data["house_flips"]}
    for f in upd.get("house_flips") or []:
        try:
            dist = clean_text(f.get("district"), 12).upper()
            if not re.fullmatch(r"[A-Z]{2}-(\d{1,2}|AL)", dist):
                raise ValueError("district must look like NJ-7")
            if dist in have:
                continue
            party, frm = f.get("party"), f.get("flipped_from")
            if party not in ("D", "R") or frm not in ("D", "R") or party == frm:
                raise ValueError("party / flipped_from invalid")
            data["house_flips"].append({"district": dist, "winner": clean_text(f.get("winner"), 60), "party": party,
                                        "flipped_from": frm, "note": clean_text(f.get("note") or "", 80) if f.get("note") else "",
                                        "called_at": label.split(",")[0]})
            have.add(dist); report["changes"] += 1
            report["new_projections"].append(f"House {dist}: {f.get('winner')} ({party}) flips seat")
        except (ValueError, AttributeError) as e:
            report["rejected"].append(f"house flip {f.get('district')}: {e}")

    seen = {c["what"] for c in data["calls"]}
    for c in upd.get("calls") or []:
        try:
            what = clean_text(c.get("what"), 120)
            if what in seen:
                continue
            p = c.get("p") if c.get("p") in ("D", "R", "I") else None
            if not p:
                raise ValueError("p must be D, R or I")
            data["calls"].append({"t": label.split(",")[0].replace(" ET", ""), "p": p, "what": what,
                                  "sub": clean_text(c.get("sub"), 120) if c.get("sub") else "", "flip": bool(c.get("flip")),
                                  "big": bool(c.get("big"))})
            seen.add(what); report["changes"] += 1
        except ValueError as e:
            report["rejected"].append(f"call {c.get('what')!r}: {e}")

    h = upd.get("headline")
    if h:
        try:
            lead, rest, dek = clean_text(h.get("lead"), 30), clean_text(h.get("rest"), 110), clean_text(h.get("dek"), 360)
            if (lead, rest, dek) != (data["meta"]["headline_lead"], data["meta"]["headline_rest"], data["meta"]["dek"]):
                data["meta"].update(headline_lead=lead, headline_rest=rest, dek=dek)
                report["changes"] += 1
        except ValueError as e:
            report["rejected"].append(f"headline: {e}")

    total_rows = len(upd.get("senate") or []) + len(upd.get("governor") or [])
    if total_rows >= 10 and len(report["rejected"]) > 0.4 * total_rows:
        print(json.dumps(report, indent=1))
        sys.exit("too many rows rejected; the source was probably misread, so nothing was written")

    if report["changes"] == 0:
        print(json.dumps(report, indent=1))
        sys.exit(3)

    data["meta"]["updated_label"] = label
    if upd.get("updated_iso"):
        data["meta"]["updated_iso"] = clean_text(upd["updated_iso"], 30)
    data["log"] = ([{"at": label, "by": by_who, "changes": report["changes"], "rejected": len(report["rejected"]),
                     "source": opt_text(upd.get("source"), 80)}] + data.get("log", []))[:15]
    out = text[:i + len(START)] + dump_block(data) + text[j:]
    open(page, "w", encoding="utf-8").write(out)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
