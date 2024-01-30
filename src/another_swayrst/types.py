import pathlib

import pydantic


class AnotherSwayrstConfig(pydantic.BaseModel):
    """Configuration of the tool."""

    command_translation: dict[str, str] = {}
    app_start_timeout: int = 30
    profile_dir: pathlib.Path
    start_missing_apps: bool = True


class TreeElement(pydantic.BaseModel):
    """Base class for all tree elements"""

    id: int


class AppContainer(TreeElement):
    """A container which represent an Application"""

    command: list[str]
    width: int
    height: int


class Container(TreeElement):
    """A container which contains multiple other Container and AppContainer"""

    sub_containers: list["Container| AppContainer"]
    layout: str


class Workspace(TreeElement):
    """A representation of a workspace."""

    name: str
    containers: list[Container | AppContainer]
    floating_containers: list[AppContainer | Container]
    number: int | None
    layout: str


class Output(TreeElement):
    """A representation of an output."""

    name: str
    workspaces: list[Workspace]


class Tree(pydantic.BaseModel):
    """Root node of the tree."""

    outputs: list[Output]
