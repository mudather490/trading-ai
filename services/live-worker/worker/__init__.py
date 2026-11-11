"""Trading Command Center — live worker.

The live engine. Runs on an always-on host (GCP free e2-micro), holds the
market-data connections, builds bars, evaluates strategy rules and writes the
result to Supabase.

Custody rule (H3, I-1): broker and data credentials live HERE and only here.
Vercel never receives a venue secret.

Design rules:
  * The worker is the ONLY writer of market data, signals, news and heartbeat.
  * It writes only through the service-role key.
  * Every AI call is a sidecar: logged, schema-validated, and never able to
    influence sizing, promotion or execution.
"""

__version__ = "0.1.0"
