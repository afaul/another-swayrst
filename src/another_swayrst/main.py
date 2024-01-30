import json
import logging
import os
import pathlib
import subprocess
import sys
import time

import i3ipc
import psutil
import pydantic.tools

import another_swayrst.types as types

_logger: logging.Logger = logging.getLogger(__name__)


class AnotherSwayrst:
    def __init__(self, config_file: pathlib.Path | None = None) -> None:
        config_file_name = "another-swayrst.conf"
        possible_dirs: list[pathlib.Path] = self.get_possible_conf_dirs()
        if config_file is None:
            for dir in possible_dirs:
                config_file = dir.joinpath(config_file_name)
                if config_file.exists():
                    break
                else:
                    config_file = None
        if config_file is None:
            config_file = possible_dirs[0].joinpath(config_file_name)

        if config_file.exists():
            _logger.info(f"loading config file: {config_file}")
            with config_file.open("r") as FILE:
                config_json: dict = json.load(FILE)
            self.config: types.AnotherSwayrstConfig = pydantic.tools.parse_obj_as(
                types.AnotherSwayrstConfig, config_json
            )
        else:
            _logger.info(f"create config file: {config_file}")
            self.config = types.AnotherSwayrstConfig(
                profile_dir=possible_dirs[0].joinpath("another-swayrst-profiles")
            )
            with config_file.open("w") as FILE:
                FILE.write(self.config.model_dump_json(indent=2))
        self.config.profile_dir.mkdir(exist_ok=True)
        self.i3ipc: i3ipc.Connection = i3ipc.Connection()

    def get_possible_conf_dirs(self) -> list[pathlib.Path]:
        """Return a list of possible configuration directories, based on default configuration dirs of sway and i3."""

        home_folder = pathlib.Path.home()
        config_folder = os.environ.get(
            "XDG_CONFIG_HOME", home_folder.joinpath(".config")
        )
        if isinstance(config_folder, str):
            config_folder = pathlib.Path(config_folder)

        possible_paths: list[pathlib.Path] = [
            home_folder.joinpath(".sway"),
            config_folder.joinpath("sway"),
            home_folder.joinpath(".i3"),
            config_folder.joinpath("i3"),
        ]
        path: list[pathlib.Path] = []
        for possible_path in possible_paths:
            if possible_path.exists() and possible_path.is_dir():
                path.append(possible_path)
        if len(path) == 0:
            _logger.critical(
                "Sway config not found! Make sure to use a default config path (man sway)"
            )
            sys.exit(1000)
        else:
            return path

    def set_profile(self, profile_name: str) -> None:
        """set the given profile to load/save."""
        self.profile_name: str = profile_name
        self.profile_file: pathlib.Path = self.config.profile_dir.joinpath(
            f"{profile_name}.json"
        )

    def load(self) -> None:
        """Load an window tree from a json file and recreate the defined layout."""

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
        self.old_map_id_app, self.old_map_cmd_ids = self.get_map_of_apps(
            self.restore_tree
        )
        self._start_missing_apps()
        self._wait_until_apps_started(timeout=self.config.app_start_timeout)
        self.move_all_apps_to_scratchpad()
        self.recreate_workspaces()

    def save(self) -> None:
        """Save the current tree as a json file."""

        _logger.info(f"Saving profile {self.profile_name} to {self.profile_file}")
        if self.profile_file is None:
            _logger.critical("no profile set -> Exiting")
            sys.exit(1002)
        if self.profile_file.exists():
            _logger.warning(
                f"Profile {self.profile_name} already exists -> overwriting {self.profile_file}"
            )
        current_tree: types.Tree = self.get_current_tree()
        with self.profile_file.open("w") as FILE:
            FILE.write(current_tree.model_dump_json(indent=2))

    def __parse_tree_container_elements(
        self, nodes
    ) -> list[types.Container | types.AppContainer]:
        """Iterate through all container elements in i3ipc-tree."""

        return_element: list[types.Container | types.AppContainer] = []
        for node in nodes:
            if node["type"] not in ["con", "floating_con"]:
                _logger.warning(f"Unexpected node type found: {node['type']}")
            if len(node["nodes"]) == 0:
                command: list[str] = psutil.Process(node["pid"]).cmdline()
                container = types.AppContainer(
                    id=node["id"],
                    command=command,
                    width=node["window_rect"]["width"],
                    height=node["window_rect"]["height"],
                )
            else:
                subcontainer: list[
                    types.Container | types.AppContainer
                ] = self.__parse_tree_container_elements(node["nodes"])
                container = types.Container(
                    id=node["id"],
                    sub_containers=subcontainer,
                    layout=node["layout"],
                )
            return_element.append(container)
        return return_element

    def __parse_tree_workspace_elements(self, nodes) -> list[types.Workspace]:
        """Iterate through all workspace elements in i3ipc-tree."""

        return_element: list[types.Workspace] = []
        for node in nodes:
            if node["type"] != "workspace":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            containers: list[
                types.Container | types.AppContainer
            ] = self.__parse_tree_container_elements(node["nodes"])
            floating_containers: list[
                types.Container | types.AppContainer
            ] = self.__parse_tree_container_elements(node["floating_nodes"])
            workspace_number: int | None = None
            if "num" in node:
                workspace_number = node["num"]
            workspace = types.Workspace(
                id=node["id"],
                name=node["name"],
                containers=containers,
                floating_containers=floating_containers,
                number=workspace_number,
                layout=node["layout"],
            )
            return_element.append(workspace)
        return return_element

    def __parse_tree_output_elements(self, nodes) -> list[types.Output]:
        """Iterate through all output elements in i3ipc-tree."""

        return_element: list[types.Output] = []
        for node in nodes:
            if node["type"] != "output":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            workspaces: list[types.Workspace] = self.__parse_tree_workspace_elements(
                node["nodes"]
            )
            output = types.Output(
                id=node["id"], name=node["name"], workspaces=workspaces
            )
            return_element.append(output)
        return return_element

    def get_current_tree(self) -> types.Tree:
        """Create a representation of the current window tree."""

        tree: i3ipc.Con = self.i3ipc.get_tree()
        tree_data: dict = tree.ipc_data

        list_of_outputs: list[types.Output] = self.__parse_tree_output_elements(
            tree_data["nodes"]
        )

        return types.Tree(outputs=list_of_outputs)

    def __recursive_walk_through_container_tree(
        self, containers: list[types.Container | types.AppContainer]
    ) -> dict[int, types.AppContainer]:
        """Walk through container tree and return a map of the ID of an application to the corresponding container."""

        map_id_app: dict[int, types.AppContainer] = {}
        for container in containers:
            if isinstance(container, types.AppContainer):
                id: int = container.id
                if id in map_id_app:
                    _logger.warning(f"duplicate ID found: {id}")
                map_id_app[id] = container
            elif isinstance(container, types.Container):
                sub_maps: dict[
                    int, types.AppContainer
                ] = self.__recursive_walk_through_container_tree(
                    container.sub_containers
                )
                for key, value in sub_maps.items():
                    if key in map_id_app:
                        _logger.warning(f"duplicate ID found: {key}")
                    map_id_app[key] = value

        return map_id_app

    def get_map_of_apps(
        self, tree: types.Tree
    ) -> tuple[dict[int, types.AppContainer], dict[str, list[int]]]:
        """Create a map of ID to app in given tree and a map of the command which was used to start a app to its ID."""

        map_id_app: dict[int, types.AppContainer] = {}
        map_commands_id: dict[str, list[int]] = {}

        for output in tree.outputs:
            for workspace in output.workspaces:
                map_apps_per_workspace: dict[
                    int, types.AppContainer
                ] = self.__recursive_walk_through_container_tree(workspace.containers)
                for app_id, app_container in map_apps_per_workspace.items():
                    if app_id in map_id_app:
                        _logger.warning(f"duplicate id found: {app_id}")
                    map_id_app[app_id] = app_container
                for container in workspace.floating_containers:
                    if isinstance(container, types.AppContainer):
                        if container.id in map_id_app:
                            _logger.warning(f"duplicate id found: {container.id}")
                        map_id_app[container.id] = container
                    else:
                        _logger.warning("other type than App in floating containers")

        for id, container in map_id_app.items():
            cmd: list[str] = container.command
            cmd_str: str = " ".join(cmd)
            if cmd_str not in map_commands_id:
                map_commands_id[cmd_str] = []
            map_commands_id[cmd_str].append(id)

        return map_id_app, map_commands_id

    def _get_missing_apps(self) -> list[dict[str, int | list[str]]]:
        """Create a list of all apps in old tree but not in current one."""

        if self.old_map_cmd_ids is None:
            _logger.error("no map for cmd to ids to restore available")
            sys.exit(1004)

        _, new_map_cmd_ids = self.get_map_of_apps(self.get_current_tree())

        missing_apps: list[dict[str, int | list[str]]] = []
        for cmd_str, old_ids in self.old_map_cmd_ids.items():
            old_amount = len(old_ids)
            if cmd_str in new_map_cmd_ids:
                new_amount = len(new_map_cmd_ids[cmd_str])
                if new_amount < old_amount:
                    missing_apps.append(
                        {
                            "amount": old_amount - new_amount,
                            "cmd": self.old_map_id_app[
                                self.old_map_cmd_ids[cmd_str][0]
                            ].command,
                        }
                    )
            else:
                missing_apps.append(
                    {
                        "amount": old_amount,
                        "cmd": self.old_map_id_app[
                            self.old_map_cmd_ids[cmd_str][0]
                        ].command,
                    }
                )
        return missing_apps

    def _start_missing_apps(self) -> None:
        """Start all apps which are in old tree but not in current one."""

        missing_apps: list[dict[str, int | list[str]]] = self._get_missing_apps()
        for app_info in missing_apps:
            amount: int = app_info["amount"]  # type: ignore
            cmd: list[str] = app_info["cmd"]  # type: ignore
            for _ in range(amount):
                if cmd[0] in self.config.command_translation:
                    cmd[0] = self.config.command_translation[cmd[0]]
                subprocess.Popen(cmd, cwd=pathlib.Path.home())

    def _wait_until_apps_started(self, timeout: int) -> None:
        """Wait until all missing apps are started, or timeout reached."""

        missing_apps_count = len(self._get_missing_apps())
        timeout_counter = 0
        while missing_apps_count > 0 and timeout_counter < timeout:
            time.sleep(1)
            timeout_counter += 1
            missing_apps_count = len(self._get_missing_apps())

        if timeout_counter >= timeout:
            _logger.warning(f"not all missing apps started after timeout of {timeout}s")

    def _get_old_to_new_map(self) -> dict[int, int]:
        """Create map of app id in old tree to app id in new tree."""

        map_old_to_new_id: dict[int, int] = {}
        _, new_map_cmd_ids = self.get_map_of_apps(self.get_current_tree())

        for cmd, ids in self.old_map_cmd_ids.items():
            for old_id in ids:
                if cmd in new_map_cmd_ids:
                    if len(new_map_cmd_ids[cmd]) > 0:
                        new_id: int = new_map_cmd_ids[cmd].pop()
                        map_old_to_new_id[old_id] = new_id

        return map_old_to_new_id

    def move_all_apps_to_scratchpad(self) -> None:
        """Move all apps to scratchpad, create empty."""

        new_map_id_app, _ = self.get_map_of_apps(self.get_current_tree())
        for id in new_map_id_app.keys():
            app: i3ipc.Con | None = self.i3ipc.get_tree().find_by_id(id)
            if app is not None:
                self.__execute_command(app=app, command="move scratchpad")

    def recreate_workspaces(self) -> None:
        """Recreate workspace layout and application sizes."""

        map_old_to_new_id: dict[int, int] = self._get_old_to_new_map()
        for output in self.restore_tree.outputs:
            if output.name != "__i3":
                for workspace in output.workspaces:
                    if workspace.number is None:
                        _logger.warning("workspace without number found")
                    else:
                        for container in workspace.containers:
                            old_id: int = self.__get_first_app_id(container)
                            new_id: int = map_old_to_new_id[old_id]
                            app: i3ipc.Con | None = self.i3ipc.get_tree().find_by_id(
                                new_id
                            )
                            if app is not None:
                                self.__execute_command(
                                    app=app,
                                    command=f"move container to workspace number {workspace.number}",
                                )
                                self.__execute_command(app=app, command="floating off")
                                layout: str = workspace.layout
                                if layout == "stacked":
                                    layout = "stacking"
                                self.__execute_command(
                                    app=app, command=f"layout {layout}"
                                )

                        for container in workspace.containers:
                            if isinstance(container, types.Container):
                                self.recreate_containers(
                                    containers=container.sub_containers,
                                    workspace_number=workspace.number,
                                    map_old_to_new_id=map_old_to_new_id,
                                    layout=container.layout,
                                )
                        for con in workspace.floating_containers:
                            new_con_id: int = map_old_to_new_id[con.id]
                            app = self.i3ipc.get_tree().find_by_id(new_con_id)
                            if app is not None:
                                self.__execute_command(
                                    app=app,
                                    command=f"move container to workspace number {workspace.number}",
                                )
                        # resize apps
                        self.resize_apps(workspace.containers, map_old_to_new_id)

    def resize_apps(
        self,
        containers: list[types.Container | types.AppContainer],
        map_old_to_new_id: dict[int, int],
    ) -> None:
        """Iterate through all apps and resize them to given size."""

        for container in containers:
            if isinstance(container, types.AppContainer):
                new_id: int = map_old_to_new_id[container.id]
                new_app: i3ipc.Con | None = self.i3ipc.get_tree().find_by_id(new_id)
                current_height: int = new_app.window_rect.height  # type: ignore
                current_width: int = new_app.window_rect.width  # type: ignore

                if current_height < container.height:
                    self.__execute_command(
                        app=new_app,
                        command=f"resize grow height {container.height - current_height}px",
                    )
                elif current_height > container.height:
                    self.__execute_command(
                        app=new_app,
                        command=f"resize shrink height {current_height - container.height}px",
                    )

                if current_width < container.width:
                    self.__execute_command(
                        app=new_app,
                        command=f"resize grow width {container.width-current_width}px",
                    )
                elif current_width > container.width:
                    self.__execute_command(
                        app=new_app,
                        command=f"resize shrink width {current_width-container.width}px",
                    )

            elif isinstance(container, types.Container):
                self.resize_apps(
                    containers=container.sub_containers,
                    map_old_to_new_id=map_old_to_new_id,
                )

    def __get_first_app_id(
        self, container: types.Container | types.AppContainer
    ) -> int:
        """Depth first walk through a tree of containers and return the id of the first container which represent an application."""

        if isinstance(container, types.AppContainer):
            return container.id
        else:
            return self.__get_first_app_id(container.sub_containers[0])

    def __execute_command(self, command: str, app: i3ipc.Con | None = None) -> None:
        """Execute an i3ipc command and log possible error messages."""

        con: i3ipc.Con | i3ipc.Connection = self.i3ipc
        if app is not None:
            con = app

        ret = con.command(command)
        if not ret[0].success:  # type: ignore
            _logger.error(f"error while executing ipc command: {ret[0].error}")  # type: ignore

    def recreate_containers(
        self,
        containers: list[types.Container | types.AppContainer],
        workspace_number: int,
        map_old_to_new_id: dict[int, int],
        layout: str,
    ) -> None:
        """width first walk through a given tree of Containers and recreate the layout defined by the tree."""

        first_app = True
        for container in containers:
            old_id: int = self.__get_first_app_id(container)
            new_id: int = map_old_to_new_id[old_id]
            app: i3ipc.Con | None = self.i3ipc.get_tree().find_by_id(new_id)
            if app is not None:
                if first_app:
                    self.__execute_command(app=app, command="focus")
                    self.__execute_command(app=app, command="split toggle")
                    if layout == "stacked":
                        layout = "stacking"
                    self.__execute_command(app=app, command=f"layout {layout}")
                    first_app = False
                else:
                    self.__execute_command(
                        app=app,
                        command=f"move container to workspace number {workspace_number}",
                    )
                    self.__execute_command(app=app, command="floating off")

        for container in containers:
            if isinstance(container, types.Container):
                self.recreate_containers(
                    containers=container.sub_containers,
                    workspace_number=workspace_number,
                    map_old_to_new_id=map_old_to_new_id,
                    layout=container.layout,
                )
