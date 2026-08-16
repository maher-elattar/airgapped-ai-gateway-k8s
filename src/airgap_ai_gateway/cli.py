"""Command-line interface for the air-gapped AI gateway scaffold."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from airgap_ai_gateway.airgap_bundle import (
    DEFAULT_COMPATIBILITY_SET,
    DEFAULT_DIST_DIR,
    DEFAULT_LOCK_PATH,
    DEFAULT_PRIVATE_REGISTRY,
    DEFAULT_PROMOTION_TOOL,
    DEFAULT_RENDERED_OVERLAY,
    build_bundle,
    verify_bundle,
    verify_rendered_manifests_against_lock,
)
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.discovery import discover
from airgap_ai_gateway.errors import AirgapGatewayError
from airgap_ai_gateway.execution import SubprocessCommandRunner, execute_plan
from airgap_ai_gateway.ledger import PreChangeSnapshot, StateLedger
from airgap_ai_gateway.lifecycle import (
    LifecyclePlan,
    apply_lifecycle_plan,
    build_consumer_plan,
    build_model_add_plan,
    model_from_request,
)
from airgap_ai_gateway.models import ModelConfig
from airgap_ai_gateway.planning import (
    DEFAULT_OVERLAY,
    ExecutionPlan,
    build_execution_plan,
    build_plan,
    build_rollback_execution_plan,
    write_plan_files,
)
from airgap_ai_gateway.registry import apply_promotion_plan, promotion_plan
from airgap_ai_gateway.renderer import render_manifests, write_rendered_manifests
from airgap_ai_gateway.reporting import to_json
from airgap_ai_gateway.rollback import rollback_plan
from airgap_ai_gateway.safety import ensure_mutation_is_confirmed
from airgap_ai_gateway.snapshot import capture_snapshot
from airgap_ai_gateway.verification import run_runtime_verification, verification_plan

DEFAULT_CONFIG = Path("examples/config")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with every planned command."""

    parser = argparse.ArgumentParser(
        prog="airgap-ai-gateway",
        description="Offline-first scaffold for an air-gapped Kubernetes AI gateway platform.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Configuration file or directory. Default: {DEFAULT_CONFIG}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    _add_simple_command(
        subcommands, "discover", "Inspect configured inputs without cluster access."
    )
    subcommands.choices["discover"].set_defaults(
        handler=_handle_discover,
        action="discover",
        mutating=False,
    )

    render = subcommands.add_parser("render", help="Render fake-only scaffold manifests offline.")
    render.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. Without it, the command prints a JSON file list only.",
    )
    render.set_defaults(handler=_handle_render, action="render", mutating=False)

    bundle = subcommands.add_parser("bundle", help="Build or verify an offline dependency bundle.")
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)
    bundle_build = bundle_sub.add_parser(
        "build", help="Build deterministic connected-side bundle audit artifacts."
    )
    _add_bundle_lock_arguments(bundle_build)
    bundle_build.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help=f"Bundle output root excluded from Git. Default: {DEFAULT_DIST_DIR}",
    )
    bundle_build.add_argument(
        "--split-size-bytes",
        type=int,
        default=None,
        help="Optional transfer-media part size in bytes.",
    )
    bundle_build.add_argument(
        "--metadata-hook",
        action="append",
        default=[],
        help="Optional declared metadata hook such as sbom, signature, or malware-scan.",
    )
    bundle_build.add_argument(
        "--payload-mode",
        choices=("descriptor", "fetch"),
        default="descriptor",
        help="Use descriptor for fast audit bundles or fetch to export real payloads.",
    )
    bundle_build.set_defaults(
        handler=_handle_bundle_build,
        action="bundle build",
        mutating=False,
    )

    bundle_verify = bundle_sub.add_parser(
        "verify", help="Verify a disconnected-side bundle without network access."
    )
    _add_bundle_lock_arguments(bundle_verify)
    bundle_verify.add_argument(
        "--bundle-dir",
        type=Path,
        default=DEFAULT_DIST_DIR / DEFAULT_COMPATIBILITY_SET,
        help="Bundle directory containing inventory.json.",
    )
    bundle_verify.set_defaults(
        handler=_handle_bundle_verify,
        action="bundle verify",
        mutating=False,
    )

    snapshot = subcommands.add_parser("snapshot", help="Capture pre-change resource snapshots.")
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_create = snapshot_sub.add_parser(
        "create", help="Capture resources listed in an approved plan."
    )
    snapshot_create.add_argument("--plan-file", type=Path, required=True)
    snapshot_create.add_argument("--expected-context", required=True)
    snapshot_create.add_argument("--output-file", type=Path, required=True)
    snapshot_create.set_defaults(handler=_handle_snapshot_create, action="snapshot create")

    registry = subcommands.add_parser("registry", help="Promote images into a private registry.")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    promote = registry_sub.add_parser("promote", help="Plan or apply private registry promotion.")
    promote_sub = promote.add_subparsers(dest="promote_command", required=False)
    promote_plan = promote_sub.add_parser("plan", help="Plan image promotion.")
    _add_registry_promote_plan_args(promote_plan)
    promote_plan.set_defaults(
        handler=_handle_registry_promote,
        action="registry promote plan",
        promote_command="plan",
        mutating=False,
    )
    promote_apply = promote_sub.add_parser("apply", help="Apply an approved image promotion plan.")
    promote_apply.add_argument("--plan-file", type=Path, required=True)
    promote_apply.add_argument("--confirm", required=True)
    promote_apply.add_argument(
        "--commands-log",
        type=Path,
        default=None,
        help="Optional redacted commands log path.",
    )
    promote_apply.set_defaults(
        handler=_handle_registry_promote_apply,
        action="registry promote apply",
        mutating=True,
    )
    _add_registry_promote_plan_args(promote)
    promote.set_defaults(
        handler=_handle_registry_promote,
        action="registry promote plan",
        promote_command="plan",
        mutating=False,
    )

    def _verify_command(value: str) -> str:
        if value not in {"static", "runtime"}:
            msg = "verify command must be static or runtime"
            raise argparse.ArgumentTypeError(msg)
        return value

    verify = subcommands.add_parser("verify", help="Run static or runtime verification.")
    verify.add_argument("verify_command", nargs="?", type=_verify_command, default="static")
    verify.add_argument("--overlay", default=DEFAULT_RENDERED_OVERLAY)
    verify.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_PATH)
    verify.add_argument("--compatibility-set", default=DEFAULT_COMPATIBILITY_SET)
    verify.add_argument("--registry", default=DEFAULT_PRIVATE_REGISTRY)
    verify.add_argument("--expected-context", default=None)
    verify.add_argument("--gateway-url", default=None)
    verify.add_argument(
        "--credential",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Runtime-only test credential mapping.",
    )
    verify.add_argument("--wrong-host", default="wrong.ai.example.internal")
    verify.add_argument("--low-limit-consumer", default="testing-client")
    verify.add_argument("--insecure-skip-tls-verify", action="store_true")
    verify.set_defaults(handler=_handle_verify, action="verify", mutating=False)

    # Remove the old direct verify parser created by earlier scaffold phases.
    # The command above keeps the old no-subcommand form as static verification.

    deploy = subcommands.add_parser("deploy", help="Plan or apply gateway deployment.")
    deploy_sub = deploy.add_subparsers(dest="deploy_command", required=True)
    _add_execution_plan_command(deploy_sub, "plan", "Plan deployment.", "deploy apply")
    _add_mutating_command(
        deploy_sub, "apply", "Apply deployment after safety confirmation.", "deploy apply"
    )

    cutover = subcommands.add_parser("cutover", help="Plan or apply traffic cutover.")
    cutover_sub = cutover.add_subparsers(dest="cutover_command", required=True)
    _add_execution_plan_command(cutover_sub, "plan", "Plan cutover.", "cutover apply")
    _add_mutating_command(
        cutover_sub, "apply", "Apply cutover after safety confirmation.", "cutover apply"
    )

    rollback = subcommands.add_parser("rollback", help="Plan or apply rollback.")
    rollback_sub = rollback.add_subparsers(dest="rollback_command", required=True)
    rollback_plan_parser = rollback_sub.add_parser("plan", help="Plan rollback.")
    rollback_plan_parser.add_argument("--ledger-file", type=Path, default=None)
    rollback_plan_parser.add_argument("--run-id", default=None)
    rollback_plan_parser.add_argument(
        "--apply-mode",
        choices=("server-side-dry-run", "live"),
        default="server-side-dry-run",
    )
    rollback_plan_parser.add_argument("--output-dir", type=Path, default=None)
    rollback_plan_parser.set_defaults(
        handler=_handle_rollback_plan,
        action="rollback plan",
        mutating=False,
    )
    _add_mutating_command(
        rollback_sub,
        "apply",
        "Apply rollback after safety confirmation.",
        "rollback apply",
    )

    model = subcommands.add_parser("model", help="Model lifecycle commands.")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_add = model_sub.add_parser("add", help="Plan or apply adding a model.")
    model_add_sub = model_add.add_subparsers(dest="model_add_command", required=False)
    _add_model_add_plan_args(model_add)
    model_add.set_defaults(handler=_handle_model_add_legacy, action="model add", mutating=False)
    model_add_plan = model_add_sub.add_parser("plan", help="Plan adding a model.")
    _add_model_add_plan_args(model_add_plan)
    model_add_plan.set_defaults(handler=_handle_model_add_plan, action="model add")
    model_add_apply = model_add_sub.add_parser("apply", help="Apply an approved model add plan.")
    _add_lifecycle_apply_args(model_add_apply)
    model_add_apply.set_defaults(handler=_handle_lifecycle_apply, action="model add")

    consumer = subcommands.add_parser("consumer", help="Consumer lifecycle commands.")
    consumer_sub = consumer.add_subparsers(dest="consumer_command", required=True)
    for name in ("add", "rotate", "revoke"):
        item = consumer_sub.add_parser(name, help=f"Plan or apply consumer {name}.")
        item_sub = item.add_subparsers(dest="consumer_lifecycle_command", required=False)
        _add_consumer_plan_args(item, name)
        item.set_defaults(handler=_handle_consumer_legacy, action=f"consumer {name}")
        plan_item = item_sub.add_parser("plan", help=f"Plan consumer {name}.")
        _add_consumer_plan_args(plan_item, name)
        plan_item.set_defaults(handler=_handle_consumer_plan, action=f"consumer {name}")
        apply_item = item_sub.add_parser("apply", help=f"Apply approved consumer {name} plan.")
        _add_lifecycle_apply_args(apply_item)
        apply_item.set_defaults(handler=_handle_lifecycle_apply, action=f"consumer {name}")

    destroy = subcommands.add_parser("destroy", help="Plan or apply gateway decommissioning.")
    destroy_sub = destroy.add_subparsers(dest="destroy_command", required=True)
    _add_execution_plan_command(destroy_sub, "plan", "Plan decommissioning.", "destroy apply")
    _add_mutating_command(
        destroy_sub,
        "apply",
        "Apply decommissioning after safety confirmation.",
        "destroy apply",
    )
    return parser


def _add_registry_promote_plan_args(promote: argparse.ArgumentParser) -> None:
    _add_bundle_lock_arguments(promote)
    promote.add_argument(
        "--tool",
        choices=("skopeo", "docker"),
        default=DEFAULT_PROMOTION_TOOL,
        help="Preferred promotion command family.",
    )
    promote.add_argument(
        "--skip-existing-check",
        action="store_true",
        help="Omit destination existence checks from the plan.",
    )
    promote.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional path for the JSON promotion plan.",
    )


def _add_model_add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-key", default="example-model", help="Model key to plan.")
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--kind", choices=("chat", "embedding"), default="chat")
    parser.add_argument("--host", default=None)
    parser.add_argument("--route-path", default=None)
    parser.add_argument("--permission", default=None)
    parser.add_argument(
        "--backend",
        choices=("agentgateway-backend", "kubernetes-service"),
        default=None,
    )
    parser.add_argument("--service-name", default=None)
    parser.add_argument("--service-namespace", default=None)
    parser.add_argument("--service-port", type=int, default=8000)
    parser.add_argument("--service-port-name", default="http")
    parser.add_argument("--grant-consumer", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)


def _add_consumer_plan_args(parser: argparse.ArgumentParser, command: str) -> None:
    parser.add_argument("--consumer-key", default="example-consumer", help="Consumer key to plan.")
    if command == "add":
        parser.add_argument("--display-name", default=None)
        parser.add_argument(
            "--allowed-model",
            action="append",
            default=[],
            help="Allowed model key. Repeat for multiple models.",
        )
        parser.add_argument("--requests-per-minute", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)


def _add_lifecycle_apply_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--confirm", required=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    try:
        return handler(args)
    except AirgapGatewayError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def entrypoint() -> None:
    """Console-script entrypoint."""

    raise SystemExit(main())


def _add_simple_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    *,
    action: str | None = None,
) -> None:
    command = subcommands.add_parser(name, help=help_text)
    command.set_defaults(handler=_handle_plan, action=action or name, mutating=False)


def _add_mutating_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    action: str,
) -> None:
    command = subcommands.add_parser(name, help=help_text)
    command.add_argument(
        "--expected-context", default=None, help="Exact disposable context expected."
    )
    command.add_argument(
        "--confirm", default=None, help="Exact confirmation token from configuration."
    )
    command.add_argument(
        "--apply-mode",
        choices=("server-side-dry-run", "live"),
        default=None,
        help="Exact apply mode recorded in the saved plan.",
    )
    command.add_argument("--plan-file", type=Path, default=None, help="Approved plan JSON.")
    command.add_argument(
        "--snapshot-file",
        type=Path,
        default=None,
        help="Saved pre-change snapshot JSON.",
    )
    command.add_argument(
        "--commands-log",
        type=Path,
        default=None,
        help="Optional redacted commands log path.",
    )
    command.add_argument(
        "--ledger-file",
        type=Path,
        default=None,
        help="State ledger JSON. Required for rollback apply.",
    )
    command.set_defaults(handler=_handle_apply, action=action, mutating=True)


def _add_execution_plan_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    execution_command: str,
) -> None:
    command = subcommands.add_parser(name, help=help_text)
    command.add_argument("--overlay", default=DEFAULT_OVERLAY)
    command.add_argument(
        "--apply-mode",
        choices=("server-side-dry-run", "live"),
        default="server-side-dry-run",
    )
    command.add_argument("--skip-ratelimit", action="store_true")
    command.add_argument("--output-dir", type=Path, default=None)
    command.set_defaults(
        handler=_handle_execution_plan,
        action=execution_command,
        mutating=False,
    )


def _add_bundle_lock_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK_PATH,
        help=f"Immutable air-gap source lock. Default: {DEFAULT_LOCK_PATH}",
    )
    parser.add_argument(
        "--compatibility-set",
        default=DEFAULT_COMPATIBILITY_SET,
        help=f"Compatibility set to use. Default: {DEFAULT_COMPATIBILITY_SET}",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_PRIVATE_REGISTRY,
        help=f"Internal registry expected in promoted images. Default: {DEFAULT_PRIVATE_REGISTRY}",
    )


def _handle_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ensure_mutation_is_confirmed(
        action=args.action,
        config=config,
        expected_context=getattr(args, "expected_context", None),
        confirmation=getattr(args, "confirm", None),
    )
    print(to_json(build_plan(config, args.action, mutating=args.mutating)))
    return 0


def _handle_execution_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    plan = build_execution_plan(
        config,
        command=args.action,
        overlay=args.overlay,
        apply_mode=args.apply_mode,
        skip_ratelimit=args.skip_ratelimit,
    )
    if args.output_dir is None:
        print(plan.to_json(), end="")
        return 0
    json_path, markdown_path = write_plan_files(plan, args.output_dir)
    print(
        to_json(
            {
                "plan_id": plan.plan_id,
                "plan_json": str(json_path),
                "plan_markdown": str(markdown_path),
                "status": "planned",
            }
        )
    )
    return 0


def _handle_apply(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    _require_apply_argument(args.expected_context, "--expected-context")
    _require_apply_argument(args.confirm, "--confirm")
    _require_apply_argument(args.apply_mode, "--apply-mode")
    if args.plan_file is None:
        msg = f"{args.action} refused: pass --plan-file with the approved plan JSON."
        raise AirgapGatewayError(msg)
    if args.snapshot_file is None:
        msg = f"{args.action} refused: pass --snapshot-file with the saved pre-change snapshot."
        raise AirgapGatewayError(msg)
    ensure_mutation_is_confirmed(
        action=args.action,
        config=config,
        expected_context=args.expected_context,
        confirmation=args.confirm,
    )
    plan = ExecutionPlan.from_file(args.plan_file)
    if plan.command != args.action:
        msg = f"{args.action} refused: plan command is {plan.command!r}."
        raise AirgapGatewayError(msg)
    snapshot = PreChangeSnapshot.from_file(args.snapshot_file)
    ledger = None
    if args.ledger_file is not None:
        ledger = StateLedger.from_file(args.ledger_file)
    if args.action == "rollback apply" and ledger is None:
        msg = "rollback apply refused: pass --ledger-file with the saved state ledger."
        raise AirgapGatewayError(msg)
    report = execute_plan(
        plan=plan,
        config=config,
        runner=SubprocessCommandRunner(),
        expected_context=args.expected_context,
        apply_mode=args.apply_mode,
        confirmation=args.confirm,
        snapshot=snapshot,
        ledger=ledger,
        commands_log_path=args.commands_log,
    )
    print(to_json(report.to_dict()))
    return 0


def _handle_render(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.output_dir is None:
        print(to_json({"manifests": render_manifests(config), "status": "rendered-fake-only"}))
        return 0
    written = write_rendered_manifests(config, args.output_dir)
    print(to_json({"files": [str(path) for path in written], "status": "rendered-fake-only"}))
    return 0


def _handle_discover(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(to_json(discover(config)))
    return 0


def _handle_bundle_build(args: argparse.Namespace) -> int:
    print(
        to_json(
            build_bundle(
                lock_path=args.lock_file,
                compatibility_set=args.compatibility_set,
                output_dir=args.dist_dir,
                private_registry=args.registry,
                split_size_bytes=args.split_size_bytes,
                metadata_hooks=tuple(args.metadata_hook),
                payload_mode=args.payload_mode,
            )
        )
    )
    return 0


def _handle_bundle_verify(args: argparse.Namespace) -> int:
    print(
        to_json(
            verify_bundle(
                bundle_dir=args.bundle_dir,
                lock_path=args.lock_file,
                compatibility_set=args.compatibility_set,
                private_registry=args.registry,
            )
        )
    )
    return 0


def _handle_registry_promote(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(
        to_json(
            promotion_plan(
                config,
                lock_file=args.lock_file,
                compatibility_set=args.compatibility_set,
                private_registry=args.registry,
                check_existing=not args.skip_existing_check,
                tool=args.tool,
                output_file=args.output_file,
            )
        )
    )
    return 0


def _handle_snapshot_create(args: argparse.Namespace) -> int:
    plan = ExecutionPlan.from_file(args.plan_file)
    report = capture_snapshot(
        plan=plan,
        runner=SubprocessCommandRunner(),
        expected_context=args.expected_context,
        output_file=args.output_file,
    )
    print(to_json(report.to_dict()))
    return 0


def _handle_registry_promote_apply(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = apply_promotion_plan(
        plan_file=args.plan_file,
        runner=SubprocessCommandRunner(),
        confirmation=args.confirm,
        expected_confirmation=config.platform.confirmation_token,
        commands_log_path=args.commands_log,
    )
    print(to_json(report))
    return 0


def _handle_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.verify_command == "runtime":
        _require_apply_argument(args.expected_context, "--expected-context")
        if not isinstance(args.gateway_url, str) or not args.gateway_url:
            msg = "runtime verification refused: pass --gateway-url."
            raise AirgapGatewayError(msg)
        report = run_runtime_verification(
            config,
            runner=SubprocessCommandRunner(),
            expected_context=args.expected_context,
            gateway_url=args.gateway_url,
            credentials=_parse_credentials(args.credential),
            wrong_host=args.wrong_host,
            low_limit_consumer=args.low_limit_consumer,
            verify_tls=not args.insecure_skip_tls_verify,
        )
    else:
        report = verification_plan(config)
        report["rendered_manifest_images"] = verify_rendered_manifests_against_lock(
            lock_path=args.lock_file,
            compatibility_set=args.compatibility_set,
            overlay=args.overlay,
            private_registry=args.registry,
        )
    print(to_json(report))
    return 0


def _handle_rollback_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.ledger_file is None or args.run_id is None:
        print(to_json(rollback_plan(config)))
        return 0
    plan = build_rollback_execution_plan(
        config,
        ledger=StateLedger.from_file(args.ledger_file),
        run_id=args.run_id,
        apply_mode=args.apply_mode,
    )
    if args.output_dir is None:
        print(plan.to_json(), end="")
        return 0
    json_path, markdown_path = write_plan_files(plan, args.output_dir)
    print(
        to_json(
            {
                "plan_id": plan.plan_id,
                "plan_json": str(json_path),
                "plan_markdown": str(markdown_path),
                "status": "planned",
            }
        )
    )
    return 0


def _handle_model_add_legacy(args: argparse.Namespace) -> int:
    return _handle_model_add_plan(args)


def _handle_model_add_plan(args: argparse.Namespace) -> int:
    plan = build_model_add_plan(
        config_path=args.config,
        model=_model_from_args(args),
        grant_consumer_key=args.grant_consumer,
    )
    _emit_lifecycle_plan(plan, args.output_dir)
    return 0


def _handle_consumer_legacy(args: argparse.Namespace) -> int:
    return _handle_consumer_plan(args)


def _handle_consumer_plan(args: argparse.Namespace) -> int:
    plan = build_consumer_plan(
        config_path=args.config,
        action=args.action,
        consumer_key=args.consumer_key,
        display_name=getattr(args, "display_name", None),
        allowed_models=tuple(getattr(args, "allowed_model", ())),
        requests_per_minute=getattr(args, "requests_per_minute", None),
    )
    _emit_lifecycle_plan(plan, getattr(args, "output_dir", None))
    return 0


def _handle_lifecycle_apply(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.confirm != config.platform.confirmation_token:
        msg = f"{args.action} apply refused: confirmation token does not match configuration."
        raise AirgapGatewayError(msg)
    plan = LifecyclePlan.from_file(args.plan_file)
    if plan.action != args.action:
        msg = f"{args.action} apply refused: plan action is {plan.action!r}."
        raise AirgapGatewayError(msg)
    report = apply_lifecycle_plan(plan, repo_root=Path.cwd(), config_path=args.config)
    print(to_json(report))
    return 0


def _require_apply_argument(value: object, flag: str) -> None:
    if not isinstance(value, str) or not value:
        msg = f"apply command refused: pass {flag}."
        raise AirgapGatewayError(msg)


def _emit_lifecycle_plan(plan: LifecyclePlan, output_dir: Path | None) -> None:
    if output_dir is None:
        print(plan.to_json(), end="")
        return
    json_path, markdown_path = plan.write(output_dir)
    print(
        to_json(
            {
                "plan_id": plan.plan_id,
                "plan_json": str(json_path),
                "plan_markdown": str(markdown_path),
                "status": "planned",
            }
        )
    )


def _model_from_args(args: argparse.Namespace) -> ModelConfig:
    key = args.model_key
    kind = args.kind
    backend = args.backend
    if backend is None:
        backend = "agentgateway-backend" if kind == "chat" else "kubernetes-service"
    route_path = args.route_path
    if route_path is None:
        route_path = "/v1/chat/completions" if kind == "chat" else "/v1/embeddings"
    return model_from_request(
        key=key,
        display_name=args.display_name or key.replace("-", " ").title(),
        kind=kind,
        host=args.host or f"{key}.ai.example.internal",
        route_path=route_path,
        permission=args.permission or f"model:{key}:invoke",
        backend=backend,
        service_name=args.service_name or f"{key}-service",
        service_namespace=args.service_namespace or "ai-gateway",
        service_port=args.service_port,
        service_port_name=args.service_port_name,
    )


def _parse_credentials(raw_values: Sequence[str]) -> dict[str, str]:
    credentials: dict[str, str] = {}
    for raw in raw_values:
        if "=" not in raw:
            msg = "credential values must use KEY=VALUE"
            raise AirgapGatewayError(msg)
        key, value = raw.split("=", 1)
        if not key or not value:
            msg = "credential values must use non-empty KEY=VALUE pairs"
            raise AirgapGatewayError(msg)
        credentials[key] = value
    return credentials


if __name__ == "__main__":
    entrypoint()
