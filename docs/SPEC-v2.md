> **Historical document — increment 2 of the v1 build contract** (tag `v1`, August 2026).
> The code has moved on since: PostgreSQL through async SQLAlchemy and Alembic, one React
> app in `web/` (the `frontend/` files named below are gone), data isolated per product,
> the Pampufa's `nagare.*` roles and the platform integration of phases N1–N3. What still
> holds: the invariants of R2.1–R2.3 — the structure rules `normalize_structure()`
> enforces, WIP editable at column, group and flow scope, lanes as a view that never
> reclassifies a card. Two changes: lanes are turned on by the URL (`?raias=classe`),
> not restored from `localStorage` (only the collapsed lanes are remembered there); and
> on a product board the flow locks after the first card (NG13: from then on only WIP
> limits, the SLE and column names and colours change). The current API is in
> [API.md](API.md); the decisions since v1 are in the Obsidian vault,
> `grupo-sr/plataforma/decisoes/integracao-nagare.md`. The text below is kept as written.

# Nagare Kanban — Build Contract, increment 2

> Extends `docs/SPEC.md`, which remains authoritative for everything it already covers
> (DDL, routes, RBAC, flags, metric formulas, design tokens). This file covers ONLY the
> three new requirements. Where the two disagree, this file wins for these three features.

---

## R2.1 — Columns must be fully customisable (extends Req. 2)

The user must be able to **add, remove, and update** columns from the UI, mapping their
own Upstream and Downstream flow. Today the backend supports this completely and the
frontend exposes none of it.

### Backend — already implemented, do not rewrite

```
POST   /api/v1/boards/{id}/columns          {name, segment, kind, position?, wip_min,
                                             wip_max, group_id?, color?, description?}
PATCH  /api/v1/columns/{id}                 partial update, incl. segment/kind/group_id
DELETE /api/v1/columns/{id}                 409 `column_not_empty` if it holds cards
POST   /api/v1/boards/{id}/columns/reorder  {ordered_ids: []}
POST   /api/v1/boards/{id}/groups           PATCH/DELETE /api/v1/groups/{id}
```

Invariants the backend already enforces via `normalize_structure()` — the UI must
*surface* them, never re-implement them:

- exactly one commitment point: the first `downstream` column, recomputed automatically
  whenever columns are added, reordered, or change segment;
- exactly one delivery point: the last `done` column;
- an `envelope` column may only be the first upstream column;
- groups must span contiguous columns;
- a column holding cards cannot be deleted (`409 column_not_empty`).

### Frontend — the work

A **board structure editor**, reachable from the board toolbar (structure icon, label
"Edit columns") and from each column header's menu ("Edit column…", "Insert column
before/after", "Delete column").

- Full-height right drawer, listing every column in unified flow order, grouped under
  its column group, with the commitment point drawn as a divider *between* the upstream
  and downstream runs exactly as the board does.
- Per row: name, `kind` selector (envelope / queue = wait / activity = touch / done),
  `segment` selector (upstream / downstream), group selector, WIP min & max, colour,
  description, plus reorder handles (drag, and ↑/↓ buttons for keyboard users) and a
  delete action.
- Changing `kind` must explain the consequence inline: **queue accrues wait time,
  activity accrues touch time**, and that is what makes flow efficiency measurable. A
  user must not have to guess which one to pick.
- Adding a column: a form with sensible defaults derived from the insertion point
  (segment inherited from the neighbour, `kind` defaulting to `queue`).
- Deleting a column that holds cards must NOT show a raw error. Catch
  `column_not_empty` and offer the real remedy: name the count and offer to move those
  cards to an adjacent column first, then retry the delete.
- Live preview of the resulting flow at the top of the drawer (a compact chip strip:
  upstream chips, commitment marker, downstream chips) that updates as edits are made,
  so the user sees the commitment point move before saving.
- Group management in the same drawer: create, rename, recolour, set group WIP, delete;
  reject non-contiguous selections with a readable message rather than a 409 dump.
- Optimistic where safe, always reconciled against the server response. Every mutation
  refreshes the board behind the drawer.
- Gated on the `manage_boards` capability: `master` and `administrator` only. Everyone
  else sees the structure read-only, with an explanation rather than a hidden button.

---

## R2.2 — WIP limits must be editable (extends Req. 3)

Min and max must be editable at all three scopes, from the board itself — not only
buried in a settings screen.

- **Per column** — click the WIP badge in the column header to open a small popover with
  min and max steppers, "no limit" for max, live validation (`min ≤ max`, both ≥ 0), and
  an immediate `PATCH /columns/{id}`. Show the resulting state
  (`under|ok|at_limit|over`) as the user types, so they can see they are about to put the
  column over limit.
- **Per group** — same interaction on the group header badge → `PATCH /groups/{id}`.
- **Whole flow** — same interaction on the toolbar's whole-flow pill →
  `PATCH /boards/{id}` (`wip_min`, `wip_max`).
- Escape cancels, Enter commits, the control is fully keyboard operable, and the change
  is announced to screen readers via a live region.
- After every change, re-fetch `/boards/{id}/wip` and repaint every badge — a WIP edit
  can flip a *group* or the *flow* into `over` even when the edited column is fine.
- Also editable from the structure editor (R2.1), which is the bulk path. Both routes
  must agree.
- Same RBAC gate as R2.1.

---

## R2.3 — Swimlanes by class of service (new, extends Req. 6)

The board must be able to render **horizontal lanes grouped by class of service**, and
this is **optional and per user**.

- A toolbar control ("Lanes: off / class of service") toggles it. Default **off** — the
  flat board stays the default experience.
- When on, the board splits into one lane per class of service in the fixed Kanban
  priority order — **expedite, fixed date, standard, intangible** — each lane carrying
  its class colour as a left edge marker, its name, and its card count.
- Lane headers are sticky on vertical scroll and stay aligned with the column grid as it
  scrolls horizontally.
- A lane with no cards collapses to a slim labelled strip; it is not hidden, because its
  emptiness is information (no expedite work in flight is a *good* signal worth seeing).
- Lanes can be individually collapsed, and the state persists.
- Drag and drop must keep working across lanes: dragging within a lane keeps the card's
  class; the forward-only rule and every WIP policy are unchanged. A card dropped into a
  different **lane** does not change its class of service — lanes are a view, not an
  editor. If a user drags across lanes, move the card by column and leave the class
  untouched (do not silently reclassify).
- WIP limits remain **per column**, not per lane cell. The column header count is the
  total across lanes. Do not imply a per-lane limit that the system does not enforce.
- The preference is per user *and* per board, persisted in `localStorage` under
  `nagare.lanes.<boardId>`, restored on mount, and reflected in the URL (`?lanes=sc`) so
  a lane view can be shared as a link.
- Works in the unified, upstream and downstream segments, in both themes, at 1440px and
  1024px.

---

## Ownership for this increment

| Files | Owner |
|---|---|
| `frontend/js/board.js`, `frontend/css/board.css` | **BOARD** — lanes, WIP popovers, entry points |
| `frontend/js/structure.js`, `frontend/css/structure.css` (both new) | **STRUCTURE** — the editor drawer |
| `backend/app/routers/boards.py`, `tests/test_structure.py` | **BACKEND** — gaps, validation, tests |
| `frontend/index.html` | already wired by the integrator — **do not edit** |

`structure.js` must export `openStructureEditor(boardId, { onChanged })` and nothing
else; `board.js` imports and calls exactly that. That single function is the contract
between the two frontend agents — neither may edit the other's files.

## Definition of done for this increment

1. `python -m pytest -q` green.
2. `python tools/ui_check.py` → 0 problems across every route in both themes.
3. A column can be created, renamed, re-kinded, re-segmented, reordered and deleted
   entirely through the UI, and the commitment point visibly follows.
4. Deleting an occupied column offers to move the cards instead of showing an error.
5. WIP min/max editable at column, group and flow scope, with every badge repainting.
6. Lanes toggle on/off, persist per board, and drag-and-drop still obeys forward-only
   and WIP rules with no reclassification.
7. No console errors, AA contrast, full keyboard operation, `prefers-reduced-motion`
   respected.
