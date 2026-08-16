from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from airgap_ai_gateway.cli import main
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.errors import SafetyError
from airgap_ai_gateway.lifecycle import (
    LifecyclePlan,
    apply_lifecycle_plan,
    build_consumer_plan,
    build_model_add_plan,
    model_from_request,
)

CONFIG_PATH = Path("examples/config")
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"


def test_model_add_plan_writes_config_manifests_policy_and_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _copy_source_tree(tmp_path)
    monkeypatch.chdir(repo)
    model = model_from_request(
        key="falcon-chat",
        display_name="Falcon Chat",
        kind="chat",
        host="falcon-chat.ai.example.internal",
        route_path="/v1/falcon/chat/completions",
        permission="model:falcon-chat:invoke",
        backend="agentgateway-backend",
        service_name="falcon-chat-nim",
        service_namespace="ai-gateway",
        service_port=8000,
    )

    plan = build_model_add_plan(
        config_path=CONFIG_PATH,
        model=model,
        grant_consumer_key="internal-chat",
    )
    assert plan.action == "model add"
    assert plan.plan_id == LifecyclePlan.from_dict(plan.to_dict()).with_computed_id().plan_id
    assert "falcon-chat" in plan.to_json()
    assert "sk-" not in plan.to_json()

    report = apply_lifecycle_plan(plan, repo_root=repo, config_path=CONFIG_PATH)

    assert report["status"] == "applied"
    updated = load_config(CONFIG_PATH)
    assert any(item.key == "falcon-chat" for item in updated.models)
    consumer = next(item for item in updated.consumers if item.key == "internal-chat")
    assert "falcon-chat" in consumer.allowed_models
    assert (repo / "manifests/baseline-v1.3.1/bases/routes/falcon-chat.yaml").exists()
    assert (repo / "manifests/baseline-v1.3.1/bases/policies/falcon-chat-policy.yaml").exists()
    ratelimit = (repo / "manifests/baseline-v1.3.1/bases/ratelimit/configmaps.yaml").read_text(
        encoding="utf-8"
    )
    assert "value: falcon-chat" in ratelimit


def test_consumer_add_rotate_and_revoke_apply_source_plans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _copy_source_tree(tmp_path)
    monkeypatch.chdir(repo)

    add_plan = build_consumer_plan(
        config_path=CONFIG_PATH,
        action="consumer add",
        consumer_key="search-app",
        display_name="Search App",
        allowed_models=("qwen-chat", "gemma-chat"),
        requests_per_minute=7,
    )
    assert add_plan.plan_id
    assert "REPLACE_AT_RUNTIME" in add_plan.to_json()
    apply_lifecycle_plan(add_plan, repo_root=repo, config_path=CONFIG_PATH)

    added = load_config(CONFIG_PATH)
    search = next(item for item in added.consumers if item.key == "search-app")
    assert search.allowed_models == ("qwen-chat", "gemma-chat")
    ratelimit = (repo / "manifests/baseline-v1.3.1/bases/ratelimit/configmaps.yaml").read_text(
        encoding="utf-8"
    )
    assert "value: search-app" in ratelimit
    assert "value: qwen-chat" in ratelimit
    assert "value: gemma-chat" in ratelimit

    rotate_plan = build_consumer_plan(
        config_path=CONFIG_PATH,
        action="consumer rotate",
        consumer_key="search-app",
    )
    apply_lifecycle_plan(rotate_plan, repo_root=repo, config_path=CONFIG_PATH)
    rotated = load_config(CONFIG_PATH)
    assert (
        next(item for item in rotated.consumers if item.key == "search-app").credential_placeholder
        == "REPLACE_AT_RUNTIME_ROTATED"
    )

    revoke_plan = build_consumer_plan(
        config_path=CONFIG_PATH,
        action="consumer revoke",
        consumer_key="search-app",
    )
    apply_lifecycle_plan(revoke_plan, repo_root=repo, config_path=CONFIG_PATH)
    revoked = load_config(CONFIG_PATH)
    search = next(item for item in revoked.consumers if item.key == "search-app")
    assert search.allowed_models == ()
    assert search.credential_placeholder == "REVOKED_AT_RUNTIME"


def test_lifecycle_apply_refuses_stale_source_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _copy_source_tree(tmp_path)
    monkeypatch.chdir(repo)
    plan = build_consumer_plan(
        config_path=CONFIG_PATH,
        action="consumer add",
        consumer_key="stale-app",
        allowed_models=("qwen-chat",),
    )
    consumers_file = repo / "examples/config/consumers.yaml"
    consumers_file.write_text(
        consumers_file.read_text(encoding="utf-8") + "\n# unrelated edit\n",
        encoding="utf-8",
    )

    with pytest.raises(SafetyError, match="source changed after plan review"):
        apply_lifecycle_plan(plan, repo_root=repo, config_path=CONFIG_PATH)


def test_cli_consumer_lifecycle_plan_and_apply_updates_source_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _copy_source_tree(tmp_path)
    monkeypatch.chdir(repo)
    output_dir = Path("plans/consumer-add")

    plan_code = main(
        [
            "--config",
            str(CONFIG_PATH),
            "consumer",
            "add",
            "plan",
            "--consumer-key",
            "cli-apply-consumer",
            "--display-name",
            "CLI Apply Consumer",
            "--allowed-model",
            "qwen-chat",
            "--output-dir",
            str(output_dir),
        ]
    )
    apply_code = main(
        [
            "--config",
            str(CONFIG_PATH),
            "consumer",
            "add",
            "apply",
            "--plan-file",
            str(output_dir / "plan.json"),
            "--confirm",
            CONFIRMATION,
        ]
    )

    assert plan_code == 0
    assert apply_code == 0
    updated = load_config(CONFIG_PATH)
    assert any(item.key == "cli-apply-consumer" for item in updated.consumers)


def _copy_source_tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(Path("examples"), repo / "examples")
    shutil.copytree(Path("manifests"), repo / "manifests")
    return repo
