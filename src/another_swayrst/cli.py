import logging
import pathlib
import sys

import click

import another_swayrst

_logger = logging.getLogger(__name__)


@click.group()
@click.pass_context
@click.option(
    "-v",
    "--log-level",
    "log_level",
    type=click.Choice(
        [str(x) for x in logging._nameToLevel.keys()], case_sensitive=False
    ),
    default="WARNING",
    show_default=True,
    help="Verbosity level of logging",
)
@click.option(
    "-c",
    "--config-file",
    "config_file",
    default=None,
    help="Config file to use.",
    type=click.Path(
        dir_okay=False, file_okay=True, resolve_path=True, path_type=pathlib.Path
    ),
)
@click.option(
    "--save-current-config",
    is_flag=True,
    default=False,
    help="Save the current effective configuration in a file.",
)
@click.option(
    "--profile-dir",
    default=None,
    type=click.Path(
        dir_okay=True, file_okay=False, resolve_path=True, path_type=pathlib.Path
    ),
    help="Where to search for / save the layout profiles.",
)
@click.option(
    "--start-missing-apps/--no-start-missing-apps",
    default=None,
    help="(Not) Start the missing apps automatically.",
)
@click.option(
    "--command-translation",
    default=None,
    nargs=2,
    multiple=True,
    help="[..ion A B] Translate command A into B when starting missing apps.",
)
@click.option(
    "--respect-other-workspaces/--no-respect-other-workspace",
    default=None,
    help="Respect the configuration of other workspaces.",
)
def main(
    ctx,
    log_level: str,
    config_file: pathlib.Path | None,
    start_missing_apps: bool | None,
    save_current_config: bool,
    profile_dir: pathlib.Path | None,
    command_translation: tuple[tuple[str, str]] | None,
    respect_other_workspaces: bool | None,
):
    log_handlers = []
    # log_stream_handler = logging.StreamHandler(sys.stderr)
    log_stream_handler = logging.StreamHandler(sys.stdout)
    log_handlers.append(log_stream_handler)
    logging.basicConfig(
        handlers=log_handlers,
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging._nameToLevel[log_level],
    )
    _logger.info(
        f"another-swayrst started with log-level: {logging.getLevelName(logging.root.level)}"
    )
    obj = another_swayrst.AnotherSwayrst(
        config_file=config_file,
        start_missing_apps=start_missing_apps,
        save_current_config=save_current_config,
        profile_dir=profile_dir,
        command_translation=command_translation,
        respect_other_workspaces=respect_other_workspaces,
    )
    ctx.params["obj"] = obj


@main.command()
@click.pass_context
@click.argument("profile_name")
@click.option(
    "-w",
    "--workspace",
    "workspaces",
    default=None,
    nargs=1,
    multiple=True,
    help="Workspace (by name) to save.",
)
def save(ctx, profile_name: str, workspaces: tuple[str]) -> None:
    """Save current window layout."""

    obj: another_swayrst.AnotherSwayrst = ctx.parent.params["obj"]
    obj.save(profile_name, workspaces)


@main.command()
@click.pass_context
@click.argument("profile_name")
def load(ctx, profile_name: str):
    """Load and restore the specified profile."""

    obj: another_swayrst.AnotherSwayrst = ctx.parent.params["obj"]
    obj.load(profile_name)


@main.command()
@click.pass_context
@click.argument("profile_name", default="")
def show_config(ctx, profile_name: str):
    """Show the effective configuration and exit"""

    obj: another_swayrst.AnotherSwayrst = ctx.parent.params["obj"]
    obj.show_config()


if __name__ == "__main__":
    main()
