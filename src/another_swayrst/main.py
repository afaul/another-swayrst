import logging
import os
import pathlib
import sys

import i3ipc

_logger = logging.getLogger(__name__)


class AnotherSwayrst:
    def __init__(self) -> None:
        self.swayrst_profile_dir: pathlib.Path = self.get_profile_dir()
        self.i3ipc: i3ipc.Connection = i3ipc.Connection()

    def get_profile_dir(self) -> pathlib.Path:
        home_folder = pathlib.Path.home()
        config_folder = os.environ.get(
            "XDG_CONFIG_HOME", home_folder.joinpath(".config")
        )
        if isinstance(config_folder, str):
            config_folder = pathlib.Path(config_folder)

        paths = [
            home_folder.joinpath(".sway"),
            config_folder.joinpath("sway"),
            home_folder.joinpath(".i3"),
            config_folder.joinpath("i3"),
        ]
        sway_config_folder = None
        for path in paths:
            if path.exists() and path.is_dir():
                sway_config_folder = path
                break

        if sway_config_folder is None:
            _logger.critical(
                "Sway config not found! Make sure to use a default config path (man sway)"
            )
            sys.exit(1000)
        else:
            _logger.info(f"using {sway_config_folder} as config directory")

            swayrst_profile_dir = sway_config_folder.joinpath(
                "another-swayrst-profiles"
            )
            swayrst_profile_dir.mkdir(parents=True, exist_ok=True)
            return swayrst_profile_dir

    def set_profile(self, profile_name: str) -> None:
        self.profile_name: str = profile_name
        self.profile_file: pathlib.Path = self.swayrst_profile_dir.joinpath(
            f"{profile_name}.json"
        )

    def load(self) -> None:
        _logger.info(f"loading profile {self.profile_name} from {self.profile_file}")
        if not self.profile_file.exists():
            _logger.critical(
                f"profile file: {self.profile_file} doesn't exists. -> Ending"
            )
            sys.exit(1001)

    def save(self) -> None:
        _logger.info(f"Saving profile {self.profile_name} to {self.profile_file}")
        if self.profile_file.exists():
            _logger.warning(
                f"Profile {self.profile_name} already exists -> overwriting {self.profile_file}"
            )
