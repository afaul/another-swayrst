import json
import logging
import os
import pathlib
import sys
import typing

import i3ipc
import psutil
import pydantic.tools

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
                f"profile file: {self.profile_file} doesn't exists. -> Exiting"
            )
            sys.exit(1001)
        with self.profile_file.open("r") as FILE:
            restore_tree_json = json.load(FILE)
        self.restore_tree: types.Tree = pydantic.tools.parse_obj_as(
            types.Tree, restore_tree_json
        )
        self.old_map_id_app = self.get_map_of_apps(self.restore_tree)
        x = self._get_missing_apps()

        print()

    def save(self) -> None:
        _logger.info(f"Saving profile {self.profile_name} to {self.profile_file}")
        if self.profile_file is None:
            _logger.critical("no profile set -> Exiting")
            sys.exit(1002)
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

    def __iterate_over_containers(
        self, containers: list[types.Container | types.AppContainer]
    ) -> dict[int, types.AppContainer]:
        map_id_app: dict[int, types.AppContainer] = {}
        for container in containers:
            if isinstance(container, types.AppContainer):
                id = container.id
                if id in map_id_app:
                    _logger.warning(f"duplicate id found: {id}")
                map_id_app[id] = container
            elif isinstance(container, types.Container):
                sub_maps = self.__iterate_over_containers(container.sub_containers)
                for key, value in sub_maps.items():
                    if key in map_id_app:
                        _logger.warning(f"duplicate id found: {key}")
                    map_id_app[key] = value

        return map_id_app

    def get_map_of_apps(self, tree: types.Tree) -> dict[int, types.AppContainer]:
        map_id_app: dict[int, types.AppContainer] = {}

        for output in tree.outputs:
            for workspace in output.workspaces:
                x = self.__iterate_over_containers(workspace.containers)
                for key, value in x.items():
                    if key in map_id_app:
                        _logger.warning(f"duplicate id found: {key}")
                    map_id_app[key] = value

        return map_id_app

    def _get_missing_apps(self) -> list[dict[str, typing.Any]]:
        if self.old_map_id_app is None:
            _logger.error("no map for id to apps to restore available")
            sys.exit(1004)

        old_cmds: dict[str, typing.Any] = {}
        for value in self.old_map_id_app.values():
            cmd = value.command
            cmd_str = " ".join(cmd)
            if cmd_str not in old_cmds:
                old_cmds[cmd_str] = {"amount": 0, "cmd": cmd}
            old_cmds[cmd_str]["amount"] += 1

        new_map_id_app = self.get_map_of_apps(self.get_current_tree())
        new_cmds: dict[str, typing.Any] = {}
        for value in new_map_id_app.values():
            cmd = value.command
            cmd_str = " ".join(cmd)
            if cmd_str not in new_cmds:
                new_cmds[cmd_str] = {"amount": 0, "cmd": cmd}
            new_cmds[cmd_str]["amount"] += 1

        missing_apps: list[dict[str, typing.Any]] = []
        for cmd_str, old_info in old_cmds.items():
            old_amount = old_info["amount"]
            if cmd_str in new_cmds:
                new_amount = new_cmds[cmd_str]["amount"]
                if new_amount < old_amount:
                    missing_apps.append(
                        {"amount": old_amount - new_amount, "cmd": old_info["cmd"]}
                    )
            else:
                missing_apps.append(old_info)
        return missing_apps
