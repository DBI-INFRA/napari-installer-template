import json
import os
import re
import select
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from copier import run_copy

TEMPLATE = Path(__file__).resolve().parent.parent
BASE = {'dist': 'napari-foo', 'repo': 'someone/napari-foo'}
FULL = {
    **BASE,
    'widget': 'Foo widget',
    'app_name': 'Foo App',
    'torch': True,
    'model_command': '-m napari_foo fetch-model',
    'model_desc': 'the Foo model (about 300 MB)',
    'shortcut_icon': 'napari_foo:resources/icon.png',
    'setup_icon': 'napari_foo/resources/icon.ico',
}
NON_ASCII = {
    **BASE,
    'widget': 'Z\u00e4hl widget',
    'app_name': 'Zellz\u00e4hlung',
    'comment': 'Z\u00e4hlt Zellen & Kerne <schnell>',
    'model_command': '-m napari_foo fetch-model',
    'model_desc': 'das Modell \U0001f642',
}
BOM = b'\xef\xbb\xbf'
SCRIPTS = [
    'installer/install.sh',
    'installer/install.ps1',
    'installer/windows/napari-foo.iss',
    'installer/smoke_test.py',
    'installer/README.md',
    '.github/workflows/installer.yml',
]


def render(tmp_path, **data):
    run_copy(str(TEMPLATE), tmp_path, data=data, defaults=True, quiet=True, vcs_ref='HEAD')
    return tmp_path


def has(tool):
    return pytest.mark.skipif(not shutil.which(tool), reason=f'needs {tool}')


def pwsh(command, check=False):
    return subprocess.run(['pwsh', '-NoProfile', '-c', command], check=check, capture_output=True, text=True)


def step_code(script):
    """the Python step inside a rendered install.sh or install.ps1"""
    text = script.read_text(encoding='utf-8')
    if script.suffix == '.sh':
        return text.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0] + '\n'
    return text.split("$StepCode = @'\n", 1)[1].split("\n'@\n", 1)[0] + '\n'


def step_config(script):
    return json.loads(re.search(r'^CONFIG = r"""(.*?)"""$', step_code(script), re.MULTILINE | re.DOTALL)[1])


def fake_python(path, log, real=False):
    """
    A stand-in for the environment's Python that records its arguments. With real, it runs the install step
    with this Python, so the step's own calls reach the stand-ins.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    run_step = f"if sys.argv[1:3] == ['-I', '-u']: os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])"
    path.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        f'with open({str(log)!r}, "a") as log: log.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        f'{run_step if real else ""}\n'
        'sys.exit(int(os.environ.get("FAKE_EXIT", "0")) if "napari_launcher" in sys.argv else 0)\n'
    )
    path.chmod(0o755)


def logged(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def make_install(root):
    """a folder as the install scripts leave it, with their marker"""
    (root / 'env' / 'lib').mkdir(parents=True)
    (root / 'env' / 'lib' / 'module.py').touch()
    (root / 'install.log').touch()
    (root / '.napari-foo-install').touch()
    return root


@pytest.fixture(params=[BASE, FULL, NON_ASCII], ids=['base', 'full', 'non-ascii'])
def rendered(request, tmp_path):
    return render(tmp_path, **request.param)


def test_files(rendered):
    files = sorted(p.relative_to(rendered).as_posix() for p in rendered.rglob('*') if p.is_file())
    assert set(SCRIPTS) <= set(files)
    assert 'installer/.copier-answers.yml' in files
    # the Python step goes into the install scripts, not into a file of its own
    assert not [name for name in files if 'install_step' in name or name.startswith('include/')]
    for name in SCRIPTS:
        text = (rendered / name).read_text()
        assert '[[' not in text and '[%' not in text, name


def test_napari_icon_only_without_setup_icon(tmp_path):
    base = render(tmp_path / 'a', **BASE)
    assert (base / 'installer/windows/icon.ico').is_file()
    # napari's license goes with its icon, in the repository and in the install folder
    notice = (base / 'installer/windows/icon-license.txt').read_text()
    assert (TEMPLATE / 'LICENSES/napari.txt').read_text() in notice
    assert 'Source: "icon-license.txt"; DestDir: "{app}"' in (base / SCRIPTS[2]).read_text()
    full = render(tmp_path / 'b', **FULL)
    assert not (full / 'installer/windows/icon.ico').exists()
    assert not (full / 'installer/windows/icon-license.txt').exists()
    iss = (full / SCRIPTS[2]).read_text()
    assert r'SetupIconFile=..\..\napari_foo\resources\icon.ico' in iss
    assert 'icon-license.txt' not in iss


def test_base_script_values(tmp_path):
    out = render(tmp_path, **BASE)
    sh = (out / 'installer/install.sh').read_text()
    config = step_config(out / 'installer/install.sh')
    assert config == {
        'dist': 'napari-foo',
        'env': 'NAPARI_FOO',
        'plugin': 'napari-foo',
        'widget': '',
        'app_name': 'napari-foo',
        'comment': 'napari-foo in napari',
        'shortcut_icon': '',
        'torch': False,
        'model_command': '',
        'model_desc': '',
        'launcher_spec': 'napari-launcher>=0.1,<0.2',
        'python_version': '3.12',
        'repo': 'someone/napari-foo',
        'branch': 'main',
    }
    assert 'NAPARI_FOO_HOME' in sh
    assert 'NAPARI_FOO_TORCH' not in sh and 'NAPARI_FOO_MODEL' not in sh
    assert 'run_step install\n' in sh and 'run_step uninstall || true\n' in sh
    assert 'UV_UNMANAGED_INSTALL="$ROOT/bin"' in sh
    ps1 = (out / 'installer/install.ps1').read_text()
    assert step_config(out / 'installer/install.ps1') == config
    assert '& $Python -I -u $Step install @StepArgs' in ps1
    assert '& $Python -I -u $Step uninstall' in ps1
    assert '--model' not in ps1.split("\n'@\n")[1]
    assert 'uv-x86_64-pc-windows-msvc.zip' in ps1
    assert 'astral.sh' not in ps1
    assert 'UNINSTALL' not in ps1
    iss = (out / SCRIPTS[2]).read_text()
    assert 'Parameters: "-m napari_launcher launch -w napari-foo"' in iss
    assert 'Parameters: "-m napari_launcher shortcut remove --name ""{#AppName}"""' in iss
    assert """' -Root "' + ExpandConstant('{app}') + '"'""" in iss
    assert 'SetupIconFile=icon.ico' in iss
    # the failure message points to the log, whichever step failed
    assert "'The end of ' + ExpandConstant('{app}\\install.log') + ' says what went wrong. '" in iss
    readme = (out / 'installer/README.md').read_text()
    url = 'https://raw.githubusercontent.com/someone/napari-foo/main/installer/install.sh'
    assert f'wget -qO- {url} | sh\n' in readme
    assert f'wget -qO- {url} | sh -s -- --uninstall\n' in readme
    assert '%LOCALAPPDATA%\\napari-foo' in readme and '~/.local/share/napari-foo' in readme
    assert 'PyTorch' not in readme and 'model' not in readme
    assert 'napari-launcher shortcut add -w napari-foo --name "napari-foo"' in readme
    assert (
        'powershell -ExecutionPolicy ByPass -c "& ([scriptblock]::Create((irm '
        'https://raw.githubusercontent.com/someone/napari-foo/main/installer/install.ps1))) -Uninstall"'
    ) in readme


def test_full_script_values(tmp_path):
    out = render(tmp_path, **FULL)
    sh = (out / 'installer/install.sh').read_text()
    assert 'NAPARI_FOO_TORCH' in sh and 'NAPARI_FOO_MODEL' in sh
    config = step_config(out / 'installer/install.sh')
    assert {key: config[key] for key in FULL if key in config} == {key: FULL[key] for key in FULL if key in config}
    ps1 = (out / 'installer/install.ps1').read_text()
    assert step_config(out / 'installer/install.ps1') == config
    assert "[ValidateSet('ask', 'yes', 'no')]" in ps1
    assert "if ($Model -ne 'ask') { $StepArgs += @('--model', $Model) }" in ps1
    iss = (out / SCRIPTS[2]).read_text()
    assert 'Parameters: "-m napari_launcher launch -w napari-foo ""Foo widget"""' in iss
    assert 'Name: "model"' in iss
    readme = (out / 'installer/README.md').read_text()
    assert 'Macs with an Intel processor' in readme
    assert 'download the Foo model (about 300 MB) straight away' in readme
    assert 'napari-launcher shortcut add -w napari-foo "Foo widget" --name "Foo App"' in readme


def test_app_id_is_stable(tmp_path):
    first = (render(tmp_path / 'a', **BASE) / SCRIPTS[2]).read_text()
    second = (render(tmp_path / 'b', **BASE) / SCRIPTS[2]).read_text()
    other = (render(tmp_path / 'c', **{**BASE, 'dist': 'napari-bar'}) / 'installer/windows/napari-bar.iss').read_text()
    app_id = next(line for line in first.splitlines() if line.startswith('AppId='))
    assert app_id in second and app_id not in other


def test_encodings(tmp_path):
    for i, data in enumerate([BASE, FULL, NON_ASCII]):
        out = render(tmp_path / str(i), **data)
        # Inno Setup reads the script as UTF-8 only with a byte order mark
        iss = (out / SCRIPTS[2]).read_bytes()
        assert iss.startswith(BOM) and not iss[3:].startswith(BOM)
        # PowerShell 5.1 reads a .ps1 without one as ANSI, and `irm | iex` fails with one
        ps1 = (out / 'installer/install.ps1').read_bytes()
        assert ps1.isascii(), data
    assert 'Zellz\u00e4hlung' in iss.decode('utf-8-sig')
    assert r'"app_name": "Zellz\u00e4hlung",' in ps1.decode()


def test_step_has_one_source(rendered):
    """both scripts carry the same Python step, the template's include/install_step.py with the answers in it"""
    sh, ps1 = rendered / 'installer/install.sh', rendered / 'installer/install.ps1'
    assert step_code(sh) == step_code(ps1)
    source = (TEMPLATE / 'include/install_step.py').read_text()
    before, after = re.split(r'\[\[.*?\]\]', source, flags=re.DOTALL)
    code = step_code(sh)
    assert code.startswith(before) and code.endswith(after)
    compile(code, 'install-step.py', 'exec')


def test_step_answers(tmp_path):
    """the answers reach the step unchanged, though install.ps1 is ASCII"""
    for i, data in enumerate([FULL, NON_ASCII]):
        out = render(tmp_path / str(i), **data)
        config = step_config(out / 'installer/install.ps1')
        expected = {'comment': f'{data["app_name"]} in napari', **data}
        assert {key: config[key] for key in expected if key in config} == {
            key: value for key, value in expected.items() if key in config
        }


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('dist', 'napari foo'),
        ('dist', '3d-foo'),
        ('widget', "Foo's widget"),
        ('app_name', 'Foo/App'),
        ('app_name', ' Foo'),
        ('app_name', 'Foo '),
        ('app_name', '.Foo'),
        ('app_name', 'Foo.'),
        ('app_name', 'con'),
        ('app_name', 'LPT1.txt'),
        ('comment', ''),
        ('repo', 'napari-foo'),
        ('branch', 'main"; rm -rf ~; "'),
        ('branch', '$(id)'),
        ('branch', '-x'),
        ('branch', 'a..b'),
        ('model_command', '-m foo; rm -rf ~'),
        ('model_command', '-m foo $(id)'),
        ('model_command', '-m foo > out'),
        ('model_command', '-m foo\nrm'),
        ('model_command', '-c print(1)'),
        ('setup_icon', 'icon.png'),
        ('app_id', '{0B5C0E3A-1D2B-4C1E-9F00-123456789ABC}'),
    ],
)
def test_rejects_unsafe_answers(tmp_path, key, value):
    with pytest.raises(ValueError, match=f"question '{key}'"):
        render(tmp_path, **{**BASE, key: value, **({'model_desc': 'the model'} if key == 'model_command' else {})})


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('dist', 'napari-3d'),
        ('app_name', 'Zellz\u00e4hlung 2.0'),
        ('app_name', 'Console'),
        ('branch', 'release/v1.2_x'),
        ('model_command', '-m napari_foo.fetch --model=cyto3 --url https://example.org/w:1'),
    ],
)
def test_accepts_answers(tmp_path, key, value):
    render(tmp_path, **{**BASE, key: value, **({'model_desc': 'the model'} if key == 'model_command' else {})})


def test_sh_uninstall(tmp_path):
    script = render(tmp_path / 'out', **BASE) / 'installer/install.sh'
    root = make_install(tmp_path / 'install [1]')
    run = subprocess.run(
        ['sh', script, '--uninstall'],
        check=False,
        env={**os.environ, 'NAPARI_FOO_HOME': str(root)},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert not root.exists()
    other = tmp_path / 'other'
    other.mkdir()
    run = subprocess.run(
        ['sh', script, '--uninstall'],
        check=False,
        env={**os.environ, 'NAPARI_FOO_HOME': str(other)},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1 and 'is not a napari-foo install' in run.stderr
    assert other.exists()


def test_sh_uninstall_removes_shortcuts(tmp_path):
    script = render(tmp_path / 'out', **{**BASE, 'app_name': 'Foo App'}) / 'installer/install.sh'
    for exit_code in ('0', '1'):
        root = make_install(tmp_path / f'install {exit_code}')
        log = tmp_path / f'calls {exit_code}'
        fake_python(root / 'env/bin/python', log, real=True)
        env = {**os.environ, 'NAPARI_FOO_HOME': str(root), 'FAKE_EXIT': exit_code}
        run = subprocess.run(['sh', script, '--uninstall'], check=False, env=env, capture_output=True, text=True)
        # the shortcuts not removed is no reason to keep the install
        assert run.returncode == 0, run.stderr
        assert not root.exists()
        assert logged(log) == [
            ['-I', '-u', str(root / 'install-step.py'), 'uninstall'],
            ['-m', 'napari_launcher', 'shortcut', 'remove', '--name', 'Foo App'],
        ]
        assert ('delete any Foo App shortcuts that are left by hand' in run.stdout) == (exit_code == '1')


def fake_uv(path, log):
    """a stand-in for uv, already the pinned version, that records its arguments"""
    path.parent.mkdir(parents=True, exist_ok=True)
    version = yaml.safe_load((TEMPLATE / 'copier.yml').read_text())['uv_version']['default']
    path.write_text(
        f'#!{sys.executable}\n'
        'import json, sys\n'
        f'if sys.argv[1:] == ["--version"]: print("uv {version}"); sys.exit()\n'
        f'with open({str(log)!r}, "a") as log: log.write(json.dumps(["uv", *sys.argv[1:]]) + "\\n")\n'
    )
    path.chmod(0o755)


def test_sh_install_runs_step(tmp_path):
    """install.sh up to the step, and the step with stand-ins for uv and napari-launcher"""
    script = render(tmp_path / 'out', **FULL) / 'installer/install.sh'
    root = tmp_path / 'install'
    log = tmp_path / 'calls'
    fake_uv(root / 'bin/uv', log)
    fake_python(root / 'env/bin/python', log, real=True)
    # as a run before left it
    (root / '.napari-foo-install').touch()
    env = {**os.environ, 'NAPARI_FOO_HOME': str(root), 'NAPARI_FOO_MODEL': '0'}
    run = subprocess.run(['sh', script], check=False, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    calls = logged(log)
    assert calls[0][:3] == ['uv', 'python', 'install'] and calls[1][:2] == ['uv', 'venv']
    assert calls[2] == ['-I', '-u', str(root / 'install-step.py'), 'install']
    assert calls[3][:6] == ['uv', 'pip', 'install', '--python', str(root / 'env/bin/python'), '--torch-backend']
    assert calls[5][:5] == ['-m', 'napari_launcher', 'shortcut', 'add', '--name']
    assert len(calls) == 6
    assert '==> Done. Start Foo App from the shortcut, or run:' in run.stdout
    assert not (root / 'install-step.py').exists() and (root / '.napari-foo-install').exists()

    # the step's exit code is the script's
    env['FAKE_EXIT'] = '1'
    run = subprocess.run(['sh', script], check=False, env=env, capture_output=True, text=True)
    assert run.returncode == 1 and 'but the shortcuts are not' in run.stderr
    assert not (root / 'install-step.py').exists()


def test_uv_windows_sha256_matches_release():
    answers = yaml.safe_load((TEMPLATE / 'copier.yml').read_text())
    version, pinned = answers['uv_version']['default'], answers['uv_windows_sha256']['default']
    url = f'https://github.com/astral-sh/uv/releases/download/{version}/uv-x86_64-pc-windows-msvc.zip.sha256'
    try:
        published = urllib.request.urlopen(url, timeout=30).read().decode()
    except urllib.error.HTTPError:
        # the release or its file does not exist
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f'no network: {e}')
    correct = published.split()[0]
    assert correct == pinned, f'set uv_windows_sha256 in copier.yml to {correct}'


@has('shellcheck')
def test_shellcheck(rendered):
    subprocess.run(['shellcheck', rendered / 'installer/install.sh'], check=True)


@has('actionlint')
def test_actionlint(rendered):
    subprocess.run(['actionlint', rendered / '.github/workflows/installer.yml'], check=True)


@has('pwsh')
def test_powershell_parses(rendered):
    script = rendered / 'installer/install.ps1'
    parse = (
        '$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('
        f"'{script}',[ref]$null,[ref]$e) | Out-Null; $e"
    )
    out = pwsh(parse, check=True)
    assert out.stdout.strip() == ''


def read_terminal(terminal, until, seen=b''):
    """what a pseudo-terminal shows, up to the text until, the end of its output, or a minute of silence"""
    while until not in seen and select.select([terminal], [], [], 60)[0]:
        try:
            chunk = os.read(terminal, 4096)
        except OSError:  # Linux: the other side is closed
            break
        if not chunk:
            break
        seen += chunk
    return seen


@pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo-terminal')
@pytest.mark.parametrize(('typed', 'downloads'), [('n', False), ('', True)])
def test_sh_model_question_on_terminal(tmp_path, typed, downloads):
    """as with `curl | sh`: the script comes through standard input, and the question goes to the terminal"""
    import fcntl
    import pty
    import termios

    script = render(tmp_path / 'out', **FULL) / 'installer/install.sh'
    root = tmp_path / 'install'
    log = tmp_path / 'calls'
    fake_uv(root / 'bin/uv', log)
    fake_python(root / 'env/bin/python', log, real=True)
    (root / '.napari-foo-install').touch()
    terminal, child_terminal = pty.openpty()

    def own_terminal():
        os.setsid()
        fcntl.ioctl(child_terminal, termios.TIOCSCTTY, 0)

    with script.open('rb') as stdin:
        sh = subprocess.Popen(
            ['sh'],
            stdin=stdin,
            stdout=child_terminal,
            stderr=child_terminal,
            env={**os.environ, 'NAPARI_FOO_HOME': str(root)},
            preexec_fn=own_terminal,  # noqa: PLW1509 (no threads here)
        )
    os.close(child_terminal)
    seen = read_terminal(terminal, b'[Y/n] ')
    assert b'[Y/n] ' in seen, seen.decode()
    os.write(terminal, typed.encode() + b'\n')
    seen = read_terminal(terminal, b'To uninstall:', seen)
    assert sh.wait(timeout=60) == 0, seen.decode()
    os.close(terminal)
    assert b'Download the Foo model (about 300 MB) now? Otherwise it downloads the first time' in seen
    assert (['-m', 'napari_foo', 'fetch-model'] in logged(log)) is downloads


@has('pwsh')
def test_powershell_step_code(tmp_path):
    """PowerShell reads the here-string as the same Python that install.sh writes"""
    out = render(tmp_path, **NON_ASCII)
    text = (out / 'installer/install.ps1').read_text()
    here_string = text[text.index("$StepCode = @'") : text.index("\n'@\n") + 4]
    written = tmp_path / 'step.py'
    pwsh(f"{here_string}\n[IO.File]::WriteAllText('{written}', $StepCode)", check=True)
    assert written.read_text() + '\n' == step_code(out / 'installer/install.sh')


@has('pwsh')
def test_powershell_install_runs_step(tmp_path):
    """install.ps1 up to the step, with -NoDesktop and -Model passed on to it"""
    script = render(tmp_path / 'out', **FULL) / 'installer/install.ps1'
    root = tmp_path / 'install [1]'
    log = tmp_path / 'calls'
    fake_uv(root / 'bin/uv.exe', log)
    fake_python(root / 'env/Scripts/python.exe', log)
    (root / '.napari-foo-install').touch()
    out = pwsh(f"& '{script}' -Root '{root}' -NoDesktop -Model no; exit $LASTEXITCODE")
    assert out.returncode == 0, out.stdout + out.stderr
    calls = logged(log)
    assert calls[0][:3] == ['uv', 'python', 'install'] and calls[1][:2] == ['uv', 'venv']
    assert calls[2] == ['-I', '-u', str(root / 'install-step.py'), 'install', '--no-desktop', '--model', 'no']
    assert not (root / 'install-step.py').exists()
    assert (root / 'install.log').exists()

    # the step's exit code is the script's, with no message of the script's own
    (root / 'env/Scripts/python.exe').write_text('#!/bin/sh\nexit 3\n')
    out = pwsh(f"& '{script}' -Root '{root}'; exit $LASTEXITCODE")
    assert out.returncode == 3 and 'error:' not in out.stdout
    assert not (root / 'install-step.py').exists()


@has('pwsh')
def test_powershell_uninstall(tmp_path):
    script = render(tmp_path / 'out', **BASE) / 'installer/install.ps1'
    # brackets would be wildcards without -LiteralPath
    root = make_install(tmp_path / 'install [1]')
    keep = tmp_path / 'install 1'
    keep.mkdir()
    out = pwsh(f"& '{script}' -Root '{root}' -Uninstall; exit $LASTEXITCODE")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not root.exists() and keep.exists()

    # as the uninstall one-liner runs it
    root = make_install(tmp_path / 'install [2]')
    one_liner = f"$env:NAPARI_FOO_HOME = '{root}'; & ([scriptblock]::Create((Get-Content -Raw '{script}'))) -Uninstall"
    out = pwsh(one_liner)
    assert out.returncode == 0, out.stdout + out.stderr
    assert not root.exists()

    out = pwsh(f"& '{script}' -Root '{keep}' -Uninstall; exit $LASTEXITCODE")
    assert out.returncode == 1 and 'is not a napari-foo install' in out.stdout
    assert keep.exists()

    # the step removes the shortcuts first
    root = make_install(tmp_path / 'install [3]')
    log = tmp_path / 'calls'
    fake_python(root / 'env/Scripts/python.exe', log)
    out = pwsh(f"& '{script}' -Root '{root}' -Uninstall; exit $LASTEXITCODE")
    assert out.returncode == 0, out.stdout + out.stderr
    assert logged(log) == [['-I', '-u', str(root / 'install-step.py'), 'uninstall']]
    assert not root.exists()


@has('pwsh')
@pytest.mark.skipif(sys.platform == 'win32' or os.geteuid() == 0, reason='needs a folder that cannot be emptied')
def test_powershell_uninstall_stops_part_way(tmp_path):
    script = render(tmp_path / 'out', **BASE) / 'installer/install.ps1'
    root = make_install(tmp_path / 'install')
    (root / 'env' / 'lib').chmod(0o555)
    try:
        out = pwsh(f"& '{script}' -Root '{root}' -Uninstall; exit $LASTEXITCODE")
    finally:
        (root / 'env' / 'lib').chmod(0o755)
    assert out.returncode == 1 and 'Close napari, then uninstall again' in out.stdout
    # the marker stays, so the uninstall can run again
    assert (root / '.napari-foo-install').exists()
    out = pwsh(f"& '{script}' -Root '{root}' -Uninstall; exit $LASTEXITCODE")
    assert out.returncode == 0 and not root.exists()


def test_smoke_test_compiles(rendered):
    compile((rendered / 'installer/smoke_test.py').read_text(), 'smoke_test.py', 'exec')
