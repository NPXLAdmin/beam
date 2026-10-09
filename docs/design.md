<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# beam

<div class="byline">
  Claude Opus 5.5 and Robert Duncan 2026-10-08
</div>

Design documents for features in `apps/beam/`. Each feature is one `##` subsection below; the
structure inside each subsection is fixed (Summary, Problem, Target app, Functional workflow,
Schema, Overrides + hooks, Permissions, Out of scope, Open questions).

Nested Handling Units themselves (Bundle Types, Bundle Entries, Nest Cap, scanning a bundle) are
described for users in [Nested Handling Units](../beam/docs/handling_unit_nesting.md). The feature
below covers what happens to bundles on stock transactions.

## Bundles on stock transactions

### 1. Summary

When a stock transaction touches Handling Units inside a bundle, BEAM keeps the bundle's contents
true without anyone editing them. Later, a transaction that brings Handling Units in will also be
able to pack them into bundles as it is submitted.

| Status | Scope |
|---|---|
| Built, unchanged | Reconciliation rules R1–R7 (§ 4.1) |
| This release: tests only | B1, B4 and the Repack split (§ 4.2). The code already behaves this way; the tests pin it. |
| Designed, not built | N1–N8 (§ 4.3) |

### 2. Problem / why now

- **Staff reach into containers.** Someone opens a carton and takes one unit for an order. If
  bundles changed only through Bundle Entries made by hand, the ledger would still say the unit
  is in the carton after it was delivered.
- **Bundles move.** A pallet goes to another warehouse whole, or a few units are taken off it, and
  the contents have to follow without a Bundle Entry for every move.
- **Packing at receipt.** Units are put on a draft receipt's rows before they hold stock, so their
  Bundle Entries can't be submitted until the receipt is. Today an app does that itself from its
  own `doc_events`. Its hooks run in app-install order relative to BEAM's reconciliation, and it
  has to undo its own entries on cancel.
- **Gaps found while mapping the cases** (§ 4.4): a bundle shipped whole leaves empty boxes behind
  and resolves to nothing; scanning an empty bundle does nothing and says nothing; moving a bundle
  without Handling Unit carry-forward empties it.

### 3. Target app

App: `beam`
Module: `BEAM`

### 4. Functional workflow

**Terms.** A *unit* is a Handling Unit holding stock, the bottom of any bundle. A *stock voucher*
is a Stock Entry, Delivery Note, Sales Invoice, Purchase Receipt, Purchase Invoice or Stock
Reconciliation. A voucher takes a bundle *whole* when it takes every unit beneath it at its full
balance (the same test Restrict Member Transactions uses). *Carry-forward* is
`custom_carry_forward` on the Handling Unit Inventory Dimension: with it, a full transfer keeps the
unit's Handling Unit; without it, every transfer creates a new one.

#### 4.1 Built (unchanged)

1. **R1. System**, on submit of a stock voucher that leaves a packed unit with no stock → the unit
   is removed from its own bundle by a system-generated Unpack naming the voucher. The rest of the
   bundle is untouched.
2. **R2. System**, on submit of a stock voucher that leaves a packed unit in a different warehouse
   from the rest of its bundle → it is removed the same way. The bundle's *home* is the warehouse
   most of its untouched units are in, or most of all its units if the voucher touched every one.
   A bundle inside a bundle that has moved elsewhere as a whole leaves as one, its own contents
   intact.
3. **R3. System**, on submit of a voucher that moves a bundle whole with carry-forward on → nothing
   is written. Where a bundle is follows its units.
4. **R4. System**, on submit of a voucher that uses part of a unit → the unit stays packed with
   what is left. A Handling Unit the voucher creates (part of a transfer, Repack or Manufacture
   output, a transfer without carry-forward) is loose. BEAM never puts a unit into a bundle by
   itself.
5. **R5. System**, on cancel of a stock voucher → each Unpack the voucher caused is cancelled where
   every unit in it is back with its bundle, in its bundle's warehouse; otherwise it stands.
6. **R6. System**, on validate of a stock voucher → a row may not carry a bundle as its Handling
   Unit, and a row whose unit is beneath a bundle with Restrict Member Transactions is refused
   unless the voucher takes that bundle whole.
7. **R7. System**, whenever it compares where units are (R2, and the one-warehouse check on a
   Pack) → only the warehouse counts. Inventory dimensions are not considered, so units of one
   bundle may carry different dimension values. (Decided 2026-10-08.)

#### 4.2 This release

Scenario IDs refer to the matrix in § 4.4.

1. **B1. User** submits a Material Transfer moving one unit of Box{a, b} in full to another
   warehouse, carry-forward on → a is unpacked by an Unpack naming the transfer; b stays packed.
   Already tested: `test_moving_one_member_away_unpacks_it`.
2. **B4. User** submits a Material Transfer moving every unit of a bundle in full, carry-forward
   on → no Bundle Entry is written; the bundle and any bundles inside it are intact; scanning it
   adds its units at the new warehouse. Already tested: `test_moving_a_whole_pallet_keeps_it_intact`,
   `test_scanning_a_bundle_that_moved_uses_where_it_is_now`.
3. **Repack split. User** submits a Repack that takes 4 of unit a's 10 into a new Handling Unit c,
   with a inside Box{a, b} → a stays packed holding 6, c is loose, and no Bundle Entry is written.
   Nothing needs unpacking: c was never in the box, and the box's quantity drops to 6 by itself,
   because a bundle lists Handling Units and reads their quantities from the Stock Ledger.
   New tests: `test_repack_split_leaves_the_source_packed`, and
   `test_repack_split_using_up_the_source_unpacks_it` for a split of all 10 (4 + 6): a is used up
   and unpacked (R1), and both new units are loose even if one is still physically in the box.

#### 4.3 Designed, not built

**N1. Bundles used up whole**

1. **System**, on submit of a voucher that takes a bundle whole and leaves it with no stock (it
   shipped, or Manufacture consumed it) → nothing is unpacked from that bundle or from bundles
   inside it. This replaces R1 for bundles taken whole; a partial take still follows R1. If the
   bundle was inside a bundle the voucher did not take whole, it is removed from that one, as in
   R2.
2. **System**, wherever a bundle is shown or scanned → its state is worked out from its contents
   and never stored, like Level: **Empty** (no members), **Holding stock** (at least one unit
   beneath it holds stock), **Used up** (it has members, but none beneath it holds stock).
3. **System**, on cancel of that voucher → the units hold stock again, so the bundle reads Holding
   stock again. There is nothing to undo.
4. **User** submits a Pack into a Used up bundle (a reusable tote coming back) → BEAM first removes
   its used-up members with a system-generated Unpack naming the new entry, then packs.

**N2. Scanning a bundle with nothing to add**

1. **User** scans a bundle on a transaction form → only units holding stock become rows. Units
   that are used up, or have no ledger entries, are skipped (the second currently raises an
   error). This matches the Scan Bundle preview.
2. **User** scans a bundle where nothing is left to add → no rows, and an orange alert that says
   why. Draft: "{bundle} has nothing packed yet; {entry} is still a draft". Empty: "{bundle} is
   empty; it was last unpacked by {entry}", or "its contents left on {voucher}" where the Unpack
   was system-generated. Used up: "{bundle} was used up on {voucher}".
3. **User** scans a bundle in a list view → filters by the bundle's members, as now. A Used up
   bundle still has members, so the scan finds the voucher that used it. An Empty bundle shows the
   alert.
4. **User** scans an Empty or Used up bundle on a Bundle Entry whose Handling Unit is blank → it
   becomes that entry's container, and its Bundle Type is filled in. Once a container is set, a
   scanned bundle is added as a row, as now.

**N3. Carry-forward warning**

1. **User** saves BEAM Settings with Nest Cap above 1 while the Handling Unit Inventory Dimension
   does not carry forward → the settings save, with an orange message: "Handling Unit carry-forward
   is off, so every transfer gives units new Handling Units and moving a bundle whole empties it."
   Not enforced. (Decided 2026-10-08.)

**N4. Posting time**

1. **System**, when it writes an Unpack for a voucher → the Unpack takes the voucher's posting date
   and time, not the time of submission, so a backdated voucher's Unpack sits in the same order as
   its Stock Ledger Entries.

**N5. Where a split bundle is**

A voucher *splits* a bundle when, after it posts, the units beneath that bundle that still hold
stock are in more than one warehouse and the voucher touched at least one of them. Moving a bundle
whole to one warehouse is not a split. Using units up while the rest stay put is not a split
either, because units with no stock don't count. N5 replaces R2's guess (the warehouse most units
are in). A box mostly emptied onto a transfer is still where its remaining units are, so BEAM never
infers home from a majority. Any automatic assumption is a setting, because staff have to follow a
procedure that matches it. (Decided 2026-10-09.)

1. **User** sets **Split Bundle Home** on BEAM Settings. It is required once Nest Cap is
   above 1 and has no default, so whoever turns nesting on chooses:
   - **Ask**: the person submitting says where each split bundle is.
   - **With the units left behind**: the bundle stays in the warehouse it was in before the
     voucher, with the units still holding stock there. If none hold stock there any more, the
     bundle was broken up.
2. **System**, on submit of a voucher that splits a bundle, with the setting at Ask and no answer
   for that bundle → the submit is stopped and rolled back with a `BundleHomeRequired` error that
   lists each split bundle and the warehouses its units are now in.
3. **User**, on a stock voucher form, sees that error → BEAM shows a dialog: one line per split
   bundle, choosing one of the listed warehouses or **Broken up**. On confirm it submits again with
   the answers.
4. **System**, on submit with an answer for each split bundle (from the dialog, or from the
   setting) → every unit and bundle beneath it that is not in the chosen warehouse comes out, by
   Unpacks naming the voucher, as in R2. A bundle that is entirely in one other warehouse comes
   out as one, its own contents intact. For **Broken up**, everything directly inside the bundle
   comes out.
5. **Integration**: code that submits a voucher itself passes the answers on the document before
   submitting, as `voucher.bundle_homes = {"<bundle>": "<warehouse>" or None}`, where `None` means
   broken up. The dialog sends its answers the same way. Under Ask, an automated submit that splits
   a bundle without an answer fails with `BundleHomeRequired`.
6. **System**, on migrate when N5 ships → BEAM Settings that already have Nest Cap above 1 are set
   to **With the units left behind**, the closest match to today's behaviour.

**N6. Packing on submit**

1. **Integration**: an app registers a function on `beam_packing_plan`. For each stock voucher,
   BEAM asks every registered function for a plan, and an app returns `None` for vouchers it does
   not handle.
2. **System**, on validate of a stock voucher with a plan → `check_packing_plan` reports every rule
   the plan breaks, together: bundle type rules, Nest Cap over the whole arrangement, loops, a
   container that holds stock or is a row's unit, a row that brings nothing in, and containers
   whose rows go to more than one warehouse (read from the rows, because nothing is on the ledger
   yet).
3. **System**, on submit, after reconciliation → `pack_from_voucher` submits one Pack per
   container, innermost first. Each takes the voucher's posting date and time and sets Voucher Type
   and Voucher No. Each is not system-generated, so the bundle type's rules apply. A member that is
   inside another bundle is first unpacked from it by an Unpack naming the voucher, which is how
   one transfer moves units from one pallet onto another. If any entry fails, the voucher's submit
   fails.
4. **System**, on cancel, before reconciliation → `unpack_voucher` cancels the entries the plan
   created (those naming the voucher that are not system-generated), newest first. Reconciliation
   then runs as in R5.

A plan is a list with one dict per container:

```python
[
	{"container": "<Handling Unit>", "bundle_type": "Pallet", "members": ["<Handling Unit>"]},
	{"container": "<Handling Unit>", "bundle_type": "Box", "rows": ["<row name>", "<row name>"]},
]
```

- `container` and `bundle_type` are required. The container is an existing Handling Unit with no
  stock, typically created earlier so its label could be printed.
- `rows` are names of the voucher's item rows. Each stands for the Handling Unit that row brings
  in, worked out at submit: `to_handling_unit` on a Stock Entry row with a target warehouse where
  it is set, otherwise `handling_unit`. A plan can therefore be written before any Handling Unit
  exists.
- `members` are existing Handling Units, or containers elsewhere in the same plan, which is how one
  voucher builds boxes onto a pallet.
- `product_bundle`, `identifier_scheme_doctype` and `identifier_scheme` are passed to the Bundle
  Entry if given.

The plan works the same on every stock voucher: receipts, Manufacture and Repack outputs,
transfers, and returns. BEAM adds no fields for it; the app that owns the workflow keeps its own
fields (for example, a Bundle column on receipt rows) and turns them into a plan.

**N7. Packing a unit that holds no stock**

1. **User** submits a Pack with a member that holds no stock, used up or never received → refused:
   "{hu} holds no stock". A plan (N6) is not affected, because it packs after the ledger is
   written. An empty bundle can still be packed, for staging empty cartons.

**N8. Vouchers and tables the hooks do not reach**

1. **System**, on validate, submit and cancel of a Delivery Note or Sales Invoice → R1, R2, R5 and
   R6 also read the Packed Items table, where Product Bundle lines take their stock.
2. **System**, on validate, submit and cancel of a Subcontracting Receipt or POS Invoice → the same
   hooks as the other stock vouchers.

#### 4.4 Scenario matrix

Box{a, b} is a bundle holding units a and b; Pallet{Box1{a, b}, Box2{c}} is two boxes on a pallet.

| ID | Scenario | Behaviour | Rule | Status |
|---|---|---|---|---|
| A1 | A Delivery Note uses up a in Box{a, b} | a unpacked; b stays | R1 | Built |
| A2 | A voucher uses part of a | a stays packed | R4 | Built, tested |
| A3 | Material Issue uses up a | a unpacked | R1 | Built, tested |
| A4 | Sales Invoice with Update Stock uses up a | as A1 | R1 | Built |
| A5 | Sales Invoice without Update Stock | nothing changes | — | Built |
| A6 | Manufacture uses up a packed raw material | unpacked; the finished good is loose | R1, R4 | Built |
| A7 | Repack uses up a | a unpacked; the new unit is loose | R1, R4 | Built |
| A2+A7 | Repack takes part of a into a new unit | a stays packed; the new unit is loose | R4 | This release |
| A8 | A purchase return uses up a | a unpacked | R1 | Built |
| A9 | Stock Reconciliation sets a to 0 | a unpacked | R1 | Built |
| A10 | One voucher uses up every unit in Box{a, b} | today: units unpacked, empty box left | R1 → N1 | Designed |
| A11 | One voucher uses up every unit on a pallet of boxes | today: units unpacked, empty boxes left on the pallet | R1 → N1 | Designed |
| B1 | a moved in full to another warehouse, carry-forward on | a unpacked | R2 | This release (tested) |
| B2 | As B1, carry-forward off | new loose unit at the destination; a unpacked | R1, R4 | Built |
| B3 | Part of a moved | a stays packed; the moved part is a new loose unit | R4 | Built |
| B4 | A bundle moved whole, carry-forward on | intact | R3 | This release (tested) |
| B5 | A bundle moved whole, carry-forward off | every unit replaced by a new loose unit; bundle emptied | R1, R4 | Built; warned by N3 |
| B6 | A whole box moved off its pallet | the box leaves the pallet, intact | R2 | Built, tested |
| B7 | Box{a, b, c}: a and b to two other warehouses | a and b unpacked; c stays | R2 | Built |
| B8 | Box{a, b, c}: a and b to one warehouse, c to another | today: the majority side stays packed. N5: asked, or broken up under "With the units left behind" | R2 → N5 | Built; N5 designed |
| B8a | Box{a, b, c, d}: a, b and c moved away, d left behind | d's side stays packed (today and under N5's setting; asked under Ask) | R2, N5 | Built; N5 designed |
| B9 | Same-warehouse transfer changing a dimension, carry-forward on | stays packed | R7 | Built |
| B10 | Same-warehouse transfer, carry-forward off | new Handling Unit; the original is unpacked | R1, R4 | Built; warned by N3 |
| B11 | Material Transfer for Manufacture of a whole pallet, then Manufacture consumes it | intact in WIP; then A11 | R3, N1 | Designed |
| C1 | Cancel a transfer, recombining | the Unpack is cancelled; a is back | R5 | Built, tested |
| C2 | Cancel a transfer, keeping units separate | a stays out | R5 | Built, tested |
| C3 | Cancel a Delivery Note that used up a | a is back | R5 | Built |
| C4 | As C3, a has since been packed elsewhere | stays where it is | R5 | Built |
| C5 | As C3, the rest of its box has moved since | stays out | R5 | Built |
| C6 | Cancel the receipt of a unit packed after receipt | the unit is unpacked, naming the receipt | R1 | Built |
| C7 | Cancel a Stock Reconciliation that zeroed a | a is back | R5 | Built |
| E1 | A Product Bundle line takes a packed unit | not seen today | N8 | Designed |
| E2 | Subcontracting Receipt, POS Invoice | not hooked today | N8 | Designed |
| E3 | A backdated voucher | Unpack stamped at submission time today | N4 | Designed |
| F1 | Pack a used-up unit | allowed today | N7 | Designed |
| F2 | Pack a Handling Unit with no ledger entries | allowed today; scanning the bundle errors | N7, N2 | Designed |
| F3 | Pack an empty bundle | allowed | — | Built |
| F4 | Scan a draft, empty or used-up bundle | nothing happens today | N2 | Designed |
| G1 | One receipt packs two pallets and leaves a row loose | two Packs naming the receipt | N6 | Designed |
| G2 | Handling Units created at submit | rows resolve after they exist | N6 | Designed |
| G3 | One receipt builds boxes onto a pallet | innermost first; Nest Cap on the whole | N6 | Designed |
| G4 | A plan breaks a bundle type rule | reported on save; submit refused | N6 | Designed |
| G5 | A container's rows go to two warehouses | reported on save | N6 | Designed |
| G6 | A container holds stock or is a row's unit | reported on save | N6 | Designed |
| G7 | Cancel a voucher that packed | its Packs cancelled before reconciliation | N6 | Designed |
| G8 | A later receipt adds to the same pallet | a second Pack | N6 | Designed |
| G9 | Manufacture packs its output into a case | as G1 | N6 | Designed |
| G10 | A transfer moves a unit from one pallet onto another | Unpack from the first, Pack into the second | N6 | Designed |
| G11 | Amend a voucher that packed | Packs made again from the amended rows | N6 | Designed |
| G12 | A Pack fails at submit | the voucher's submit fails | N6 | Designed |

### 5. Schema

#### 5.1 `Handling Unit Bundle Entry` — Touched (no change)

No schema impact; two field descriptions change, see § 6.

#### 5.2 `BEAM Settings` — Touched (one field added to BEAM's own definition, N5)

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `split_bundle_home` | Select | `Ask`, `With the units left behind` | When `nest_cap > 1` (`mandatory_depends_on`) | Label "Split Bundle Home". No default. Description: "When a stock transaction leaves a bundle's contents in more than one warehouse: ask the person submitting where the bundle is, or keep it with the units left in its warehouse." Placed after `nest_cap`. |

#### 5.3 Stock vouchers (`Stock Entry`, `Delivery Note`, `Sales Invoice`, `Purchase Receipt`, `Purchase Invoice`, `Stock Reconciliation`, `Subcontracting Receipt`, `POS Invoice`) — Touched (no change)

No schema impact; see § 6 for wiring.

#### 5.4 `Inventory Dimension` — Touched (no change)

No schema impact; N3 reads `custom_carry_forward` on the Handling Unit dimension.

### 6. Overrides, hooks, and direct file edits

**`doc_events`** (N6, N8; not built). For `Stock Entry`, `Delivery Note`, `Sales Invoice`,
`Purchase Receipt`, `Purchase Invoice`, `Stock Reconciliation`, `Subcontracting Receipt` and
`POS Invoice`:

- `validate` → `beam.beam.bundle.validate_no_bundle_on_rows`,
  `beam.beam.bundle.validate_member_transactions` (existing; added for the last two doctypes),
  `beam.beam.bundle.check_voucher_packing_plan` (new)
- `on_submit` → `beam.beam.bundle.on_stock_voucher_submit` (replaces
  `reconcile_contained_members`; runs reconciliation, then the packing plan)
- `on_cancel` → `beam.beam.bundle.on_stock_voucher_cancel` (replaces
  `reconcile_contained_members`; cancels the plan's entries, then runs reconciliation)

These are `doc_events`, not part of BEAM's Stock Entry class. They run on eight doctypes, the Stock
Entry class is also extended by other apps, and putting both steps inside one function fixes their
order in code instead of in hook lists.

**Other hooks** (N6; not built):

- `beam_packing_plan`: a list of dotted paths, each called as `(voucher) -> list[dict] | None`.
  Documented in `beam/docs/hooks.md`.

**File-direct edits:**

- `beam/tests/test_bundle.py` — Add (this release): `test_repack_split_leaves_the_source_packed`,
  `test_repack_split_using_up_the_source_unpacks_it`.
- `beam/beam/bundle.py` — Add: `on_stock_voucher_submit`, `on_stock_voucher_cancel`,
  `check_voucher_packing_plan`, `check_packing_plan`, `pack_from_voucher`, `unpack_voucher` (N6);
  `get_bundle_state` (N1); the `BundleHomeRequired` exception, a `frappe.ValidationError` (N5).
  Change: `MemberReconciliation` skips depletion removals beneath a bundle the voucher took whole
  (N1), and finds split bundles and settles them from `voucher.bundle_homes` or the setting instead
  of inferring a home (N5); `restore_system_unpacks` selects only Unpacks; `create_system_unpack`
  takes the voucher's posting date and time (N4); `get_row_handling_units` also reads
  `packed_items` (N8).
- `beam/beam/doctype/beam_settings/beam_settings.json` — Add: `split_bundle_home` (§ 5.2).
- `beam/public/js/beam.bundle.js` — Add: a `frappe.request.on_error("BundleHomeRequired", …)`
  handler that shows the N5 dialog on the open form and submits again with `bundle_homes` set.
- `beam/patches.txt` and `beam/patches/` — Add: the N5 migrate patch.
- `beam/beam/doctype/handling_unit_bundle_entry/handling_unit_bundle_entry.py` — Add: refuse
  members that hold no stock (N7); clear used-up members before packing into a Used up bundle (N1).
- `beam/beam/doctype/handling_unit_bundle_entry/handling_unit_bundle_entry.json` — Change the
  description of `voucher_no` to "The voucher whose packing plan created this entry, or, on a
  system-generated entry, the voucher that caused it", and of `system_generated` to "Created
  automatically to keep a bundle's contents true after a stock transaction. Entries created by a
  packing plan are not system-generated, so the bundle type's rules apply to them."
- `beam/beam/scan/__init__.py` — Change: `get_container_form_actions` skips units holding no
  stock and raises the N2 alert when nothing is left; `get_container_list_actions` raises it for an
  Empty bundle; `get_bundle_entry_form_action` sets the container on an entry with none (N2.4).
- `beam/beam/doctype/beam_settings/beam_settings.py` — Add: the N3 warning in `validate`.
- `beam/docs/handling_unit_nesting.md`, `beam/docs/hooks.md` — document each N item as it is
  built.

### 7. Permissions

Unchanged. Handling Unit Bundle Entry:

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| Stock User | ✓ | ✓ | ✓ | | ✓ | |
| Stock Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| System Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Entries written by reconciliation and by packing plans are inserted ignoring permissions, so the
person submitting or cancelling the voucher needs rights on the voucher only.

### 8. Out of scope

- Putting a unit back into its bundle when it holds stock again after being zeroed, for example
  found on a recount. BEAM only removes.
- Inventory dimensions as part of a bundle's location (R7).
- A stored status on Handling Unit or Handling Unit Bundle Entry. State is derived (N1).
- The fields and screens an app uses to build its packing plan. They belong to that app.
- `get_handling_unit` reporting a Purchase Receipt row's quantity instead of the balance. It is
  fixed separately.
- Labels for bundles.

### 9. Open questions

1. N1 (bundles used up whole stay intact, with a derived state) answers "what should a shipped
   bundle show" but has not been confirmed.

Settled: N5 replaces the tie-break with a dialog and a setting (2026-10-09). N8.1's gap is real:
ERPNext posts a Product Bundle line's stock with the Packed Items row's dimensions
(`erpnext/controllers/stock_controller.py`, `update_inventory_dimensions` reads `item_row`), so a
Handling Unit on that row reaches the Stock Ledger.
