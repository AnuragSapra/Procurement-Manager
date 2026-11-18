# Approach

This is what the repository contains and how it was built. Procurement decisions are deterministic. A language model does not choose a route, supplier, contract, or price.

## Applications

One Python process serves the pages and the API. FastAPI renders Jinja templates in `app/templates`, with the stylesheet and script in `app/static`. There is no separate frontend build.

SQLite is the database. The hosted service starts from the seeded file in `app/seed/corvo.db`, which already holds the item master, suppliers, plants, purchase history, contracts, policy 5.0, and the sample batch. `CORVO_DB` points at that file. Outside local development the process refuses the default session secret.

`app/src/corvo/engine.py`, `match.py`, `policy.py`, and `ranking.py` match lines, club them, value them, apply the policy, rank suppliers, and consolidate purchase orders. Those modules do not import FastAPI or SQLite. `main.py` loads the database, checks the signed-in person, and calls the engine. The pages render those results and do not contain procurement rules.

Docker Compose starts the same process. The public site is that image.

## Decision pipeline

`process` runs one purchase-request batch in this order:

1. Match each line to the item master. A known item code wins. Otherwise the description is matched. One match is resolved. No match is a new item. A plant outside the plant master, a quantity of zero, or a new item with no estimated price is not processed.
2. Club resolved lines that share an item and a plant inside the policy windows. A line joins the earliest fitting requirement. New items are never clubbed. Any Emergency line makes that requirement an emergency.
3. Find applicable contracts, test each ceiling at that contract's own rate, and select the cheapest usable contract. If every applicable contract is over the ceiling, the valuation price is the lowest applicable rate and the contract is not usable.
4. Set the valuation price from the order in `DECISIONS.md`, then the requirement value, rounded half up to the rupee. The limits use that rounded value.
5. Select the approved policy version effective on the requirement date. When two approved versions share that date, the higher version number wins. Its rules run in order. The first match wins.
6. Rank suppliers with the weighted score in `DECISIONS.md`. Blocked and Inactive suppliers are excluded. Suggestions continue with other Active suppliers in the item's category when the preferred list is short.
7. Consolidate purchase-order routes that share a supplier, plant, and route within 24 hours. Sourcing requests stay separate.

The valuation price and the purchase-order unit price are stored separately. A contract purchase order uses the contract rate. An emergency or spot order uses the rank-1 preferred supplier's reference price.

Money is INR excluding GST. A batch copies each contract's opening consumed amount into memory and does not write it back, so a second run of the same file starts from the same figures.

## Policy as data

Policy 5.0 is a versioned document: version, effective date, status, windows, limits, ranking weights, and ordered rules. The engine reads that document. It does not execute it as code.

A buyer can paste an amendment or upload it as text. The Policy screen shows what will change and leaves it inactive. Approval stores a new version from the effective date on the amendment. It does not edit version 5.0. The same sort of change can be described in chat, which saves a preview and sends the buyer to that screen. A requester cannot approve a policy.

A new rule can name facts the engine already computes, including plant, category, value, and urgency, without a code change. The ranking weights live in the same document. The reason for each weight is in `DECISIONS.md`.

## Employee chat

Chat collects one request: the item, the quantity, the plant, the need-by date, and, for a new item, an estimated unit price. It asks for one missing fact at a time and asks the person to confirm before the requisition is created.

The chat uses a local intent model whose weights ship with the app in `app/src/corvo/language_model.json`. It is a small logistic model over character and word features. It only classifies the sentence: a requisition, an answer to a question already asked, a greeting, thanks, goodbye, an apology, a request for help, or ordinary conversation. Catalogue matching, quantities, plants, dates, prices, and suppliers stay in the deterministic parser and the engine. The model does not call Grok, or any other external language-model service. The blind test needs no API key.

## Buyer workspace

The queue shows each requirement with its lines, the route, the value, the supplier and price or the suggested suppliers, and the clause, policy version, and contract or purchase order used. Approve releases that decision. Override requires a written comment and keeps the original decision beside it. Both actions record who did them and when. The person who raised a requirement cannot release it.

A buyer can also upload a purchase-request CSV, add a contract from a PDF, and download the preferred-supplier list. A requester can raise a requisition and read the queue. A requester cannot run a batch, approve a policy, add a contract, or open the audit log.

## Batch outputs

`POST /api/process-batch` accepts the purchase-request CSV and returns one decision row per input line. The columns are those named in the brief: `pr_no`, `pr_line`, `resolved_item_code`, `group_id`, `po_id`, `decision`, `route`, `supplier_id`, `unit_price`, `group_value_inr`, `suggested_supplier_ids`, `policy_version`, and `reason`. A line that is not processed uses route `NOT_PROCESSED` and gives the reason. Suggested suppliers are separated by semicolons.

`GET /api/preferred-suppliers.csv` takes `as_of` as `YYYY-MM-DD` and returns `item_code`, `rank`, `supplier_id`, and `reference_price` for the top three suppliers. An item with nobody eligible is left out of that file. The same batch can be downloaded as CSV from the queue.

## Verification

The engine was checked against the 14 worked decisions on the sample batch, including club membership, route, supplier, rounded value, suggestion order, and the purchase-order numbers those examples name. The three preferred lists were checked as of 1 November 2026. The ten signed PDFs were read back against the contract register. RC-2026-031 and RC-2026-033, which are not in the register, were read from the PDFs and start with a consumed amount of zero.

A second check processes a held-out batch under policy 5.0, then approves an amendment through the app and processes the same batch again. Contract text is extracted locally. No external PDF service is used.

## Tools used

AI tools were used to write the application, and one small model runs inside the chat.

**Coding.** The application was written in Cursor. Grok was the coding assistant: reading the policy and the worked examples, editing the engine, the pages, and these notes, and checking the results. No other model is claimed.

**Inside the app.** Chat classification uses the local intent model described above. Policy reading, item matching, valuation, ranking, and the purchase decision do not use a language model. The running app has no language-model API key and does not call one.
