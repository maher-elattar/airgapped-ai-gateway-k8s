# Disposable Gateway Lab

This is the local proof path. It gives the repository a real gateway, real
Kubernetes reconciliation, real HTTP requests, and fake model backends that are
safe to run on a laptop.

![Direct internal test path](assets/diagrams/article/11-direct-internal-test-path.png)

## What it creates

- A uniquely named kind cluster.
- A uniquely named local registry.
- Three repository-owned OpenAI-compatible mock model Services.
- Runtime-only fake credentials under the ignored run directory.
- Gateway API and agentgateway resources for the tested compatibility set.
- JSON, JUnit, and Markdown evidence.

No NVIDIA GPUs, model weights, or real NIM images are needed.

## Model fixtures

The mock image serves:

- Qwen-style chat completion responses.
- Gemma-style chat completion responses.
- Embedding responses with a deterministic non-empty vector.

The mocks exist to prove gateway behavior, not model quality. The deterministic
embedding vector is the part that matters: it lets the test distinguish a real
embedding response from any handler that happens to return 200.

## Request matrix

![Policy test matrix](assets/diagrams/article/12-policy-test-matrix.png)

The lab verifies:

- Missing key returns 401.
- Unknown key returns 401.
- Allowed Qwen consumer returns 200.
- Denied Qwen consumer returns 403.
- Allowed Gemma consumer returns 200.
- Allowed embedding consumer returns 200 with vector length greater than zero.
- Denied embedding consumer returns 403.
- Low-limit repeated traffic reaches 429.
- Wrong Host returns 404.
- Broken backend produces route diagnostics or an expected upstream failure.
- Runtime images come from the local registry.
- Model Services survive gateway cleanup.

## Run it

```bash
make kind-test
```

Optional retained-edge pass:

```bash
python scripts/kind_e2e_lab.py run --with-nginx
```

Evidence lands under `runs/kind-e2e-*/evidence/`, which is outside version
control. Example outputs are kept in [lab/samples](../lab/samples).

## Safety boundary

The lab only tears down clusters whose names start with `agw-e2e-`, and every
kubectl operation verifies the exact generated kind context before it runs. Those
two guards exist because a test harness that can delete clusters should never be
able to reach one you care about.

Runtime credentials are fake and generated per run. They are never stored in
repository source.

## Air-gap simulation gate

After connected preparation, the lab proves that runtime workload images point at
the local registry and checks the gateway namespace for unexpected public pulls.

The proof is registry-based because that is the approach that survives contact
with a multi-node cluster.
