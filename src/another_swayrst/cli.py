import logging
import os
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
    help="Config file to use (will be created if not existing)",
    type=click.Path(dir_okay=False, resolve_path=True, path_type=pathlib.Path),
)
def main(ctx, log_level: str, config_file):
    log_handlers = []
    # log_stream_handler = logging.StreamHandler(sys.stderr)
    log_stream_handler = logging.StreamHandler(sys.stdout)
    log_handlers.append(log_stream_handler)
    logging.basicConfig(
        handlers=log_handlers,
        # format="%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging._nameToLevel[log_level],
    )
    _logger.info(
        f"another-swayrst started with log-level: {logging.getLevelName(logging.root.level) }"
    )
    obj = another_swayrst.AnotherSwayrst(config_file=config_file)
    ctx.params["obj"] = obj


@main.command()
@click.pass_context
@click.argument("profile_name")
def save(ctx, profile_name: str):
    """Save current window layout."""
    obj: another_swayrst.AnotherSwayrst = ctx.parent.params["obj"]
    obj.set_profile(profile_name)
    obj.save()


@main.command()
@click.pass_context
@click.argument("profile_name")
def load(ctx, profile_name: str):
    """Load and restore the specified profile."""
    obj: another_swayrst.AnotherSwayrst = ctx.parent.params["obj"]
    obj.set_profile(profile_name)
    obj.load()


if __name__ == "__main__":
    main(auto_envvar_prefix="ANOTHER_SWAYRST")
