# Corvo PR to PO

Chat-based purchase requests for Corvo Engineering. One Python process matches items, clubs requirements, values them, applies the approved policy, suggests suppliers, and consolidates purchase orders. A buyer can approve or override the result, change the policy, and add a contract from a PDF.

Open the app at https://procurement-manager-production.up.railway.app/

## Run locally

`docker compose up` builds the same image and starts it on port 8000. The site to use is the address above.

The image already contains the item master, suppliers, plants, purchase history, contracts, policy 5.0, and the sample batch. Sign in on the first page. The role stays with the person.

| Name | Role | Password |
| --- | --- | --- |
| A. Kulkarni | Requester, Stores | `bhosari-stores` |
| M. Shaikh | Requester, Production | `chakan-line` |
| R. Deshmukh | Buyer, Procurement | `buyer-desk` |

A requester raises a requisition in chat. A buyer runs a batch, approves or overrides a requirement he did not raise, approves a policy, adds a contract, and reads the audit log. The session lasts eight hours. Outside local development the process refuses the default `corvo-dev` secret.

To run without Docker, from this directory:

```bash
pip install -r app/requirements.txt
set PYTHONPATH=app/src
set CORVO_DB=app/seed/corvo.db
set CORVO_SECRET=corvo-dev
set CORVO_ENV=local
python -m uvicorn corvo.main:app --host 127.0.0.1 --port 8000
```

On macOS or Linux, use `export` instead of `set`.

## What you can do

- **Chat.** A requester types the item, quantity, plant, and need-by date. The assistant asks for anything missing, including an estimate for an unknown item, and creates the requisition only after confirmation.
- **Batch.** A buyer uploads a purchase-request CSV in the sample format. The queue shows each requirement, the route, the value, the supplier or suggestions, and the clause used.
- **Approve and override.** Approve releases the decision the engine already made. Override needs a written comment and keeps that decision.
- **Policy.** A buyer uploads an amendment or describes it in chat. The screen shows what will change. Nothing is active until the buyer approves it, and it then applies from its effective date.
- **Contracts.** The register is loaded, including the two contracts that exist only as PDFs. A buyer can add another by uploading its PDF.
- **Preferred suppliers.** Every item is ranked from purchase history and contracts, with the reason on the page. The CSV is the top three as of a chosen date.

## API

Both calls require a signed-in buyer.

- `POST /api/process-batch` accepts the purchase-request CSV as the raw body, or as a multipart file named `file`. Add `?format=csv` to download the decisions. The columns are `pr_no`, `pr_line`, `resolved_item_code`, `group_id`, `po_id`, `decision`, `route`, `supplier_id`, `unit_price`, `group_value_inr`, `suggested_supplier_ids`, `policy_version`, and `reason`.
- `GET /api/preferred-suppliers.csv?as_of=YYYY-MM-DD` returns `item_code`, `rank`, `supplier_id`, and `reference_price`.

A missing CSV column returns an error and no decision file. The route is chosen by the engine, not by the request.

## Layout

- `app/src/corvo` — FastAPI app, chat, and the decision engine. The engine does not import the web framework or the database.
- `app/templates` and `app/static` — the pages.
- `app/seed/corvo.db` — the seeded SQLite database shipped with the app.
- `Dockerfile` and `docker-compose.yml` — the local and hosted run.
- `DECISIONS.md` — where the policy is silent, what was decided, and why.
- `APPROACH.md` — how the app was built and which tools were used.
