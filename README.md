# another-swayrst

Inspired by swayrst from Nama <https://github.com/Nama/swayrst/blob/main/README.md>.
Restore workspaces in sway to displays, optional starts applications, move already open windows to their workspace and restores the layout and size of the windows.

## Setup

1. Download
    * From [AUR] -> comming soon.
    * From [Relaeses](https://github.com/afaul/another-swayrst/releases)
        * `unzip` and install e.g. with [pdm](https://pdm-project.org/latest/) `pdm install`
1. Setup your wanted layout (outputs, workspaces and windows).
1. Run `another-swayrst save <profilename>` to save.
1. Repeat with another `profilename` for different setups.
1. Run `another-swayrst load <profilename>` to restore.

## Command Line Options

It is possible to modify the behavior of `another-swayrst` with commandline options and with a config file.

The syntax is: `another-swayrst [<OPTIONS>] save|load|show-config <profilename>`

Available Options are:

| Option | Values | Description |
| --- | --- | --- |
| -v, --log-level | CRITICAL, FATAL, ERROR, WARNING, INFO, DEBUG | Verbosity level of the application, default: `WARNING` |
| -c, --config-file | FILE | Config file to use. |
| --save-current-config | None | Save the current configuration as json-file. |
| --profile-dir | DIRECTORY | Where to search for / save the layout |
| --start-missing-apps, --no-start-missing-apps | None |  (Not) Start the missing apps automatically. |
| --command-translation | command_A command_B | Translate command A into B when                               starting missing apps. (Necessary since some applications are listed with different name in ps.) |
| --help | None | Show help message and exit. |

## Development

* Windows are matches based on their executing command in `ps`. If multiple windows are available a secondary match based on the window title is tried.
* The information about the windows are gathered from `swaymsg -t get_tree` and `ps`.

## References

* A list of all commands of the ipc service: <https://i3wm.org/docs/userguide.html#list_of_commands>
