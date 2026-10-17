"""Blind side-by-side visual comparison harness.

Pairs each Nagare Kanban screenshot against a same-category reference screenshot from a
best-in-class product, strips the branding that would give the game away, randomises
which panel is A and which is B, and writes one composite PNG per pair.

A critic looks at the composite and answers "A or B -- which is the better designed
interface, and why". Only ``reveal`` knows the mapping, so the critic cannot favour us.

Commands
--------
  build   pair shots with references and emit composites + a hidden key file
  reveal  score a verdicts file against the key and print the tally
  status  show the current pair inventory

Example
-------
  python tools/blind_compare.py build --round 1
  # ... critic writes compare/round1/verdicts.json ...
  python tools/blind_compare.py reveal --round 1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOTS = ROOT / "shots"
REFS = ROOT / "refs"
COMPARE = ROOT / "compare"

PANEL_W = 1180  # each panel is normalised to this width
GUTTER = 44
MARGIN = 40
HEADER = 78
BG = (24, 24, 27)
FG = (245, 245, 247)
SUBTLE = (150, 150, 158)

# Which Nagare route is judged against which reference category.
ROUTE_CATEGORY = {
    "board-unified": "board",
    "board-upstream": "board",
    "board-downstream": "board",
    "card-drawer": "board",
    "projects": "board",
    "project-detail": "board",
    "metrics": "metrics",
    "metrics-kmm": "metrics",
    "simulator": "metrics",
    "simulator-results": "metrics",
    "admin-users": "board",
    "admin-flags": "board",
    "admin-plans": "board",
}

# Regions (as fractions of panel size) wiped on BOTH panels so a wordmark can't leak
# the identity of either side. Symmetric by construction -- the mask itself is no tell.
BRAND_MASKS = [
    (0.000, 0.000, 0.190, 0.062),  # top-left wordmark / logo
    (0.780, 0.000, 1.000, 0.052),  # top-right account chrome
]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def normalise(path: pathlib.Path, width: int = PANEL_W, max_h: int = 1500) -> Image.Image:
    """Scale a screenshot to a common width and crop tall pages to a comparable height."""
    img = Image.open(path).convert("RGB")
    scale = width / img.width
    img = img.resize((width, max(1, round(img.height * scale))), Image.LANCZOS)
    if img.height > max_h:
        img = img.crop((0, 0, width, max_h))
    return img


def mask_branding(img: Image.Image) -> Image.Image:
    """Blur out the corners where product wordmarks live, on every panel equally."""
    out = img.copy()
    for fx0, fy0, fx1, fy1 in BRAND_MASKS:
        box = (
            round(fx0 * img.width),
            round(fy0 * img.height),
            round(fx1 * img.width),
            round(fy1 * img.height),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = out.crop(box).filter(ImageFilter.GaussianBlur(18))
        out.paste(region, box)
    return out


def compose(left: Image.Image, right: Image.Image, pair_id: str, prompt_note: str) -> Image.Image:
    h = max(left.height, right.height)
    w = MARGIN * 2 + PANEL_W * 2 + GUTTER
    canvas = Image.new("RGB", (w, HEADER + h + MARGIN), BG)
    draw = ImageDraw.Draw(canvas)

    f_label = load_font(30, bold=True)
    f_note = load_font(17)

    draw.text((MARGIN, 20), "A", font=f_label, fill=FG)
    draw.text((MARGIN + PANEL_W + GUTTER, 20), "B", font=f_label, fill=FG)
    draw.text((MARGIN + 34, 26), prompt_note, font=f_note, fill=SUBTLE)
    draw.text((w - MARGIN - 260, 26), pair_id, font=f_note, fill=(90, 90, 98))

    for x, panel in ((MARGIN, left), (MARGIN + PANEL_W + GUTTER, right)):
        canvas.paste(panel, (x, HEADER))
        draw.rectangle(
            [x - 1, HEADER - 1, x + PANEL_W, HEADER + panel.height], outline=(60, 60, 66), width=1
        )
    return canvas


def discover_shots() -> list[pathlib.Path]:
    if not SHOTS.exists():
        return []
    return sorted(p for p in SHOTS.rglob("*.png") if p.is_file())


def discover_refs() -> dict[str, list[pathlib.Path]]:
    out: dict[str, list[pathlib.Path]] = {}
    if not REFS.exists():
        return out
    for cat_dir in REFS.iterdir():
        if cat_dir.is_dir():
            imgs = sorted(p for p in cat_dir.glob("*.png"))
            if imgs:
                out[cat_dir.name] = imgs
    return out


def route_of(shot: pathlib.Path) -> str:
    return shot.stem


def cmd_build(args: argparse.Namespace) -> int:
    rnd = random.Random(args.seed + args.round * 7919)
    shots = discover_shots()
    refs = discover_refs()
    if not shots:
        print(f"no screenshots under {SHOTS} -- run tools/screenshot.py first", file=sys.stderr)
        return 1
    if not refs:
        print(f"no references under {REFS} -- run tools/fetch_references.py first", file=sys.stderr)
        return 1

    outdir = COMPARE / f"round{args.round}"
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.glob("pair-*.png"):
        stale.unlink()

    keys, pairs = [], []
    for shot_index, shot in enumerate(shots):
        route = route_of(shot)
        cat = ROUTE_CATEGORY.get(route, "board")
        pool = refs.get(cat) or next(iter(refs.values()))
        theme = shot.parent.name
        # Rotate the starting opponent per shot: taking pool[:n] every time would pit
        # every board route against the same product and tell us nothing about how we
        # fare against the rest of the field.
        offset = shot_index % len(pool)
        chosen = [pool[(offset + i) % len(pool)] for i in range(min(args.per_route, len(pool)))]
        for ref in chosen:
            pair_id = f"pair-{len(pairs):03d}"
            ours = mask_branding(normalise(shot))
            theirs = mask_branding(normalise(ref))
            ours_is_a = rnd.random() < 0.5
            left, right = (ours, theirs) if ours_is_a else (theirs, ours)
            note = f"{cat} interface  |  which is better designed?"
            img = compose(left, right, pair_id, note)
            dest = outdir / f"{pair_id}.png"
            img.save(dest, optimize=True)
            keys.append(
                {
                    "pair_id": pair_id,
                    "ours_panel": "A" if ours_is_a else "B",
                    "ours": str(shot.relative_to(ROOT)).replace("\\", "/"),
                    "theirs": str(ref.relative_to(ROOT)).replace("\\", "/"),
                    "route": route,
                    "theme": theme,
                    "category": cat,
                }
            )
            pairs.append(dest)

    (outdir / "_key.json").write_text(json.dumps(keys, indent=2), encoding="utf-8")
    manifest = [
        {
            "pair_id": k["pair_id"],
            "image": f"compare/round{args.round}/{k['pair_id']}.png",
            "category": k["category"],
        }
        for k in keys
    ]
    (outdir / "pairs.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"built {len(pairs)} blind pairs in {outdir}")
    print(f"  critic reads : {outdir / 'pairs.json'}")
    print(
        f"  critic writes: {outdir / 'verdicts.json'}  "
        '[{"pair_id":"pair-000","winner":"A","margin":"clear|slight|tie",'
        '"reasons":["..."],"loser_fixes":["..."]}]'
    )
    print(f"  key (do NOT show the critic): {outdir / '_key.json'}")
    return 0


def cmd_reveal(args: argparse.Namespace) -> int:
    outdir = COMPARE / f"round{args.round}"
    key_path, verdict_path = outdir / "_key.json", outdir / "verdicts.json"
    if not key_path.exists() or not verdict_path.exists():
        print(
            f"missing {key_path.name if not key_path.exists() else verdict_path.name}",
            file=sys.stderr,
        )
        return 1

    keys = {k["pair_id"]: k for k in json.loads(key_path.read_text(encoding="utf-8"))}
    verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("verdicts", [])

    rows, wins, losses, ties = [], 0, 0, 0
    for v in verdicts:
        k = keys.get(v.get("pair_id"))
        if not k:
            continue
        winner = str(v.get("winner", "")).strip().upper()[:1]
        margin = v.get("margin", "")
        if margin == "tie" or winner not in ("A", "B"):
            verdict, ties = "TIE", ties + 1
        elif winner == k["ours_panel"]:
            verdict, wins = "NAGARE", wins + 1
        else:
            verdict, losses = "REFERENCE", losses + 1
        rows.append(
            {
                "pair_id": k["pair_id"],
                "route": k["route"],
                "theme": k["theme"],
                "category": k["category"],
                "opponent": pathlib.Path(k["theirs"]).stem,
                "verdict": verdict,
                "margin": margin,
                "reasons": v.get("reasons", []),
                "fixes": v.get("loser_fixes", []),
            }
        )

    total = wins + losses + ties
    print(f"\n{'pair':10} {'route':18} {'theme':6} {'opponent':22} {'winner':10} margin")
    print("-" * 84)
    for r in sorted(rows, key=lambda r: (r["verdict"] != "REFERENCE", r["route"])):
        print(
            f"{r['pair_id']:10} {r['route']:18} {r['theme']:6} {r['opponent']:22} "
            f"{r['verdict']:10} {r['margin']}"
        )

    pct = (wins / total * 100) if total else 0.0
    print("-" * 84)
    print(f"Nagare wins {wins}/{total} ({pct:.0f}%)   losses {losses}   ties {ties}")

    losing_routes = sorted({r["route"] for r in rows if r["verdict"] == "REFERENCE"})
    if losing_routes:
        print("\nROUTES THAT LOST -- these are the next round's work items:")
        for route in losing_routes:
            print(f"\n  {route}")
            for r in rows:
                if r["route"] == route and r["verdict"] == "REFERENCE":
                    for fix in r["fixes"]:
                        print(f"    - [{r['opponent']}] {fix}")

    report = {
        "round": args.round,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_pct": round(pct, 1),
        "passed": losses == 0 and wins >= max(1, total - ties),
        "losing_routes": losing_routes,
        "rows": rows,
    }
    (outdir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nPASSED: {report['passed']}   -> {outdir / 'report.json'}")
    return 0 if report["passed"] else 2


def cmd_status(_: argparse.Namespace) -> int:
    shots = discover_shots()
    refs = discover_refs()
    print(f"screenshots ({len(shots)}):")
    for s in shots:
        print(f"  {s.relative_to(ROOT)}  {s.stat().st_size:,} bytes")
    print(f"\nreferences ({sum(len(v) for v in refs.values())}):")
    for cat, imgs in sorted(refs.items()):
        print(f"  {cat}: {', '.join(i.stem for i in imgs)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--round", type=int, default=1)
    b.add_argument("--seed", type=int, default=20260816)
    b.add_argument(
        "--per-route",
        type=int,
        default=2,
        help="how many references each Nagare route is pitted against",
    )
    b.set_defaults(fn=cmd_build)

    r = sub.add_parser("reveal")
    r.add_argument("--round", type=int, default=1)
    r.set_defaults(fn=cmd_reveal)

    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
