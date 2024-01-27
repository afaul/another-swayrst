import logging
import os
import pathlib
import sys

import i3ipc
import psutil

import another_swayrst.types as types

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
        current_tree = self.get_current_tree()
        with self.profile_file.open("w") as FILE:
            FILE.write(current_tree.model_dump_json(indent=2))

    def __parse_tree_container_elements(
        self, nodes
    ) -> list[types.Container | types.AppContainer]:
        return_element: list[types.Container | types.AppContainer] = []
        for node in nodes:
            if node["type"] != "con":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            if len(node["nodes"]) == 0:
                command = psutil.Process(node["pid"]).cmdline()
                container = types.AppContainer(
                    id=node["id"], command=command, layout=node["layout"]
                )
            else:
                subcontainer: list[
                    types.Container | types.AppContainer
                ] = self.__parse_tree_container_elements(node["nodes"])
                container = types.Container(
                    id=node["id"], sub_containers=subcontainer, layout=node["layout"]
                )
            return_element.append(container)
        return return_element

    def __parse_tree_workspace_elements(self, nodes) -> list[types.Workspace]:
        return_element: list[types.Workspace] = []
        for node in nodes:
            if node["type"] != "workspace":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            x = self.__parse_tree_container_elements(node["nodes"])
            if len(x) == 0:
                _logger.warning("Workspace without apps found")
            workspace = types.Workspace(id=node["id"], name=node["name"], containers=x)
            return_element.append(workspace)
        return return_element

    def __parse_tree_output_elements(self, nodes) -> list[types.Output]:
        return_element: list[types.Output] = []
        for node in nodes:
            if node["name"] != "__i3":
                if node["type"] != "output":
                    _logger.warning(f"Unexpected node type found: {node['type']}")
                x = self.__parse_tree_workspace_elements(node["nodes"])
                output = types.Output(id=node["id"], name=node["name"], workspaces=x)
                return_element.append(output)
        return return_element

    def get_current_tree(self) -> types.Tree:
        tree = self.i3ipc.get_tree()
        tree_data = tree.ipc_data

        list_of_outputs: list[types.Output] = self.__parse_tree_output_elements(
            tree_data["nodes"]
        )
        return types.Tree(outputs=list_of_outputs)
