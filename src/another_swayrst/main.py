import json
import logging
import os
import pathlib
import subprocess
import sys
import time
import typing

import i3ipc
import psutil
import pydantic.tools
import yaml

import another_swayrst.types as types

_logger = logging.getLogger(__name__)


class AnotherSwayrst:
    def __init__(self) -> None:
        self.config_dir, self.swayrst_profile_dir = self.get_dirs()
        config_file = self.config_dir.joinpath("another-swayrst.conf.yaml")
        if config_file.exists() and config_file.is_file():
            _logger.debug(f"loading config file: {config_file}")
            self.config = yaml.safe_load(config_file.read_text())
        else:
            _logger.info(f"config file: {config_file} not found")
            self.config = {
                "command_translation": {},
                "app_start_timeout": 30,
            }
            with config_file.open("w") as FILE:
                yaml.dump(self.config, FILE)

        self.i3ipc: i3ipc.Connection = i3ipc.Connection()

    def get_dirs(self) -> tuple[pathlib.Path, pathlib.Path]:
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
            return sway_config_folder, swayrst_profile_dir

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
        self.old_map_id_app, self.old_map_cmd_ids = self.get_map_of_apps(
            self.restore_tree
        )
        self._start_missing_apps()
        self._wait_until_apps_started(timeout=self.config["app_start_timeout"])
        self.move_all_apps_to_scratchpad()
        self.recreate_workspaces()
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
            if node["type"] not in ["con", "floating_con"]:
                _logger.warning(f"Unexpected node type found: {node['type']}")
            if len(node["nodes"]) == 0:
                command = psutil.Process(node["pid"]).cmdline()
                container = types.AppContainer(
                    id=node["id"],
                    command=command,
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
        return_element: list[types.Workspace] = []
        for node in nodes:
            if node["type"] != "workspace":
                _logger.warning(f"Unexpected node type found: {node['type']}")
            x = self.__parse_tree_container_elements(node["nodes"])
            floating_cons = self.__parse_tree_container_elements(node["floating_nodes"])
            if len(x) + len(floating_cons) == 0 and node["name"] != "__i3_scratch":
                _logger.warning("Workspace without apps found")
            workspace_number = None
            if "num" in node:
                workspace_number = node["num"]
            workspace = types.Workspace(
                id=node["id"],
                name=node["name"],
                containers=x,
                floating_containers=floating_cons,
                number=workspace_number,
                layout=node["layout"],
            )
            return_element.append(workspace)
        return return_element

    def __parse_tree_output_elements(self, nodes) -> list[types.Output]:
        return_element: list[types.Output] = []
        for node in nodes:
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

    def get_map_of_apps(
        self, tree: types.Tree
    ) -> tuple[dict[int, types.AppContainer], dict[str, list[int]]]:
        map_id_app: dict[int, types.AppContainer] = {}
        map_commands_id: dict[str, list[int]] = {}

        for output in tree.outputs:
            for workspace in output.workspaces:
                x = self.__iterate_over_containers(workspace.containers)
                for key, value in x.items():
                    if key in map_id_app:
                        _logger.warning(f"duplicate id found: {key}")
                    map_id_app[key] = value
                for con in workspace.floating_containers:
                    if isinstance(con, types.AppContainer):
                        if con.id in map_id_app:
                            _logger.warning(f"duplicate id found: {con.id}")
                        map_id_app[con.id] = con
                    else:
                        _logger.warning("other type than App in floating containers")

        for id, con in map_id_app.items():
            cmd = con.command
            cmd_str = " ".join(cmd)
            if cmd_str not in map_commands_id:
                map_commands_id[cmd_str] = []
            map_commands_id[cmd_str].append(id)

        return map_id_app, map_commands_id

    def _get_missing_apps(self) -> list[dict[str, typing.Any]]:
        if self.old_map_cmd_ids is None:
            _logger.error("no map for cmd to ids to restore available")
            sys.exit(1004)

        new_map_id_app, new_map_cmd_ids = self.get_map_of_apps(self.get_current_tree())

        missing_apps: list[dict[str, typing.Any]] = []
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

    def _start_missing_apps(self):
        missing_apps = self._get_missing_apps()
        for app_info in missing_apps:
            amount: int = app_info["amount"]
            cmd: list[str] = app_info["cmd"]
            for _ in range(amount):
                if cmd[0] in self.config["command_translation"]:
                    cmd[0] = self.config["command_translation"][cmd[0]]
                subprocess.Popen(cmd, cwd=pathlib.Path.home())

    def _wait_until_apps_started(self, timeout: int) -> None:
        """Wait until all missing apps are started, or timeout reached"""

        missing_apps_count = len(self._get_missing_apps())
        timeout_counter = 0
        while missing_apps_count > 0 and timeout_counter < timeout:
            time.sleep(1)
            timeout_counter += 1
            missing_apps_count = len(self._get_missing_apps())

        if timeout_counter >= timeout:
            _logger.warning(f"not all missing apps started after timeout of {timeout}s")

    def _get_old_to_new_map(self) -> dict[int, int]:
        map_old_to_new_id = {}
        new_map_id_app, new_map_cmd_ids = self.get_map_of_apps(self.get_current_tree())

        for cmd, ids in self.old_map_cmd_ids.items():
            for old_id in ids:
                if cmd in new_map_cmd_ids:
                    if len(new_map_cmd_ids[cmd]) > 0:
                        new_id = new_map_cmd_ids[cmd].pop()
                        map_old_to_new_id[old_id] = new_id

        return map_old_to_new_id

    def move_all_apps_to_scratchpad(self):
        new_map_id_app, new_map_cmd_ids = self.get_map_of_apps(self.get_current_tree())
        for id in new_map_id_app.keys():
            app = self.i3ipc.get_tree().find_by_id(id)
            if app is not None:
                app.command("move scratchpad")

    def recreate_workspaces(self):
        map_old_to_new_id = self._get_old_to_new_map()
        for output in self.restore_tree.outputs:
            if output.name != "__i3":
                for workspace in output.workspaces:
                    # self.i3ipc.command(f"workspace number {workspace.number}")
                    # for app_workspace in self.i3ipc.get_tree().workspaces():
                    #     if app_workspace.num == workspace.number:
                    #         app_workspace.command(f"layout {workspace.layout}")
                    #         break
                    last_app = None
                    if workspace.number is None:
                        _logger.warning("workspace without number found")
                    else:
                        for container in workspace.containers:
                            old_id = self.__get_first_app_id(container)
                            new_id = map_old_to_new_id[old_id]
                            app = self.i3ipc.get_tree().find_by_id(new_id)
                            if app is not None:
                                self.__execute_command(
                                    app=app,
                                    command=f"move container to workspace number {workspace.number}",
                                )
                                self.__execute_command(app=app, command="floating off")
                                self.__execute_command(
                                    app=app, command=f"layout {workspace.layout}"
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
                            new_con_id = map_old_to_new_id[con.id]
                            app = self.i3ipc.get_tree().find_by_id(new_con_id)
                            if app is not None:
                                self.__execute_command(
                                    app=app,
                                    command=f"move container to workspace number {workspace.number}",
                                )

    def __get_first_app_id(
        self, container: types.Container | types.AppContainer
    ) -> int:
        if isinstance(container, types.AppContainer):
            return container.id
        else:
            return self.__get_first_app_id(container.sub_containers[0])

    def __execute_command(self, command: str, app: i3ipc.Con | None = None) -> None:
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
    ):
        first_app = True
        for container in containers:
            old_id = self.__get_first_app_id(container)
            new_id = map_old_to_new_id[old_id]
            app = self.i3ipc.get_tree().find_by_id(new_id)
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
                    # app.command(f"layout {layout}")

        for container in containers:
            if isinstance(container, types.Container):
                self.recreate_containers(
                    containers=container.sub_containers,
                    workspace_number=workspace_number,
                    map_old_to_new_id=map_old_to_new_id,
                    layout=container.layout,
                )

        # if isinstance(container, types.AppContainer):
        #     new_app_id = map_old_to_new_id[container.id]
        #     app = self.i3ipc.get_tree().find_by_id(new_app_id)
        #     if app is not None:
        #         app.command(f"move container to workspace number {workspace.number}")
        #         app.command(f"floating off")
        #         # app.command("split toggle")
        #         # app.command("split toggle")
        #         # app.command(f"layout {layout}")

        #         match layout:
        #             case "splitv" | "splith":
        #                 app.command(f"{layout}")
        #             case "tabbed" | "stacking":
        #                 app.command("split toggle")
        #                 app.command(f"layout {layout}")
        #         # print(layout)
        #         last_app = app
        # elif isinstance(container, types.Container):
        #     for con in container.sub_containers:
        #         # if last_app is not None:
        #         #     last_app.command(f"{layout}")
        #         self.recreate_container(
        #             workspace,
        #             con,
        #             map_old_to_new_id,
        #             container.layout,
        #         )
        # return last_app
