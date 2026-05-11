# bridge-ri

A2A v0.3 ↔ v1.0 protocol bridge for the `resource-intelligence` (ri_v10) agent.

`ri_v10` accepts only A2A v0.3 JSON-RPC (`method: "message/send"`, `parts[].kind: "text"`)
while orchestrator's a2a-sdk 1.0 client sends `method: "SendMessage"`. This bridge:

- Exposes A2A v1.0 endpoints (`POST /` + `GET /.well-known/agent-card.json`) to orchestrator
- Internally calls `ri_v10` at `RI_UPSTREAM_URL` (default
  `http://resource-intelligence.resource-intelligence.svc:443/a2a`) with v0.3 wire format
- Optional Bearer token auth (`RI_BRIDGE_A2A_TOKEN`)

`ri_v10` code and k8s manifests are NOT modified.
