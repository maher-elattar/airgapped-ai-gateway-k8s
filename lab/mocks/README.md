# Mock OpenAI Services

The disposable lab uses one repository-owned image for three backend services:

- Qwen chat mock.
- Gemma chat mock.
- Embedding mock.

The image implements the small OpenAI-compatible surface required by the
gateway tests. It does not require GPUs, model weights, or NVIDIA NIM images.
