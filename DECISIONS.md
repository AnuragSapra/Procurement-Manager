# Decisions

This document records how the app treats Procurement Policy v5.0 where the policy or the brief is silent. It is written for an evaluator who has the policy and the worked examples.

A **policy requirement** is a rule taken from the policy or the brief. An **implementation choice** is a gap this app had to fill. Sample purchase-request numbers are not special-cased. Where a worked example is named, it is the check that the choice reproduces, not a hard-coded answer.

## What the policy requires

These points are treated as mandatory. The numbers in parentheses are policy clauses.

- If the request supplies an item code that is in the item master, that code wins. If no code was given, the description is matched (1.1). No master match is a new item: never clubbed, valued at quantity times the estimated unit price, and routed to new-item sourcing (1.2).
- Lines club on the same item and plant inside the stated windows. A line joins the earliest requirement it fits. Any Emergency line makes the requirement an emergency (2.1–2.3).
- The valuation ladder (3.1) is the contract rate that will be used, otherwise the lowest applicable contract rate when every contract is over its ceiling, otherwise the most recent Active-supplier purchase in the 365 days before the requirement date, otherwise the standard price. A new item uses the estimated unit price.
- A contract applies when it covers the item, the requirement date is inside its term, and the supplier is Active (4.1). Consumed value plus this requirement at that contract's own rate must not exceed the ceiling (4.2). The lowest rate that still has room is selected (4.3).
- The first matching rule wins: 5.1 Capex sourcing; 5.2 a usable contract; 5.3 emergency with value at or below ₹5,00,000, a recent Active-supplier purchase, and a preferred supplier; 5.4 value below ₹2,00,000 with that same purchase and preferred-supplier test; 5.5 value below ₹10,00,000 as RFQ; 5.6 value of ₹10,00,000 or more as RFP.
- The 365-day purchase test in 5.3 and 5.4 is an eligibility test. It is not the preferred-supplier list. Preferred-list membership and the reference price use 548 days (6.1, 6.3). Clause 6.2 names frequency, recency, contract history, on-time delivery, and quality, and leaves the weighting open.
- Blocked and Inactive suppliers never receive a purchase order, a suggestion, a valuation price, or a place on the preferred list (5.7).
- Purchase orders for the same supplier, plant, and route within 24 hours are consolidated. Sourcing requests stay separate (7.1, 7.2).
- The recorded result cites the clause, the policy version, and the contract or purchase order relied on. An override needs a written reason and keeps the original decision (8.1).
- The brief requires policy to be versioned data with an effective date, a readable preview, and buyer approval. A new rule made of facts the engine already computes, including a rule for one plant, must be possible without a code change.

## D-001 Supplier ranking

Clause 6.2 does not fix a formula. The three published lists and the suggestion orders in the worked examples line up with one weighted score, highest first:

`100 × purchase-order count + 500 × recency + 200 × contract + on-time percent / 10 + quality rating`

Recency is 1.0 when the last purchase in the 548-day window is within 90 days, 0.5 when it is within 365 days, and 0 otherwise. A supplier who qualifies only through a contract has a purchase count of 0 and recency 0.

**Choice.** Use that score. A full recency step is worth two and a half purchases, which places the more recent supplier ahead of one with two extra purchases on the hydraulic-oil list. A contract is worth two purchases, which places the contract holder between the five-purchase and two-purchase suppliers on the bearing list. On-time and quality move the score by only a few points, enough to separate two suppliers who match on count and recency, and not enough to overturn frequency, recency, or a contract.

**Consequence.** Preferred suppliers, and the rank-1 supplier used for an emergency or spot order, follow this score. The weights are stored in the policy document so a later amendment can change them without a code change. The preferred page states the score for each row.

## D-002 Tie-break

The examples do not show a complete tie on the score, or two purchases on the same day.

**Choice.** Equal scores sort by supplier id ascending. When two purchases fall on the same day, the higher purchase-order number is the later one. That purchase sets recency and, if it is the latest, the reference price.

**Consequence.** Equal suppliers do not swap places between runs. The reference price is the latest purchase in the 548-day window, or the rate on the contract with the latest start date when there is no such purchase.

## D-003 Ambiguous item matching

Clause 1.1 does not say what to do when several master items fit one description.

**Choice.** One match is accepted. Several matches are not auto-picked and are not treated as a new item. In chat the person is asked which catalogue item was meant. Short forms are folded: case and punctuation, and the tokens `brg`, `rod`, `hyd`, and `pneu`. Noise words such as "urgent", "for maintenance", "as per sample", and "repeat" are ignored. A number or size in the request must occur on the item, so M10 does not match M16.

**Consequence.** A vague name such as "gloves" offers both catalogue gloves. The word "urgent" inside a description does not make the line an emergency. Only the urgency field does.

## D-004 Missing estimated price

Clause 1.2 needs an estimated unit price to value a new item. It does not name a route when that price is absent. The brief allows `NOT_PROCESSED` with a reason.

**Choice.** A new item with an estimate is valued at quantity times that estimate and routed to new-item sourcing. A new item with no estimate is `NOT_PROCESSED`. A known item does not need an estimate. A quantity of zero, and a plant that is not in the plant master, are also `NOT_PROCESSED`.

**Consequence.** The app does not invent a price. The plant master is the allow-list. Chat asks for the estimated unit price before a new item is created.

## D-005 Batch isolation

Clause 4.2 consumes ceiling in requirement-date order from the register baseline. The blind test processes one batch under v5.0 and the same batch again after an amendment. Both runs need the same starting balances.

**Choice.** Each batch copies the opening consumed amounts into memory. The run never writes them back. When two requirements share a requirement date, consumption continues by first-line time, then purchase-request number, then line number. If two usable contracts have the same rate, the lower contract id is used. The whole requirement fits the remaining ceiling, or the contract is not used. The comparison uses the exact product, not the rounded rupee.

**Consequence.** Two calls to `POST /api/process-batch` start from the same stored baselines. Ceiling used in the first call is not visible to the second call.

## D-006 History windows and rounding

The policy says "within" 14 days and 7 days, and "in the 365 days before" a date, without saying whether the boundary day counts. Worked-example prose rounds some unit prices to the nearest rupee.

**Choice.** The 14-day creation window and the 7-day need-by window include the 14th and 7th day, measured from the requirement's first line. The 24-hour purchase-order window includes the 24th hour. The 365-day valuation window and the 548-day preferred window end before the requirement date and do not include that date. Group value is rounded half up to the rupee, and the section 5 limits use that rounded value. Below ₹2,00,000 is strict, so ₹2,00,000 is an RFQ. At or below ₹5,00,000 includes ₹5,00,000. ₹10,00,000 or more is an RFP.

**Consequence.** A requirement that lands on a ceiling threshold is classified from the rounded rupee. The unit price on a purchase order can still be the unrounded contract rate or reference price. All figures are INR excluding GST.

## D-007 Valuation price and purchase-order price

Section 3 and section 5 do not use the same price. A spot or emergency purchase order goes to the first preferred supplier at that supplier's own reference price (6.3). The requirement value can still come from a more recent purchase placed with someone else.

**Choice.** Keep both. Example 6 is the check: the requirement value uses the latest purchase of the item, and the purchase order names the preferred supplier at that supplier's own last price.

**Consequence.** The export can show a supplier, a unit price, and a group value that do not multiply to each other. Capex skips the contract rate for valuation, uses purchase history or the standard price, and does not consume a ceiling. The route remains Capex sourcing.

## D-008 Contracts the register does not list

There is no status history, and two signed contracts are not in the register.

**Choice.** Today's supplier status is used. An expired contract is not usable for a purchase, but it still places an Active supplier on the preferred list if it had started by the as-of date. The two PDF-only contracts are read from the signed PDFs and start at a consumed amount of zero: RC-2026-031 (S021, 15 November 2026 to 14 November 2027, ceiling ₹20,00,000) and RC-2026-033 (S035, 20 November 2026 to 19 November 2027, ceiling ₹50,00,000). A buyer can add a further contract by uploading its PDF.

**Consequence.** Those contracts can be selected like register contracts. A later load does not rebuild consumed value from purchase history. Blocked S004's aluminium-plate contract is not used, and that item is valued at the standard price when there is no Active purchase in the valuation window.

## D-009 Policy extensibility

The brief requires new rules without a code change. It does not specify the file format.

**Choice.** A policy version is a document: version, effective date, status, windows, limits, ranking weights, and ordered rules. The engine reads it. It does not execute it as code. An amendment can change a number or add a rule that uses facts already computed for every requirement: item type, category, plant, department, value, urgency, whether a contract is usable, and whether an Active supplier bought the item in the history window. Department, when a rule names it, is the department on the first line. A buyer previews the change in plain language and approves it. The same change can be described in chat, which saves the preview. A requester cannot approve it. The effective date is mandatory. The requirement uses the latest approved version whose effective date is on or before the requirement date. When two approved versions share that date, the higher version number wins.

**Consequence.** An amendment dated on the same day as version 5.0 replaces it for requirements on or after that day. Lines earlier than the effective date stay on the previous version. A rule that asks for a spot or emergency order still needs a preferred supplier, or it does not fire.

## D-010 Suggestions and the preferred export

Clause 6.4 asks for suggestions when the preferred list is short. The brief asks for a top-three CSV.

**Choice.** Suggestions walk the preferred list, then other Active suppliers in the item's category, ordered by on-time percent times quality rating, then supplier id. If fewer Active suppliers exist than the route asks for, only those that exist are suggested. The CSV is the top three preferred suppliers, with columns `item_code`, `rank`, `supplier_id`, and `reference_price`. An item with nobody eligible is omitted from that file. The preferred page still lists the item and says why.

**Consequence.** Category suppliers can appear on a sourcing suggestion and do not appear on the preferred-supplier CSV.

## D-011 Purchase-order numbering

Numbers are assigned in the order requirements open a new purchase order. A quantity of zero is not a requirement.

**Choice.** Keep every real line. The two earliest lines in the sample club into the first spot purchase order, so later worked-example numbers sit one lower than a run that had dropped those lines. The gaps between the examples are unchanged: the hex-bolt requirements still share one purchase order, and the hose order is still the one just before the oil-seal order.

**Consequence.** The engine does not drop lines in order to copy the numbering printed in the worked examples.

## D-012 Who may release a decision

The brief requires a buyer workspace, an override comment, and a record of who acted and when. It does not define sign-in.

**Choice.** The role is stored with the person and is never taken from the form. A requester can raise a requisition. Only a buyer can run a batch, approve or override a requirement, approve a policy, add a contract, or read the audit log. The person who raised a requirement cannot release it. Approve releases the engine's decision. Override stores a written comment, marks the requirement overridden, and keeps the original route, supplier, price, and value.

**Consequence.** A silent change to a calculated decision is not possible. The comment and the original decision are both kept.
