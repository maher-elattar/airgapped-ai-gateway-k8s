from __future__ import annotations

from pathlib import Path

from airgap_ai_gateway.cli import main
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.renderer import render_manifests


def test_render_manifests_use_fake_runtime_placeholders_only() -> None:
    config = load_config(Path("examples/config"))
    rendered = render_manifests(config)
    combined = "\n".join(rendered.values())

    assert "REPLACE_AT_RUNTIME" in combined
    assert "example-only-do-not-use" in combined
    assert "sk-" not in combined
    assert "kind: Secret" not in combined
    assert "stringData" not in combined


def test_cli_render_writes_fake_only_output(tmp_path: Path) -> None:
    code = main(["--config", "examples/config", "render", "--output-dir", str(tmp_path)])

    assert code == 0
    rendered_files = sorted(tmp_path.glob("*.yaml"))
    assert [path.name for path in rendered_files] == [
        "00-platform-contract.yaml",
        "10-model-contract.yaml",
        "20-consumer-contract.yaml",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in rendered_files)
    assert "REPLACE_AT_RUNTIME" in combined
    assert "sk-" not in combined
