# Kind End-to-End Lab Report

- Cluster: `agw-e2e-sample`
- Context: `kind-agw-e2e-sample`
- Registry: `localhost:5001`
- Status: `passed`

## Results

- PASS: internal-gateway: missing key returns 401 — expected=401 actual=401
- PASS: internal-gateway: allowed embedding consumer returns 200 with vector — expected=200 actual=200
- PASS: internal-gateway: repeated traffic reaches 429 — statuses=[200, 200, 429]

## Image audit

```json
{
  "publicImages": [],
  "status": "passed"
}
```
