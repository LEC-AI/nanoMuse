# Host-controlled computer service

This GPL-3.0-or-later fork exposes the actual ComputerLink through a separate authenticated HTTP service. Sandy owns tasks, permissions, and approvals. No MuseAgent, Sentinel, model, memory store or scheduler is started.

Install nanoMuse and FastAPI/uvicorn in an isolated Python environment. Set SANDY_NATIVE_TOKEN to a random secret of at least 32 characters. Run `uvicorn nanomuse.integrations.sandy:create_app --factory --host 127.0.0.1 --port 18212`. Grant OS accessibility and screen capture access only on the intended device. Configure the host device URL and token to match. Non-screenshot input requires an Idempotency-Key supplied by the host's durable operation journal.

Receipts indicate input execution, not delivery to a recipient. The host must inspect the resulting screen. Lost outcomes remain uncertain and require owner reconciliation. The in-memory replay cache supplements, but does not replace, the host's durable journal. This service has not been validated against a live WeChat account.
