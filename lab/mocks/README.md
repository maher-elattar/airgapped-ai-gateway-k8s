# Mock OpenAI-Compatible Services

The disposable gateway lab runs one repository-owned image behind three backend
Services:

- Qwen chat mock.
- Gemma chat mock.
- Embedding mock.

The image implements only the OpenAI-compatible surface the gateway tests
exercise. It exists to validate policy, so it needs no GPUs, no model weights,
and no NVIDIA NIM images.
