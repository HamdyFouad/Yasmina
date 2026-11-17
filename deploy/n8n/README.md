# n8n RFQ → supplier quote relay

`rfq-supplier-workflow.json` is an importable n8n workflow for the Yasmina
sourcing loop. It receives the signed RFQ envelope, verifies the HMAC, builds a
supplier quote from the tender positions, and posts the signed callback to
Yasmina.

## Required configuration

| Value | Purpose |
|---|---|
| `YASMINA_N8N_HMAC_SECRET` | Must equal the API's `N8N_WEBHOOK_HMAC_SECRET`. |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` | The Code node reads `$env.YASMINA_N8N_HMAC_SECRET`; alternatively replace it with an n8n credential. |
| `N8N_WEBHOOK_BASE_URL` (Yasmina) | Public n8n base URL, for example `https://n8n.example.com`. |
| `N8N_RFQ_WEBHOOK_PATH` (Yasmina) | Defaults to `/webhook/yasmina/rfq`. |
| `N8N_CALLBACK_URL` (Yasmina) | Absolute URL of `/api/v1/integrations/n8n/supplier-quotes`. |

## Cryptographic contract

Yasmina signs every outbound RFQ with:

```text
sha256 = HMAC-SHA256(secret, "<unix-timestamp>." + raw_body)
```

The headers are `X-Yasmina-Timestamp`, `X-Yasmina-Signature` and
`Idempotency-Key`. Replays outside `N8N_CALLBACK_MAX_AGE_SECONDS` are rejected.
The workflow verifies the signature against the raw request body before parsing
it, then signs the callback body with the same contract and a fresh timestamp.

## Import

1. Open n8n → **Workflows** → **Import from File**.
2. Select `rfq-supplier-workflow.json`.
3. Configure the environment value or credential referenced by the Code node.
4. Activate the workflow and point Yasmina's `N8N_WEBHOOK_BASE_URL` at the n8n
   instance.
5. Replace the fixed `unitPrice` in **Verify HMAC & Build Quote** with the real
   supplier quote extraction/mapping logic. The quote `tender_item_id` values
   must remain the IDs supplied in the RFQ envelope.

## Local E2E relay

CI does not run the real n8n workflow. It starts
`frontend/tests/e2e/mock-n8n-server.mjs`, which implements the same HMAC
verification, callback signing and quote mapping. This proves the cryptographic
contract in `tender-to-export.spec.ts` and the pending-TTL behavior in
`rfq-pending.spec.ts`.
