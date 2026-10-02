<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Nested Handling Units

<div class="byline">
  Claude Opus 5.5 and Robert Duncan 2026-10-01
</div>


A Handling Unit identifies one quantity of one item. Nesting lets you say that
several of them travel together: eaches in a box, boxes on a pallet, pallets in
a trailer. The container gets its own Handling Unit and its own barcode, so a
single scan of a pallet brings every item on it onto whatever document you are
working in.

Nesting is recorded in a new doctype, **Handling Unit Bundle Entry**. The
Handling Unit doctype itself is unchanged — a Handling Unit still means exactly
what it meant before, and existing Handling Units need no conversion.

Nesting is off until you turn it on. See [Nest Cap](#nest-cap).

## Containers and contents

A **container** is a Handling Unit that holds other Handling Units. A **leaf** is
a Handling Unit that holds stock — the ordinary kind that BEAM has always
generated on receipt and manufacture.

Only leaves carry stock. A container never appears in the Stock Ledger, and it
has no quantity of its own; its contents are always read from the bundle records.
This is what lets a pallet hold a mixture of different items without confusing
the ledger — each item stays on its own leaf, with its own quantity and
warehouse.

| Item             | Warehouse          | Handling Unit  |       Quantity |
| ---------------- | ------------------ | -------------- | --------------:|
| Cocoplum         | Storeroom          |            123 |          40 Ea |
| Limequat         | Storeroom          |            124 |          20 Ea |

Bundling 123 and 124 into pallet 999 leaves the stock ledger untouched. Scanning
999 now finds both rows.

```
999   Pallet
 ├── 123   Cocoplum   40 Ea
 └── 124   Limequat   20 Ea
```

Every leaf under one container must be in the same warehouse. A container cannot
straddle two warehouses.

## Bundle Types

A **Handling Unit Bundle Type** is the kind of container something is — Box,
Case, Pallet, Tote, Trailer. You define the vocabulary that matches your
operation; BEAM ships none, because a default set would be wrong for most
warehouses.

A type is what a container *is*, not how deep it sits. The same Box type can
hold loose eaches in one place and other boxes somewhere else, so a type is
never tied to a particular level. Levels are worked out from what is actually
inside a container and are never entered by hand.

Each type carries the rules for what may go inside it. All of them are optional,
and a type with nothing configured accepts anything.

| Setting | Effect |
| --- | --- |
| **Allowed Child Types** | The container types permitted inside. Leave empty to permit any. |
| **Allow Loose Handling Units** | Whether ordinary stock-bearing Handling Units may go in directly. Turn this off on a Pallet to require that everything is boxed first. |
| **Require Uniform Members** | When set, everything inside must be the same kind — all leaves, or all one type. Prevents a half-packed pallet of mixed cases and loose units. |
| **Max Members** | A count limit. Leave at zero for unlimited. |
| **Seal When Contained** | When set, a container of this type cannot be packed or unpacked while it is itself inside something else. It has to come off the pallet first. |
| **Restrict Member Transactions** | When set, the contents cannot be used on a stock transaction while they are inside. See [Reaching into a container](#reaching-into-a-container). |

So "a pallet takes cases or boxes, but never loose units" is a Pallet with
**Allowed Child Types** of Case and Box and **Allow Loose Handling Units**
turned off. "A case takes loose units or boxes, but nothing bigger" is a Case
with **Allowed Child Types** of Box and **Allow Loose Handling Units** left on.

## Bundling

Create a Handling Unit Bundle Entry and choose a **bundle type**.

If the container already has a barcode — a tote you use over and over — scan it.
For a new carton, leave the Handling Unit field empty and save. BEAM creates the
container's Handling Unit at that point, and it becomes scannable straight away,
so you can print a Handling Unit label and tape it to the box before you start
filling it.

Then scan everything going into it. Each scan adds a row showing the item,
quantity, unit of measure, and warehouse so you can confirm what you picked up.
Without a scanner attached, **Scan Handling Unit** in the toolbar does the same
from a typed or pasted code.

The **Bundle Contents** section shows the whole arrangement as you build it: its
level, how many Handling Units holding stock it contains, the total quantity, the
warehouse, and a tree of every box and item inside.

A row can be either a Handling Unit or another Bundle Entry, which is how boxes
go onto pallets — you scan the box, and the box brings its contents with it.

Submitting the entry checks the type's rules, the depth limit, and that
everything is in one warehouse, then records the bundle. **Nothing moves.** No
Stock Entry is created and no Stock Ledger Entry is written, because bundling is
a statement about how goods are arranged, not a movement of them.

Bundle records are a ledger. Packing more into the same container later is
another entry rather than an edit, so the history of a pallet is readable:

```
HU-BE-2026-00001   Pack     999    + 123, 124
HU-BE-2026-00002   Pack     999    + 125
HU-BE-2026-00003   Unpack   999    − 123

999 now contains 124 and 125
```

A Handling Unit can only be in one container at a time.

## Container identifiers

A container usually needs a printed identifier that means something outside your
warehouse — a GS1 SSCC-18 on a shipping label, or a customer's own shipping unit
code. A Bundle Entry can be linked to an **identifier scheme**, and on submit
BEAM asks that scheme for the next identifier, stores it, and creates a barcode
for it too, so the printed string scans straight back to the container.

Identifiers are issued at submit, when the contents are settled — which is also
when you would normally label a finished pallet. Until then the container is
still scannable by its Handling Unit barcode, so nothing is blocked while you
pack.

BEAM provides the mechanism only. The schemes themselves belong to the
applications that own them, so an integration can mint codes in whatever format
it requires without modifying BEAM. See [Extending BEAM With Custom
Hooks](./hooks.md).

## Product Bundles

A Bundle Entry can reference a **Product Bundle** to declare that the container
is a kit of that shape. BEAM compares what is actually inside against the
Product Bundle's items and quantities — counting items at any depth, so a pallet
of boxes is judged on what is in the boxes — and reports whether the kit is
complete, which makes a short-packed pallet visible before it ships.

This is a check, not a constraint — an incomplete kit can still be submitted.

## Scanning a container

Scanning a container's barcode with a Delivery Note, Sales Invoice, Stock Entry,
Packing Slip, or Stock Reconciliation open adds **one row per item inside it**,
each with its own quantity, warehouse, and inventory dimensions.

Scanning pallet 999 on a Delivery Note produces:

| Item             | Warehouse          | Handling Unit  |       Quantity |
| ---------------- | ------------------ | -------------- | --------------:|
| Cocoplum         | Storeroom          |            123 |          40 Ea |
| Limequat         | Storeroom          |            124 |          20 Ea |

Depth does not matter. Scanning a trailer that holds pallets of boxes of eaches
produces a row for every each, because the rows that reach the document are
always the stock-bearing leaves.

On a Stock Entry, **Scan Bundle** under **Handling Units** does the same from the
toolbar, and lists what will be added before you add it. The button appears once
Nest Cap for the company is above 1.

In a list view, scanning a container filters to every document that references
anything inside it.

The [Handling Unit Traceability report](./hu_traceability_report.md) continues to
work as before, tracing the leaves.

## Unpacking and corrections

To take something out, submit an entry with a purpose of **Unpack** listing what
to remove. The contents come back out as loose Handling Units at their own
level.

To correct a mistake, cancel the entry. Contents revert to whatever the remaining
entries say — there is nothing to unwind by hand, because contents are always
read from the ledger rather than stored on the Handling Unit. Amending a
cancelled entry replaces it.

## Reaching into a container

Staff open cartons. Someone can lift the lid on a pallet, open a box, and take a
single unit for an order without unpacking anything first, and by default BEAM
allows it — the levels describe how goods are packed, not who may touch them.

When that happens the bundle corrects itself. If the unit that was taken is now
used up, or has ended up in a different warehouse from the rest of its
container, BEAM removes it from the container automatically and records an entry
saying which document caused it. The box's contents stay accurate without anyone
having to remember to update them, and the history explains why the unit left.

If you need this prevented, turn on **Restrict Member Transactions** on the
container's type. The transaction is then refused until the unit is explicitly
unpacked.

## Nest Cap

**Nest Cap** on BEAM Settings is the deepest nesting permitted, per company. It
defaults to `1`, which means no nesting at all — so installing or upgrading BEAM
changes nothing until you raise it. A cap of `3` allows each, then box, then
pallet.

The cap applies to a whole arrangement, not to one container in isolation.
Packing a box deep inside a pallet is refused if it would push the pallet past
the cap, even when the box itself looks shallow.

Lowering the cap below bundles that already exist is allowed. BEAM tells you how
many are affected and asks you to confirm; the existing bundles remain valid and
usable, and only new entries are held to the lower cap.

## Extending

Bundling rules and identifier schemes are both extension points, so an
application can enforce its own nesting policy or mint identifiers in its own
format without changing BEAM. See [Extending BEAM With Custom
Hooks](./hooks.md).

A screen that shows what is inside a container can read it one level at a time
with `beam.beam.bundle.get_bundle_tree`, opening each branch only when it is
expanded, so a deep trailer is as quick to inspect as a single box.
