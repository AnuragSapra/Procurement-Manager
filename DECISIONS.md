# Decisions

The procurement policy does not cover every row in the data. These are the choices the app implements. The same notes, with links between them, are in the Obsidian vault under `notes/`.

## How a line becomes a decision

Match the item, club lines into a requirement, value the requirement, then apply the active policy rules in order. The first rule that fits wins. Purchase orders are consolidated after the route is known.

A requirement uses the latest approved policy whose effective date is on or before the requirement date. The requirement date is the creation date of its first line. When two approved policies share that date, the higher version number applies, so an amendment dated on the same day as version 5.0 replaces it.

## Item matching

A valid item code is used as given. Otherwise the description is matched. Short forms are normalised: case and punctuation are folded, and the tokens `brg`, `rod`, `hyd` and `pneu` expand to bearing, electrode, hydraulic and pneumatic. Noise words that appear in the sample (`urgent`, `for maintenance`, `as per sample`, `repeat`) are ignored. A number or size in the request must occur on the item, so M10 does not match M16.

Two descriptions are not in the item master and stay `NEW`: drinking water purifier (`PR-26-00170`) and ultrasonic thickness gauge (`PR-26-00171`).

The word "urgent" in a description does not make the line an emergency. Only the urgency field does. In chat, the words "urgent" and "emergency" set Emergency, and the assistant asks the person to confirm before creating the PR.

## Chat

Chat is a parser. It can greet you, and it asks for one missing fact at a time: the item, the quantity, the plant, the need-by date, and an estimate when nothing in the item master matches. A greeting or a stray word is not an item, so it does not answer those with "how many?". "Bhosari", "PUN" and "PUN1" are the Bhosari plant. "Chakan", "CHK" and "CHK1" are the Chakan plant. A near miss such as "chaka" asks whether that means CHK1, and the item already chosen is kept. "Pune" on its own is ambiguous, because both plants are in the Pune area, so the assistant asks which one. A plant code that is not PUN1 or CHK1, such as PUJ1 or PUN2, is refused. Need-by dates can be `20/11/2026`, `20-11-2026`, `2026-11-20`, `20 Nov 2026`, `Nov 20`, a weekday such as Friday, "next Friday", "tomorrow" (also "tommorow" and "tmrw"), "today", "asap", "in 3 days", "in two weeks", "next month", or "end of the month". A short answer that is not a plant or a date does not replace the item. "Next Friday" is the coming Friday, or seven days ahead when today is already Friday. A bare weekday means the coming day, including today when today is that day. Each person in the header has a separate conversation. A chat PR is numbered `PR-CHAT-#####` and is clubbed only with other chat PRs.

## Lines that are not processed

- A plant that is not in the plant master is `NOT_PROCESSED`. `PR-26-00173` is on `PUN2`. The policy applies to PUN1 and CHK1, which are the only rows in the plant master. The decision column is also `NOT_PROCESSED`.
- A new item with no estimated unit price is `NOT_PROCESSED`. A known item does not need the estimate. The sample's two new items both have an estimate.
- A quantity of zero is `NOT_PROCESSED`. `PR-26-00172` asks for 0 cutting wheels. There is nothing to price or to club.

The plant master is the allow-list. Adding a plant there is enough for the engine to accept it.

## Clubbing

Lines are considered in order of creation time, then PR number, then line number. A line joins the earliest requirement with the same item and the same plant when both of these hold:

- the creation dates are at most 14 calendar days apart
- the need-by dates are at most 7 days apart, either side

Both windows are inclusive. They are read from the policy, not hard-coded. New items are never clubbed, even with each other. "Within 14 days" is judged from the requirement's first line, as the policy says.

If any line in the requirement is Emergency, the whole requirement is an emergency.

## Valuation and the price on the PO

Section 3 and section 5 name different prices, and example 6 only adds up if both are kept.

The requirement value, which is what the limits in section 5 use, is quantity times the first available of:

1. the rate of the contract that will actually be used
2. if every applicable contract is over its ceiling, the lowest of those rates
3. the price on the most recent PO for the item from an Active supplier in the 365 days before the requirement date
4. the item's standard price

A new item is quantity times the requester's estimate.

The group value is that amount rounded half-up to the rupee. 15 × 9168.71 = 137530.65, which is ₹1,37,531 in example 1. Rule comparisons use this rounded value. "Below ₹2,00,000" is strict, so ₹2,00,000 is an RFQ. "At or below ₹5,00,000" includes ₹5,00,000. "₹10,00,000 or more" is an RFP.

The unit price printed on a PO is not always that valuation price.

- A contract PO is at the contract rate. Value and unit price match.
- A spot or emergency PO goes to the first preferred supplier at that supplier's own reference price (section 6.3). The requirement value can still come from a more recent PO placed with someone else.

Example 6 is the proof. VCI bags, 7,370 at Pune, value ₹1,56,170. That is 7,370 × 21.19, and 21.19 is Zerust's PO on 19 June 2026, the latest purchase of the item. The supplier named in the example is Vardhman (S027), whose own last price is 21.59, which rounds to the ₹22 in the prose. The export therefore has supplier `S027`, unit price `21.59`, and group value `156170`.

"In the 365 days before the requirement date" does not include the requirement date. PO history ends on 28 October 2026, so the sample never sits on that boundary. The same exclusive end is used for the 548-day preferred-supplier window.

Blocked and Inactive suppliers are ignored for this history, for valuation, and for suggestions. Example 10 depends on it: aluminium plate's only contract is with Blocked S004, the item has no Active purchase in the last year, and the value is the standard price, 220 × 365 = 80,300.

## Contracts and ceilings

A contract applies when it covers the item, the requirement date is inside the term, and the supplier is Active today. There is no status history in the master, so today's status is used.

The opening consumed amount is the register figure. It is not rebuilt from PO history. Contracts that exist only as PDFs were signed after the register was updated, so they start at zero:

- RC-2026-031, S021, 15 Nov 2026 to 14 Nov 2027, ceiling ₹20,00,000. ITM-1036 at 3537.76, ITM-1037 at 2439.01, ITM-1039 at 2746.87.
- RC-2026-033, S035, 20 Nov 2026 to 19 Nov 2027, ceiling ₹50,00,000. ITM-1056 at 64654.36, ITM-1057 at 10689.51.

Ceiling room is `consumed + quantity × contract rate <= cap`, using the exact product, not the rounded rupee. The whole requirement fits or the contract is not used. Requirements take ceiling in requirement-date order, which for this data is the same as first-line timestamp, then PR number. Consumption is calculated inside one batch and is not written back to the register, so processing the same file twice is stable. A second batch does not see the first batch's consumption. That is deliberate: the blind test reprocesses one batch from the same opening balances.

If two usable contracts have the same rate, the lower contract id is used. The sample does not hit this tie.

An expired contract is not usable, but it still puts an Active supplier on the preferred list if it had started by the as-of date. RC-2025-020 ended on 30 September 2026 and still counts for Castrol on the November lists.

## Preferred suppliers

Someone is on the list when they are Active and either bought the item in the 548 days before the date, or held a contract for it that had started by that date.

Recency of their last PO in that window, using the buckets published with the worked lists: 1.0 if the last PO is within 90 days, 0.5 if it is within 365 days, otherwise 0.

Score, highest first, ties broken by supplier id:

`100 × PO count + 500 × recency + 200 × contract + on-time percent / 10 + quality rating`

Why these weights:

- A full recency step is worth two and a half purchases. That puts Janatics (4 POs, last buy 14 days before 2 Nov) ahead of Hydrax (6 POs, last buy in February) for hydraulic oil. Example 5 names Janatics.
- A contract is worth two purchases. That puts Industrial Bearings (1 PO and a contract) ahead of Chakan Transmission (2 POs) and behind Mahalaxmi (5 POs). Example 3 names that order.
- On-time and quality move the score by only a few points. They put Vardhman ahead of Zerust for VCI bags, where both have one PO and the same recency bucket, and they do not overturn frequency, recency, or a contract.

The same score reproduces the three published lists as of 1 Nov 2026 and the suggestion lists in examples 3, 9, 11 and 12.

Suggestions walk that list, then add other Active suppliers in the item's category. The fill is ordered by on-time percent times quality rating, then supplier id. That order is S003, S002, S005 for aluminium (example 10, no Active history) and S017, S019, S018 after Vertex for the encoder. If fewer than three or five Active suppliers exist, only those that exist are suggested. The encoder RFP names four because only four Active electrical suppliers exist.

Reference price is the supplier's latest PO for the item in the 548 days. If they have none, it is the rate on their most recent contract for the item.

A spot or emergency rule does not fire when it needs a preferred supplier and the list is empty. The request falls through to the next rule.

## Purchase orders

PO routes are combined when the supplier, plant and route match and the later requirement starts within 24 hours of the first requirement already on that PO. The window is inclusive. Sourcing requests are not combined. Numbers are `PO-0001` upward, in the order requirements open a new PO.

A quantity of zero is not a requirement. `PR-26-00172` asks for 0 cutting wheels and is `NOT_PROCESSED`. Leaving it in would open an empty PO and shift every later number.

`PR-26-00174` and `PR-26-00175` are the earliest lines in the batch: two servo drives at Pune, four minutes apart, same need-by. They club. The value is about ₹1,31,676, there is purchase history, and there is no contract, so the route is a spot PO. That PO is `PO-0001`. The worked examples print `PO-0001` on the hydraulic-oil buy, which starts half an hour later, and every later example number is one lower than this run. The gaps between those examples are the same either way: the two hex-bolt requirements still share one PO, and the hose PO is still the one just before the oil-seal PO. The engine keeps the servo PO. Dropping it would be ignoring two real lines in order to copy the pamphlet's numbering.

## Policy changes

Rules, limits and windows live in a versioned JSON document. An amendment can change a number, or insert a rule that uses facts the engine already computes: item type, category, plant, department, value, urgency, whether a contract is usable, and whether an Active supplier bought the item in the history window. Department, when a rule looks at it, is the department on the requirement's first line.

A rule that asks for a spot or emergency PO still needs a preferred supplier. The preview says so.

The amendment's effective date is mandatory. Lines earlier than that date stay on the previous version.

## What one batch means

`POST /api/process-batch` clubs and values only the lines in the uploaded file. Chat requests are a separate batch. Reprocessing a batch replaces its decisions. An override changes that requirement, keeps the original decision, requires a comment, and does not re-club the batch.

## Who is acting

Sign-in is its own page. The person chooses a name and enters a password. The role is stored with the person, not sent by the form. A session with only a name is not signed in. A. Kulkarni uses `bhosari-stores`, M. Shaikh uses `chakan-line`, and R. Deshmukh uses `buyer-desk`. Sign out clears the session. A successful sign-in writes a new one and keeps that person's chat. Approvals, overrides, policy activation, added contracts, sign-ins, and refused sign-ins record the name and the time. A requester cannot approve, run a batch, or read the audit log. The person who raised a requirement cannot release it.

## Money

All figures are INR excluding GST. Nothing in the app adds tax.
