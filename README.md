# napari-installer-template

A [copier](https://copier.readthedocs.io/) template that adds a one-step installer to a napari
plugin repository, for people who have no Python.

| Platform | User runs | Gets |
|---|---|---|
| Windows | `<dist>-setup.exe` from the GitHub release, or a PowerShell one-liner | Start menu and desktop shortcuts; the setup also adds an entry in Settings > Apps |
| macOS | `curl … install.sh \| sh` | `~/Applications/<name>.app` and a link to it on the desktop |
| Linux | `curl … install.sh \| sh`, or the same with `wget` | an app menu entry and a desktop launcher |

Each installer downloads a pinned [uv](https://docs.astral.sh/uv/) into a folder of its own. uv
installs Python in that folder, then napari, the plugin from PyPI and
[napari-launcher](https://github.com/jeskowagner/napari-launcher) into a virtual environment. For
plugins that need PyTorch, `uv pip install --torch-backend auto` picks the build for the machine's
GPU: CUDA, ROCm, Apple MPS or CPU. napari-launcher then adds a shortcut that opens napari with the
plugin's widget docked. Uninstalling deletes the folder and the shortcuts.

The plugin needs no code changes, only a release on PyPI. The repository must be public, because
the install commands download the scripts from `raw.githubusercontent.com`.

## Use

From the plugin's repository root, run:

```bash
uvx copier copy gh:jeskowagner/napari-installer-template .
```

copier asks about the plugin, its widget and the shortcut, and shows a help text with each
question.

If you give a command that downloads model weights (`model_command`), the installers offer to run
it at install time. The setup has a tick box for it, and the scripts ask a yes or no question in
the terminal. The command must exit with a non-zero code when the download fails.

The template writes these files:

```
installer/install.sh               installer for macOS and Linux, in POSIX sh
installer/install.ps1              installer for Windows PowerShell
installer/windows/<dist>.iss       Inno Setup script for the setup .exe, which runs install.ps1
installer/windows/icon.ico         napari's icon, used when setup_icon is empty
installer/windows/icon-license.txt napari's license, which covers that icon
installer/windows/.gitignore       keeps Inno Setup's Output folder out of git
installer/smoke_test.py            opens the installed napari, checks that the widget is docked,
                                   and closes it
installer/README.md                install instructions for the plugin's users
installer/.copier-answers.yml      your answers, for copier update
.github/workflows/installer.yml    tests the scripts and the setup, and attaches the setup to
                                   the release of a v* tag
```

Both install scripts contain the same Python program, which does the work once the script has set
up Python. [docs/design.md](docs/design.md) explains how they share it.

Then:

1. Put the instructions from `installer/README.md` in the plugin's README, or link to that file.
2. Commit the files, then open a pull request or push to the branch you named. Either runs the
   installer workflow. It installs the plugin from the checkout with the scripts on Windows,
   macOS, Linux and a bare Ubuntu container, then builds, installs and uninstalls the setup.
   Nothing on Windows can be checked from macOS or Linux, so the workflow's `windows-setup` job
   is the first real run of the setup.
3. Push a `v*` tag to attach the setup to that tag's GitHub release.

The setup installs the newest release of the plugin on PyPI, not the version of the tag it was
built from. Put the release on PyPI before you push the tag.

The workflow also runs once a week, because new releases of napari, PyTorch or uv can break an
install that worked before.

To bring in fixes to the template, such as a newer uv, run:

```bash
uvx copier update -a installer/.copier-answers.yml
```

## Test locally

In the commands below, `<ENV>` is the plugin's PyPI name in capitals, with `_` for `-`. For
example, it is `NAPARI_FOO` for `napari-foo`. The top of each script lists all its environment
variables.

```bash
H=$(mktemp -d)
HOME=$H <ENV>_SPEC=$PWD sh installer/install.sh                        # installs this checkout
HOME=$H $H/.local/share/<dist>/env/bin/python installer/smoke_test.py  # opens a window briefly
HOME=$H sh installer/install.sh --uninstall
```

## Not covered

The template makes no offline installer, no signed or notarised builds, and no macOS `.pkg` or
`.dmg`. [docs/design.md](docs/design.md) explains why the installer works this way, compares it
with other approaches, and describes what signing takes. Its last section lists the pinned versions
to bump when maintaining the template.

## License

The template, and the files it writes into a plugin repository, are under the
[0BSD license](LICENSE). You may use, change and share them under any license, without
attribution. The one exception is napari's icon.

napari's icon, `installer/windows/icon.ico`, comes from [napari](https://github.com/napari/napari)
and is under napari's BSD 3-Clause license ([LICENSES/napari.txt](LICENSES/napari.txt)). The
template writes that license next to the icon as `icon-license.txt`, and the setup installs both.
If you set `setup_icon`, the template writes neither file.
