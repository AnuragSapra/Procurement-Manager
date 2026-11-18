# Approach

The app was written in Cursor, with Grok as the coding assistant. Item matching, policy reading and the purchase decision are ordinary Python. A small intent model, trained in this repo and stored as weights, only decides whether a sentence is conversation, a greeting, a request to clear the chat, or a requisition. It does not choose an item code, a price, or a supplier, and it does not call an external service. A reviewer can run the blind test with no API key.

## Why one process

FastAPI serves the pages and the API. SQLite is the database. The decision engine is a pure function of the batch, the masters, the contracts and the approved policies, so the worked examples are tested without the browser. Docker Compose starts that one process. There is no separate frontend build and no ORM.

## What is data

Policy v5.0 is a JSON document: windows, limits, ranking weights, and the ordered rules. The engine interprets the document. A later amendment is another version of the same document, approved in the app, effective from the date written on it. Adding a plant-specific rule does not require a code change, because plant, category, value and the other facts are already computed for every requirement.

The ranking weights are in that document too. They were fitted to the worked lists and then frozen. The reason for each weight is in `DECISIONS.md`.

## What was checked

The 14 worked decisions are assertions on the sample batch, including club membership, route, supplier, rounded value, suggestion order and the PO numbers those examples name. The three preferred lists are asserted as of 1 November 2026. The contract parser is asserted by reading the ten signed PDFs back to the register, then reading RC-2026-031 and RC-2026-033, which are not in the register.

The PDF text extractor is local. The contracts are Chromium PDFs with subset fonts, not scans. No external PDF service is used.
