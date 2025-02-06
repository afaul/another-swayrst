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
    def __init__(
        self,
        config_file: pathlib.Path | None,
        start_missing_apps: bool | None,
        save_current_config: bool,
        profile_dir: pathlib.Path | None,
        command_translation: tuple[tuple[str, str]] | None,
        respect_other_workspaces: bool | None,
    ) -> None:
        self.__config_file: pathlib.Path | None = config_file
        config_file_name = "another-swayrst.conf"
        possible_dirs: list[pathlib.Path] = self.__get_possible_conf_dirs()
        if self.__config_file is None:
            for dir in possible_dirs:
                self.__config_file = dir.joinpath(config_file_name)
                if self.__config_file.exists():
                    break
                else:
                    self.__config_file = None
        if self.__config_file is None:
            self.__config_file = possible_dirs[0].joinpath(config_file_name)

        if self.__config_file.exists():
            _logger.info(f"loading config file: {self.__config_file}")
            with self.__config_file.open("r") as FILE:
                config_json: dict = json.load(FILE)
            if config_json["version"] != 2:
                if config_json["version"] == 1:
                    config_json["version"] = 2
                    config_json["respect_other_workspaces"] = False
                with self.__config_file.open("w") as FILE:
                    json.dump(config_json, FILE, indent=2)
            self._config: types.AnotherSwayrstConfig = pydantic.tools.parse_obj_as(
                types.AnotherSwayrstConfig, config_json
            )
        else:
            _logger.info("loading default values for configuration")
            self._config = types.AnotherSwayrstConfig(
                profile_dir=possible_dirs[0].joinpath("another-swayrst-profiles")
            )

        if profile_dir is not None:
            self._config.profile_dir = profile_dir

        if start_missing_apps is not None:
            self._config.start_missing_apps.active = start_missing_apps

        if command_translation is not None:
            for translation_pair in command_translation:
                command_A: str = translation_pair[0]
                command_B: str = translation_pair[1]
                self._config.start_missing_apps.command_translation[command_A] = (
                    command_B
                )
        if respect_other_workspaces is not None:
            self._config.respect_other_workspaces = respect_other_workspaces

        if save_current_config:
            _logger.info(f"create config file: {self.__config_file}")
            with self.__config_file.open("w") as FILE:
                FILE.write(self._config.model_dump_json(indent=2))
        self.__i3ipc: i3ipc.Connection = i3ipc.Connection()

    def __execute_command(self, command: str, app: i3ipc.Con | None = None) -> None:
        """Execute an i3ipc command and log possible error messages."""

        con: i3ipc.Con | i3ipc.Connection = self.__i3ipc
        if app is not None:
            con = app

        ret = con.command(command)
        if not ret[0].success:  # type: ignore
            _logger.error(
                f"error while executing ipc command {command}: {ret[0].error}"  # type: ignore
            )  # type: ignore

    def __get_current_tree(self) -> types.Tree:
        """Create a representation of the current window tree."""

        tree: i3ipc.Con = self.__i3ipc.get_tree()
        tree_data: dict = tree.ipc_data

        list_of_outputs: list[types.Output] = self.__parse_tree_output_elements(
            tree_data["nodes"]
        )

        return types.Tree(outputs=list_of_outputs)

    def __get_first_app_id(
        self,
        container: types.Container | types.AppContainer,
        map_old_to_new_id: dict[int, int],
    ) -> int | None:
        """Depth first walk through a tree of containers and return the id of the first container which represent an application and which exist in the current tree."""

        if isinstance(container, types.AppContainer):
            if container.id in map_old_to_new_id:
                return map_old_to_new_id[container.id]
        else:
            for sub_container in container.sub_containers:
                first_app_id = self.__get_first_app_id(sub_container, map_old_to_new_id)
                if first_app_id is not None:
                    return first_app_id

    def __get_map_of_apps(
        self, tree: types.Tree
    ) -> tuple[dict[int, types.AppContainer], dict[str, list[int]]]:
        """Create a map of ID to app in given tree and a map of the command which was used to start a app to its ID."""

        map_id_app: dict[int, types.AppContainer] = {}
        map_commands_id: dict[str, list[int]] = {}

        for output in tree.outputs:
            for workspace in output.workspaces:
                map_apps_per_workspace: dict[int, types.AppContainer] = (
                    self.__recursive_walk_through_container_tree(workspace.containers)
                )
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

    def __get_missing_apps(self) -> list[dict[str, int | list[str]]]:
        """Create a list of all apps in old tree but not in current one."""

        if self.__old_map_cmd_ids is None:
            _logger.error("no map for cmd to ids to restore available")
            sys.exit(1004)

        _, new_map_cmd_ids = self.__get_map_of_apps(self.__get_current_tree())

        missing_apps: list[dict[str, int | list[str]]] = []
        for cmd_str, old_ids in self.__old_map_cmd_ids.items():
            old_amount = len(old_ids)
            if cmd_str in new_map_cmd_ids:
                new_amount = len(new_map_cmd_ids[cmd_str])
                if new_amount < old_amount:
                    missing_apps.append(
                        {
                            "amount": old_amount - new_amount,
                            "cmd": self.__old_map_id_app[
                                self.__old_map_cmd_ids[cmd_str][0]
                            ].command,
                        }
                    )
            else:
                missing_apps.append(
                    {
                        "amount": old_amount,
                        "cmd": self.__old_map_id_app[
                            self.__old_map_cmd_ids[cmd_str][0]
                        ].command,
                    }
                )
        return missing_apps

    def __get_old_to_new_map(self) -> dict[int, int]:
        """Create map of app id in old tree to app id in new tree."""

        map_old_to_new_id: dict[int, int] = {}
        new_map_id_app, new_map_cmd_ids = self.__get_map_of_apps(
            self.__get_current_tree()
        )

        for cmd, ids in self.__old_map_cmd_ids.items():
            if cmd in new_map_cmd_ids:
                matched_old_ids: set[int] = set()
                for old_id in ids:
                    old_title = self.__old_map_id_app[old_id].title
                    for new_id in new_map_cmd_ids[cmd]:
                        new_title = new_map_id_app[new_id].title
                        if old_title == new_title:
                            map_old_to_new_id[old_id] = new_id
                            matched_old_ids.add(old_id)
                            new_map_cmd_ids[cmd].remove(new_id)
                            break

                for old_id in ids:
                    if old_id not in matched_old_ids:
                        if len(new_map_cmd_ids[cmd]) > 0:
                            new_id: int = new_map_cmd_ids[cmd].pop()
                            map_old_to_new_id[old_id] = new_id

        return map_old_to_new_id

    def __get_possible_conf_dirs(self) -> list[pathlib.Path]:
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

    def __move_all_apps_to_scratchpad(self) -> None:
        """Move all apps to scratchpad, create empty."""

        new_map_id_app, _ = self.__get_map_of_apps(self.__get_current_tree())
        for id in new_map_id_app.keys():
            app: i3ipc.Con | None = self.__i3ipc.get_tree().find_by_id(id)
            if app is not None:
                self.__execute_command(app=app, command="move scratchpad")

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
                    title=node["name"],
                )
            else:
                subcontainer: list[types.Container | types.AppContainer] = (
                    self.__parse_tree_container_elements(node["nodes"])
                )
                container = types.Container(
                    id=node["id"],
                    sub_containers=subcontainer,
                    layout=node["layout"],
                )
            return_element.append(container)
        return return_element

    def __output_in_tree(self, output_name: str, tree: types.Tree) -> bool:
        """Check if output is in tree."""
        for output in tree.outputs:
            if output.name == output_name:
                return True
        return False

    def __parse_tree_output_elements(self, nodes) -> list[types.Output]:
        """Iterate through all output elements in i3ipc-tree."""

        return_element: list[types.Output] = []
        for node in nodes:
            if node["type"] != "output":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            if self._config.respect_other_workspaces and hasattr(self, "_restore_tree"):
                if not self.__output_in_tree(node["name"], self._restore_tree):
                    continue
            workspaces: list[types.Workspace] = self.__parse_tree_workspace_elements(
                node["nodes"], node["name"]
            )
            output = types.Output(
                id=node["id"], name=node["name"], workspaces=workspaces
            )
            return_element.append(output)
        return return_element

    def __workspace_in_tree(
        self, workspace_name: str, output_name: str, tree: types.Tree
    ) -> bool:
        """Check if workspace is in tree."""
        for output in tree.outputs:
            if output.name == output_name:
                for workspace in output.workspaces:
                    if workspace.name == workspace_name:
                        return True
        return False

    def __parse_tree_workspace_elements(
        self, nodes, output_name: str
    ) -> list[types.Workspace]:
        """Iterate through all workspace elements in i3ipc-tree."""

        return_element: list[types.Workspace] = []
        for node in nodes:
            if node["type"] != "workspace":
                _logger.warning(f"Unexpected node type found: {node['type']}")

            if self._config.respect_other_workspaces and hasattr(self, "_restore_tree"):
                if not self.__workspace_in_tree(
                    node["name"], output_name, self._restore_tree
                ):
                    continue
            containers: list[types.Container | types.AppContainer] = (
                self.__parse_tree_container_elements(node["nodes"])
            )
            floating_containers: list[types.Container | types.AppContainer] = (
                self.__parse_tree_container_elements(node["floating_nodes"])
            )
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

    def __recreate_containers(
        self,
        containers: list[types.Container | types.AppContainer],
        workspace_number: int,
        map_old_to_new_id: dict[int, int],
        layout: str,
    ) -> None:
        """width first walk through a given tree of Containers and recreate the layout defined by the tree."""

        first_app = True
        for container in containers:
            new_id: int | None = self.__get_first_app_id(container, map_old_to_new_id)
            if new_id is not None:
                app: i3ipc.Con | None = self.__i3ipc.get_tree().find_by_id(new_id)
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
                self.__recreate_containers(
                    containers=container.sub_containers,
                    workspace_number=workspace_number,
                    map_old_to_new_id=map_old_to_new_id,
                    layout=container.layout,
                )

    def __recreate_workspaces(self) -> None:
        """Recreate workspace layout and application sizes."""

        map_old_to_new_id: dict[int, int] = self.__get_old_to_new_map()
        for output in self._restore_tree.outputs:
            if output.name != "__i3":
                for workspace in output.workspaces:
                    if workspace.number is None:
                        _logger.warning("workspace without number found")
                    else:
                        for container in workspace.containers:
                            new_id: int | None = self.__get_first_app_id(
                                container, map_old_to_new_id
                            )
                            if new_id is not None:
                                app: i3ipc.Con | None = (
                                    self.__i3ipc.get_tree().find_by_id(new_id)
                                )
                                if app is not None:
                                    self.__execute_command(
                                        app=app,
                                        command=f"move container to workspace number {workspace.number}",
                                    )
                                    self.__execute_command(
                                        app=app, command="floating off"
                                    )
                                    layout: str = workspace.layout
                                    if layout == "stacked":
                                        layout = "stacking"
                                    self.__execute_command(
                                        app=app, command=f"layout {layout}"
                                    )

                        for container in workspace.containers:
                            if isinstance(container, types.Container):
                                self.__recreate_containers(
                                    containers=container.sub_containers,
                                    workspace_number=workspace.number,
                                    map_old_to_new_id=map_old_to_new_id,
                                    layout=container.layout,
                                )
                        for con in workspace.floating_containers:
                            new_con_id: int = map_old_to_new_id[con.id]
                            app = self.__i3ipc.get_tree().find_by_id(new_con_id)
                            if app is not None:
                                self.__execute_command(
                                    app=app,
                                    command=f"move container to workspace number {workspace.number}",
                                )
                        # move workspace to output
                        self.__execute_command(
                            app=None,
                            command=f"move workspace to output {output.name}",
                        )

                        # resize apps
                        # map_old_to_new_id = self.__get_old_to_new_map()
                        self.__resize_apps(workspace.containers, map_old_to_new_id)

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
                sub_maps: dict[int, types.AppContainer] = (
                    self.__recursive_walk_through_container_tree(
                        container.sub_containers
                    )
                )
                for key, value in sub_maps.items():
                    if key in map_id_app:
                        _logger.warning(f"duplicate ID found: {key}")
                    map_id_app[key] = value

        return map_id_app

    def __resize_apps(
        self,
        containers: list[types.Container | types.AppContainer],
        map_old_to_new_id: dict[int, int],
    ) -> None:
        """Iterate through all apps and resize them to given size."""

        for container in containers:
            if isinstance(container, types.AppContainer):
                if container.id in map_old_to_new_id:
                    new_id: int = map_old_to_new_id[container.id]
                    new_app: i3ipc.Con | None = self.__i3ipc.get_tree().find_by_id(
                        new_id
                    )
                    current_height: int = new_app.window_rect.height  # type: ignore
                    current_width: int = new_app.window_rect.width  # type: ignore

                    if current_height < container.height:
                        self.__execute_command(
                            app=new_app,
                            command=f"resize grow down {container.height - current_height}px",
                        )
                    elif current_height > container.height:
                        self.__execute_command(
                            app=new_app,
                            command=f"resize shrink down {current_height - container.height}px",
                        )

                    if current_width < container.width:
                        self.__execute_command(
                            app=new_app,
                            command=f"resize grow right {container.width - current_width}px",
                        )
                    elif current_width > container.width:
                        self.__execute_command(
                            app=new_app,
                            command=f"resize shrink right {current_width - container.width}px",
                        )

            elif isinstance(container, types.Container):
                self.__resize_apps(
                    containers=container.sub_containers,
                    map_old_to_new_id=map_old_to_new_id,
                )

    def __set_profile(self, profile_name: str) -> None:
        """set the given profile to load/save."""

        self._profile_name: str = profile_name
        self._profile_file: pathlib.Path = self._config.profile_dir.joinpath(
            f"{profile_name}.json"
        )

    def __get_first_workspace(self, tree) -> types.Workspace | None:
        """Return the first non '__i3' workspace in tree."""
        for output in tree.outputs:
            if output.name != "__i3":
                for workspace in output.workspaces:
                    return workspace

    def __start_missing_apps(self) -> None:
        """Start all apps which are in old tree but not in current one."""

        if self._config.start_missing_apps.active:
            first_workspace = self.__get_first_workspace(self._restore_tree)
            if first_workspace is not None:
                self.__execute_command(
                    app=None,
                    command=f"workspace number {first_workspace.number}",
                )
            missing_apps: list[dict[str, int | list[str]]] = self.__get_missing_apps()
            while len(missing_apps) > 0:
                app_info: dict[str, int | list[str]] = missing_apps[0]
                cmd_org: list[str] = app_info["cmd"]  # type: ignore
                cmd_new: list[str] = cmd_org.copy()
                if cmd_org[0] in self._config.start_missing_apps.command_translation:
                    cmd_new[0] = self._config.start_missing_apps.command_translation[
                        cmd_org[0]
                    ]
                _logger.debug(f"starting App for {cmd_org} with command: {cmd_new}")
                p = subprocess.Popen(cmd_new, cwd=pathlib.Path.home())
                time.sleep(
                    self._config.start_missing_apps.wait_time_after_command_start
                )
                missing_apps = self.__get_missing_apps()

    def __check_output_exists(self, tree1: types.Tree, tree2: types.Tree) -> bool:
        """Check if at least one common output exists in given trees."""
        output_names_tree_1: set[str] = set()
        for output in tree1.outputs:
            output_name = output.name
            if output_name != "__i3":
                output_names_tree_1.add(output_name)
        for output in tree2.outputs:
            if output.name in output_names_tree_1:
                return True
        return False

    def load(self, profile_name: str) -> None:
        """Load an window tree from a json file and recreate the defined layout."""

        self.__set_profile(profile_name=profile_name)

        _logger.info(f"loading profile {self._profile_name} from {self._profile_file}")
        if not self._profile_file.exists():
            _logger.critical(
                f"profile file: {self._profile_file} doesn't exists. -> Exiting"
            )
            sys.exit(1001)

        with self._profile_file.open("r") as FILE:
            restore_tree_json = json.load(FILE)
        self._restore_tree: types.Tree = pydantic.tools.parse_obj_as(
            types.Tree, restore_tree_json
        )

        if not self.__check_output_exists(
            self._restore_tree, self.__get_current_tree()
        ):
            _logger.error("no common output name in restore profile and current system")
            sys.exit(1002)

        self.__old_map_id_app, self.__old_map_cmd_ids = self.__get_map_of_apps(
            self._restore_tree
        )
        self.__start_missing_apps()

        self.__move_all_apps_to_scratchpad()
        self.__recreate_workspaces()

    def save(self, profile_name) -> None:
        """Save the current tree as a json file."""

        self._config.profile_dir.mkdir(exist_ok=True)
        self.__set_profile(profile_name=profile_name)

        _logger.info(f"Saving profile {self._profile_name} to {self._profile_file}")
        if self._profile_file is None:
            _logger.critical("no profile set -> Exiting")
            sys.exit(1002)
        if self._profile_file.exists():
            _logger.warning(
                f"Profile {self._profile_name} already exists -> overwriting {self._profile_file}"
            )
        current_tree: types.Tree = self.__get_current_tree()
        with self._profile_file.open("w") as FILE:
            FILE.write(current_tree.model_dump_json(indent=2))

    def show_config(self) -> None:
        print(f"configuration file: {self.__config_file}")
        print("effective configuration:")
        print(self._config.model_dump_json(indent=2))
