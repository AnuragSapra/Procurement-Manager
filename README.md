# Corvo PR to PO

One process. It reads the kit CSVs and contract PDFs, applies procurement policy v5.0, and serves the chat, the batch upload, the buyer queue, policy changes, contracts, and preferred-supplier lists.

```bash
docker compose up
```

Then open http://localhost:8000.

Sign in at http://localhost:8000. Each person has a password. A. Kulkarni (`bhosari-stores`) and M. Shaikh (`chakan-line`) raise requisitions. R. Deshmukh (`buyer-desk`) is the buyer and can approve a requirement he did not raise, approve a policy, add a contract, or run a batch. A person cannot release a requirement they raised. Sign out is in the header. The session lasts eight hours. Outside local development the process refuses the default `corvo-dev` secret.

```bash
python -m pytest
```

`POST /api/process-batch` accepts the sample CSV and returns one decision per line. `GET /api/preferred-suppliers.csv?as_of=2026-11-01` returns the top three suppliers per item.

Deleting the SQLite file and starting again loads the masters, both PDF-only contracts, policy 5.0, and the sample batch.
