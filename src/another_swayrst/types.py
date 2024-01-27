import typing

import pydantic


class TreeElement(pydantic.BaseModel):
    """Base class for all tree elements"""

    id: int


class AppContainer(TreeElement):
    """A container which represent an Application"""

    command: list[str]
    layout: str


class Container(TreeElement):
    """A container which contains multiple other Container and AppContainer"""

    sub_containers: list["Container| AppContainer"]
    layout: str


class Workspace(TreeElement):
    name: str
    containers: list[Container | AppContainer]


class Output(TreeElement):
    name: str
    workspaces: list[Workspace]


class Tree(pydantic.BaseModel):
    outputs: list[Output]
