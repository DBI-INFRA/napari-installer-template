# Design choices and alternatives

## Why a thin online installer with uv

PyTorch is the hardest part of a napari plugin installer. The right build depends on the user's GPU
and driver, and each CUDA build is over 2 GB. `uv pip install --torch-backend auto` picks the build
on the user's machine: it reads the NVIDIA driver version, or looks for an AMD GPU on Linux, and
installs from the matching PyTorch index. That covers the CUDA versions the driver supports, ROCm,
Apple MPS and CPU. `--torch-backend` exists only in the `uv pip` interface, not in `uv add` or
`uv sync`. Users can override the choice with the scripts' `<ENV>_TORCH` variable, for example
`cpu` or `cu128`. `<ENV>` is the plugin's PyPI name in capitals, with `_` for `-`.

Further reasons:
- The plugin comes from PyPI, the same package that `pip install` users get, so there is nothing
  else to build.
- uv downloads its own Python (python-build-standalone), so the machine needs no Python. Python,
  the package cache and the environment all live in the install folder, and any Python already on
  the machine stays untouched.
- Updating means running the installer again.
- The setup `.exe` is a few MB, so CI can attach it to every release.
- napari-launcher makes the shortcuts, so conda or pip users can add the same shortcut to their own
  environment with `napari-launcher shortcut add -w <plugin>`.

The costs: the installer needs internet access, and the result depends on what PyPI has at install
time. `<ENV>_SPEC` can pin the plugin version for a reproducible install, and the template pins the
uv version.

## What is verified

- uv: on macOS and Linux, `install.sh` runs uv's own installer for the pinned version, downloaded
  over HTTPS from astral.sh. That installer checks the sha256 of the uv archive it fetches. On
  Windows, `install.ps1` downloads uv's release zip from GitHub and checks it against the sha256
  pinned in `copier.yml`.
- Python: uv checks the Python builds it downloads against checksums built into uv.
- libxcb-cursor, which the installer adds on Linux, is a conda-forge build pinned by its sha256.
- napari, the plugin and napari-launcher come from PyPI over HTTPS, and PyTorch from PyTorch's
  index. Their hashes are not pinned. napari and the plugin are the newest releases that fit
  together. napari-launcher is the newest release in the range set by `launcher_spec`.
- The install commands download the scripts from the branch you named, so they run whatever is on
  that branch at the time.
- The setup `.exe` contains the `install.ps1` of the commit it was built from. It still installs
  the newest release of the plugin on PyPI, not the version of the tag.
- Neither the setup nor the scripts are signed. See "Distribution caveats" below.

## How others do it

| Approach | Used by | GPU build chosen | Offline | Fits when |
|---|---|---|---|---|
| uv + `--torch-backend auto` (this template) | — | at install, per machine | no | the plugin is on PyPI, with or without torch |
| [conda constructor](https://github.com/conda/constructor) (`.exe`, `.pkg` or `.sh` of a fixed conda env) | napari's own bundle, napari-phasors | when the installer is built | yes | all dependencies are on conda-forge, and there is no GPU torch |
| micromamba + uv, with Inno Setup or an `.app` | SenoQuant | fixed per platform, for example one CUDA version on Windows | no | — |
| PyInstaller or briefcase (frozen app) | — | when built | yes | rarely for napari: torch, Qt and napari freeze into several GB, and the plugin manager can't install into a frozen app |
| pixi or pixi-pack | — | when packed | yes | the team already uses conda; the GPU build is fixed when packed, as with constructor |

Choose constructor instead when an offline installer matters more than GPU support and every
dependency is on conda-forge. It is napari's own route, and it makes native installers that can be
signed. With torch, it would need one installer per GPU flavour.

## How the two scripts share the work

`install.sh` (POSIX sh) and `install.ps1` (Windows PowerShell 5.1 and later) do only what has to
happen before the install's own Python exists, or while it must not run. They download and check
uv, install Python, create the environment, check the marker file, and delete the install folder. A
running `python.exe` can't delete its own folder on Windows.

Everything else is one Python program, `include/install_step.py`. It installs the packages with uv,
adds libxcb-cursor on Linux, adds the shortcuts, asks about the model and downloads it, and prints
the closing message. On uninstall, it removes the shortcuts while the environment still exists.
copier includes the program in both scripts, so each script stays a single file that the one-liners
can download. The script writes it into the install folder as `install-step.py`, runs it with the
environment's Python and deletes it again.

The program reads the `<ENV>_*` variables itself, and `install.ps1` passes `-NoDesktop` and `-Model`
on as arguments. The template's answers are in the program as JSON, with `\uXXXX` for characters
that are not ASCII, so `install.ps1` stays ASCII. `tests/test_install_step.py` runs the program
with stand-ins for uv and napari-launcher.

## Problems the scripts handle

- **`curl | sh` cut short** would run half a script. All the work is in functions, called on the
  last line. The model question goes to `/dev/tty` (`CONIN$` and `CONOUT$` on Windows), since
  standard input is the script.
- **`rm -rf` of a folder the user pointed at**: install and uninstall need the marker file
  `.<dist>-install` in the folder. The setup writes it before it calls `install.ps1`.
- **Windows: torch after Qt** crashes with a DLL error
  ([pytorch#166628](https://github.com/pytorch/pytorch/issues/166628)). napari-launcher imports
  torch first.
- **Windows: admin rights**: the install is per user, in `%LOCALAPPDATA%`
  (`PrivilegesRequired=lowest`), so napari's plugin manager can write to the environment.
  `uv python install --no-bin --no-registry` keeps that Python off PATH and out of the `py`
  launcher.
- **Windows PowerShell 5.1 started from PowerShell 7** inherits PowerShell 7's `PSModulePath`.
  This happens when the setup or the one-liner runs from a `pwsh` terminal, or in a `shell: pwsh`
  CI step. Windows PowerShell then loads PowerShell 7's copies of its built-in modules, which fail
  with errors such as "The member AuditToString is already present". Commands from those modules,
  such as `Get-FileHash` and `Expand-Archive`, stop working. `install.ps1` removes the PowerShell 7
  folders from `PSModulePath` before anything else, and napari-launcher does the same for the
  PowerShell it runs.
- **Windows: errors from the setup**: the setup runs `install.ps1` in a console window of its own.
  The script and its Python program record their messages and any error in `install.log` in the
  install folder. The setup's
  error message points to that file, and CI prints it.
- **Windows PowerShell 5.1 drops empty arguments** to native commands, so `install.ps1` never
  passes one.
- **Inno Setup can't abort in `ssPostInstall`**. Instead, the exit code of `install.ps1` becomes
  the setup's exit code through `GetCustomSetupExitCode`, so silent installs and CI see failures.
- **Linux: Qt 6 needs `libxcb-cursor0`**, which stock Ubuntu desktops lack ("could not load the Qt
  platform plugin xcb"). No PyPI package has it. The installer copies conda-forge's build into
  `PyQt6/Qt6/lib`, where Qt finds it through its `$ORIGIN` RUNPATH. It unpacks the package with
  `zstandard` in a throwaway `uv run --with` environment.
- **Intel Macs**: PyTorch stopped publishing x86_64 macOS wheels after 2.2, so a torch plugin that
  needs a newer torch fails to install there.
- **CI**: `runner.temp` isn't available in a job-level `env:`, so the workflow sets the install
  folder through `$GITHUB_ENV`. Qt's `offscreen` platform has no OpenGL, so the smoke test runs
  under `aganders3/headless-gui` or `xvfb-run`.
- **shellcheck SC2218** reports functions as used before they are defined, although every function
  is defined before the last line calls it. A comment disables it.
- **Names with quotes, `$`, `%` or braces** would break the generated string literals in the
  setup script, the workflow and the README's commands, so the template's questions refuse them.

napari-launcher handles the shortcut problems: a desktop in OneDrive, `pythonw.exe`, GNOME's trust
flag and the macOS desktop link.

## Distribution caveats

- **Windows SmartScreen** warns about an unsigned `.exe` ("Windows protected your PC", then More
  info, then Run anyway). The generated README says so. The PowerShell one-liner shows no warning,
  since files fetched by PowerShell or curl carry no Mark of the Web. Signing gives a named
  publisher and a reputation that carries across versions, but no instant trust: even EV-signed
  files build reputation from zero. The options are compared in
  [Microsoft's overview](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options):
  - [SignPath Foundation](https://signpath.org/) is free for open source and signs from GitHub
    Actions. The application takes some effort.
  - [Azure Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/overview)
    has a monthly fee and is made for CI. Who can sign up depends on the country and on whether
    you apply as a person or an organisation, so check Microsoft's terms or ask your institution.
  - The institution's own certificate, for example through its research network (GÉANT TCS or
    HARICA for research and education bodies). The key is usually on a hardware token, so signing
    happens outside CI.
  - An OV certificate from a certificate authority, with the key on a token or in the authority's
    cloud HSM.

  Signing in CI is one step in the `windows-setup` job, after the `.exe` is built.
- **macOS Gatekeeper** blocks a downloaded unsigned `.app`, `.pkg` or `.dmg`. Shipping one needs an
  Apple Developer ID and notarisation. `curl … | sh` sets no quarantine flag, so the scripted
  install and the `.app` it creates run without prompts. That is why there is no macOS download.
- **Linux**: there is nothing to sign. The one-liner works on any glibc distribution that uv's
  Python supports.
- **The plugin manager inside the installed napari** installs other plugins with pip into the same
  environment. A plugin that pins a different torch can replace the GPU build with PyPI's default.
  Running the installer again restores it.
- **Uninstall** removes the install folder and the shortcuts. Caches outside the folder stay, for
  example model weights in `~/.cache` or `~/.cellpose`. Say in the README where the plugin keeps
  them.

## Extending

- **Offline or air-gapped sites**: run the script on a machine with internet access, then copy the
  install folder to the same path on the offline machine, since the environment holds absolute
  paths. Then add the shortcut there with
  `<install folder>/env/bin/python -m napari_launcher shortcut add -w <plugin> --name <name>`
  (on Windows, `env\Scripts\python.exe`).
- **Pin a release**: set `<ENV>_SPEC="<dist>==1.2.3"`.

## Maintaining the template

Renovate keeps most pinned values up to date. Its configuration is in `.github/renovate.json5`.
Once the repository is on GitHub, install the [Renovate app](https://github.com/apps/renovate) for it.
Renovate then opens pull requests once a month:

- **CI dependencies**, in one pull request: the actions in `.github/workflows/test.yml`,
  `ACTIONLINT_VERSION`, and the gl-ci-helpers commit in the "Install Windows OpenGL" steps of both
  workflows. In `template/.github/workflows/installer.yml.jinja`, the actions stay on major tags such
  as `@v7`, and Renovate only proposes a new major.
- **uv**: `uv_version` in `copier.yml`, in its own pull request. Renovate cannot update
  `uv_windows_sha256`. Until it is updated, `test_uv_windows_sha256_matches_release` fails and prints
  the hash to paste. The hash is also in the `.sha256` file that uv publishes next to
  `uv-x86_64-pc-windows-msvc.zip`. `install.sh` downloads uv's installer for `uv_version`, so it needs
  no change of its own.

These values need a bump by hand:

- **libxcb-cursor**: the conda-forge file names and sha256 values in `XCB_CURSOR` in
  `include/install_step.py`, for `linux-64` and `linux-aarch64`. Renovate's conda datasource knows
  versions but not build strings, so it cannot produce the file name. Find both in
  `https://api.anaconda.org/package/conda-forge/xcb-util-cursor/files` (`basename` and `sha256`).
  `test_xcb_cursor_matches_conda_forge` checks the pinned files against that list and prints the
  correct values.
- **Ubuntu container**: the `ubuntu:24.04` image of the "Ubuntu desktop libraries" jobs in both
  workflows. Moving to a new LTS can change the names of the packages the job installs.
- **napari-launcher**: `test.yml` installs it from its GitHub repository through the
  `NAPARI_*_LAUNCHER` variables. Once napari-launcher is on PyPI, remove them, so that CI installs
  what users get. When napari-launcher releases a version with a changed command line, update
  `include/install_step.py`, the `.iss` and `launcher_spec` in `copier.yml`.
