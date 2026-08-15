"""Command-line interface for the air-gapped AI gateway scaffold."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.discovery import discover
from airgap_ai_gateway.errors import AirgapGatewayError
from airgap_ai_gateway.execution import SubprocessCommandRunner, execute_plan
from airgap_ai_gateway.ledger import PreChangeSnapshot, StateLedger
from airgap_ai_gateway.models import ModelKind
from airgap_ai_gateway.onboarding import render_chat_model_onboarding
from airgap_ai_gateway.planning import (
    DEFAULT_OVERLAY,
    ExecutionPlan,
    build_execution_plan,
    build_plan,
    build_rollback_execution_plan,
    write_plan_files,
)
from airgap_ai_gateway.registry import promotion_plan
from airgap_ai_gateway.renderer import render_manifests, write_rendered_manifests
from airgap_ai_gateway.reporting import to_json
from airgap_ai_gateway.rollback import rollback_plan
from airgap_ai_gateway.safety import ensure_mutation_is_confirmed
from airgap_ai_gateway.verification import verification_plan

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
    _add_simple_command(bundle_sub, "build", "Plan an offline bundle build.", action="bundle build")
    _add_simple_command(
        bundle_sub, "verify", "Verify an offline bundle manifest.", action="bundle verify"
    )

    registry = subcommands.add_parser("registry", help="Promote images into a private registry.")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    promote = registry_sub.add_parser(
        "promote", help="Plan image promotion into the private registry."
    )
    promote.set_defaults(
        handler=_handle_registry_promote, action="registry promote", mutating=False
    )

    deploy = subcommands.add_parser("deploy", help="Plan or apply gateway deployment.")
    deploy_sub = deploy.add_subparsers(dest="deploy_command", required=True)
    _add_execution_plan_command(deploy_sub, "plan", "Plan deployment.", "deploy apply")
    _add_mutating_command(
        deploy_sub, "apply", "Apply deployment after safety confirmation.", "deploy apply"
    )

    verify = subcommands.add_parser("verify", help="Plan the verification checks.")
    verify.set_defaults(handler=_handle_verify, action="verify", mutating=False)

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
    model_add = model_sub.add_parser("add", help="Plan adding a model with default-deny access.")
    model_add.add_argument("--model-key", default="example-model", help="Model key to plan.")
    model_add.set_defaults(handler=_handle_model_add, action="model add", mutating=False)

    consumer = subcommands.add_parser("consumer", help="Consumer lifecycle commands.")
    consumer_sub = consumer.add_subparsers(dest="consumer_command", required=True)
    for name in ("add", "rotate", "revoke"):
        item = consumer_sub.add_parser(name, help=f"Plan consumer {name}.")
        item.add_argument(
            "--consumer-key", default="example-consumer", help="Consumer key to plan."
        )
        item.set_defaults(handler=_handle_plan, action=f"consumer {name}", mutating=False)

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


def _handle_registry_promote(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(to_json(promotion_plan(config)))
    return 0


def _handle_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(to_json(verification_plan(config)))
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


def _handle_model_add(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    model = next((item for item in config.models if item.key == args.model_key), None)
    if model is None or model.kind is not ModelKind.CHAT:
        print(to_json(build_plan(config, args.action, mutating=False)))
        return 0
    print(render_chat_model_onboarding(model, namespace=config.platform.gateway.namespace), end="")
    return 0


def _require_apply_argument(value: object, flag: str) -> None:
    if not isinstance(value, str) or not value:
        msg = f"apply command refused: pass {flag}."
        raise AirgapGatewayError(msg)


if __name__ == "__main__":
    entrypoint()
