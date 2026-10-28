"""the Python step that install.sh and install.ps1 share, run directly with fakes for uv and napari-launcher"""

import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent
SOURCE = TEMPLATE / 'include/install_step.py'
CONFIG = {
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
FULL = {
    **CONFIG,
    'widget': 'Foo widget',
    'app_name': 'Foo App',
    'shortcut_icon': 'napari_foo:resources/icon.png',
    'torch': True,
    'model_command': '-m napari_foo fetch-model',
    'model_desc': 'the Foo model',
}
# records each call as a JSON line in calls.jsonl next to it; fails when FAKE_FAIL is part of its arguments
FAKE = """#!{python}
import json, os, sys
with open({log!r}, 'a') as log:
    log.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')
fail = os.environ.get('FAKE_FAIL')
sys.exit(1 if fail and fail in ' '.join(sys.argv[1:]) else 0)
"""


def load_step():
    spec = importlib.util.spec_from_file_location('install_step', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def step(monkeypatch):
    module = load_step()
    monkeypatch.setattr(module, 'WINDOWS', False)
    monkeypatch.setattr(module, 'COLOUR', False)
    # Linux only, and tested on its own
    monkeypatch.setattr(module, 'add_xcb_cursor', lambda config, uv: None)
    return module


def fake(path, log):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE.format(python=sys.executable, log=str(log)))
    path.chmod(0o755)


@pytest.fixture
def root(tmp_path):
    """an install folder with fakes for uv and the environment's Python"""
    root = tmp_path / 'install'
    fake(root / 'bin/uv', tmp_path / 'calls.jsonl')
    fake(root / 'env/bin/python', tmp_path / 'calls.jsonl')
    return root


def calls(root):
    log = root.parent / 'calls.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_install_command(step):
    uv, python = Path('/i/bin/uv'), Path('/i/env/bin/python')
    command = step.install_command(CONFIG, {}, uv, python)
    assert command == [
        str(uv),
        'pip',
        'install',
        '--python',
        str(python),
        '--upgrade',
        'napari[all]',
        'napari-foo',
        'napari-launcher>=0.1,<0.2',
    ]
    environ = {
        'NAPARI_FOO_SPEC': 'napari-foo==1.2',
        'NAPARI_FOO_LAUNCHER': '/src/napari-launcher',
        'NAPARI_FOO_TORCH': 'cpu',
    }
    command = step.install_command(CONFIG, environ, uv, python)
    assert command[-2:] == ['napari-foo==1.2', '/src/napari-launcher'] and '--torch-backend' not in command


def test_install_command_torch(step):
    command = step.install_command(FULL, {}, 'uv', 'python')
    assert command[5:9] == ['--torch-backend', 'auto', '--upgrade', 'napari[all]']
    command = step.install_command(FULL, {'NAPARI_FOO_TORCH': 'cpu', 'NAPARI_FOO_SPEC': ''}, 'uv', 'python')
    assert command[5:7] == ['--torch-backend', 'cpu'] and 'napari-foo' in command


def test_shortcut_command(step):
    command = step.shortcut_command(CONFIG, {}, 'python')
    assert command == [
        *('python', '-m', 'napari_launcher', 'shortcut', 'add'),
        *('--name', 'napari-foo', '--comment', 'napari-foo in napari', '-w', 'napari-foo'),
    ]
    command = step.shortcut_command(FULL, {}, 'python', no_desktop=True)
    assert command[-6:] == ['--icon', 'napari_foo:resources/icon.png', '--no-desktop', '-w', 'napari-foo', 'Foo widget']
    assert '--no-desktop' in step.shortcut_command(CONFIG, {'NAPARI_FOO_NO_DESKTOP': '1'}, 'python')
    assert '--no-desktop' not in step.shortcut_command(CONFIG, {'NAPARI_FOO_NO_DESKTOP': '0'}, 'python')


def test_xcb_cursor_folder(step, tmp_path):
    qt_lib = tmp_path / 'PyQt6/Qt6/lib'
    # no PyQt6, so nothing to add it to
    assert step.xcb_cursor_folder(tmp_path, find_library=lambda name: None) is None
    qt_lib.mkdir(parents=True)
    assert step.xcb_cursor_folder(tmp_path, find_library=lambda name: None) == qt_lib
    # the system has it
    assert step.xcb_cursor_folder(tmp_path, find_library=lambda name: 'libxcb-cursor.so.0') is None
    # an earlier run added it
    (qt_lib / 'libxcb-cursor.so.0').touch()
    assert step.xcb_cursor_folder(tmp_path, find_library=lambda name: None) is None


def test_add_xcb_cursor(root, monkeypatch, capsys):
    step = load_step()
    folder = root / 'env/lib/python3.12/site-packages/PyQt6/Qt6/lib'
    monkeypatch.setattr(step, 'xcb_cursor_folder', lambda site_packages: folder)
    step.add_xcb_cursor(CONFIG, root / 'bin/uv')
    assert calls(root) == [
        [
            *('uv', 'run', '--quiet', '--no-project', '--isolated', '--python', '3.12', '--with', 'zstandard'),
            *('python', '-I', str(SOURCE), 'xcb-cursor', str(folder)),
        ]
    ]
    assert capsys.readouterr().out == ''
    monkeypatch.setenv('FAKE_FAIL', 'xcb-cursor')
    step.add_xcb_cursor(CONFIG, root / 'bin/uv')
    assert 'Could not add libxcb-cursor; if napari does not open, run: sudo apt install libxcb-cursor0' in (
        capsys.readouterr().out
    )


def test_xcb_cursor_matches_conda_forge():
    url = 'https://api.anaconda.org/package/conda-forge/xcb-util-cursor/files'
    try:
        files = json.loads(urllib.request.urlopen(url, timeout=30).read())
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f'no network: {e}')
    files = sorted((f for f in files if 'main' in f['labels']), key=lambda f: f['upload_time'])
    published = {f['basename']: f['sha256'] for f in files}
    pinned = load_step().XCB_CURSOR
    # the pinned file with its published hash, or the newest build for that platform if it is gone
    expected = {}
    for machine, (path, _) in pinned.items():
        if path not in published:
            subdir = path.split('/')[0]
            path = next(f['basename'] for f in reversed(files) if f['basename'].startswith(f'{subdir}/'))
        expected[machine] = (path, published[path])
    assert pinned == expected, f'set XCB_CURSOR in include/install_step.py to {expected}'


def terminal(tmp_path, typed):
    answers = tmp_path / 'answers'
    answers.write_text(typed)
    return str(answers), str(tmp_path / 'questions')


@pytest.mark.parametrize(
    ('typed', 'expected'),
    [('y\n', True), ('\n', True), ('yes please\n', True), ('n\n', False), ('  No\n', False), ('', False)],
)
def test_model_question(step, tmp_path, typed, expected):
    assert step.wants_model(FULL, {}, 'ask', terminal(tmp_path, typed)) is expected
    question = (tmp_path / 'questions').read_text()
    assert question == 'Download the Foo model now? Otherwise it downloads the first time\nthe plugin needs it. [Y/n] '


def test_model_without_terminal(step, tmp_path):
    missing = str(tmp_path / 'no-tty')
    assert step.wants_model(FULL, {}, 'ask', (missing, missing)) is False


def test_model_choice(step, tmp_path):
    asked = terminal(tmp_path, 'y\n')
    assert step.wants_model(FULL, {}, 'yes', asked) is True
    assert step.wants_model(FULL, {}, 'no', asked) is False
    # the environment variable wins over the setup's choice, as in install.ps1
    assert step.wants_model(FULL, {'NAPARI_FOO_MODEL': '1'}, 'no', asked) is True
    assert step.wants_model(FULL, {'NAPARI_FOO_MODEL': '0'}, 'ask', asked) is False
    assert not (tmp_path / 'questions').exists()


def test_install(step, root, capsys):
    step.install(CONFIG, root, {})
    python = str(root / 'env/bin/python')
    assert [call[:3] for call in calls(root)] == [['uv', 'pip', 'install'], ['python', '-m', 'napari_launcher']]
    assert calls(root)[1][1:] == step.shortcut_command(CONFIG, {}, python)[1:]
    output = capsys.readouterr().out
    assert output.startswith('==> Installing napari and napari-foo\n==> Adding shortcuts\n')
    assert output.endswith('\n'.join(step.summary(CONFIG, python)) + '\n')


def test_install_torch_and_model(step, root, capsys):
    step.install(FULL, root, {'NAPARI_FOO_TORCH': 'cpu'}, no_desktop=True, model='yes')
    made = calls(root)
    assert made[0][:7] == ['uv', 'pip', 'install', '--python', str(root / 'env/bin/python'), '--torch-backend', 'cpu']
    assert made[1][:2] == ['python', '-c'] and 'torch.cuda.is_available()' in made[1][2]
    assert made[2][-4:] == ['--no-desktop', '-w', 'napari-foo', 'Foo widget']
    assert made[3] == ['python', '-m', 'napari_foo', 'fetch-model']
    output = capsys.readouterr().out
    assert '==> Installing napari and napari-foo (PyTorch build: cpu)\n' in output
    assert '==> Downloading the Foo model\n' in output


def test_install_model_declined(step, root):
    step.install(FULL, root, {'NAPARI_FOO_MODEL': '0'})
    assert ['python', '-m', 'napari_foo', 'fetch-model'] not in calls(root)


def test_install_fails(step, root, monkeypatch):
    monkeypatch.setenv('FAKE_FAIL', 'pip install')
    with pytest.raises(step.Failed, match=r'/bin/uv failed with exit code 1$'):
        step.install(CONFIG, root, {})
    assert len(calls(root)) == 1


def test_shortcut_fails(step, root, monkeypatch):
    monkeypatch.setenv('FAKE_FAIL', 'shortcut add')
    with pytest.raises(step.Failed) as failed:
        step.install(FULL, root, {}, model='yes')
    message = str(failed.value)
    assert message.startswith('napari and napari-foo are installed, but the shortcuts are not.')
    assert 'says a file was not made by napari-launcher, rename or delete that file, then run this again' in message
    # a wrong widget name in the answers is for the plugin's developers to fix
    assert message.endswith(
        'If it says a plugin or widget is not there, report it at https://github.com/someone/napari-foo/issues.'
    )
    # no model download after the failure
    assert calls(root)[-1][3:5] == ['shortcut', 'add']


def test_summary(step):
    python = Path('/home/me/.local/share/napari-foo/env/bin/python')
    assert step.summary(FULL, python) == [
        'Done. Start Foo App from the shortcut, or run:',
        f'    "{python}" -m napari_launcher launch -w napari-foo "Foo widget"',
        'To uninstall:',
        '    curl -LsSf https://raw.githubusercontent.com/someone/napari-foo/main/installer/install.sh | sh -s -- --uninstall',
    ]
    assert step.summary(CONFIG, python)[1] == f'    "{python}" -m napari_launcher launch -w napari-foo'


def test_summary_windows(step, monkeypatch):
    monkeypatch.setattr(step, 'WINDOWS', True)
    python = r'C:\Users\me\AppData\Local\napari-foo\env\Scripts\python.exe'
    assert step.summary(FULL, python) == [
        'Done. Start Foo App from the Start menu or desktop shortcut, or run:',
        f'    & "{python}" -m napari_launcher launch -w napari-foo \'Foo widget\'',
    ]


def test_uninstall(step, root, monkeypatch, capsys):
    assert step.uninstall(FULL, root) == 0
    assert calls(root) == [['python', '-m', 'napari_launcher', 'shortcut', 'remove', '--name', 'Foo App']]
    monkeypatch.setenv('FAKE_FAIL', 'shortcut remove')
    assert step.uninstall(FULL, root) == 1
    assert capsys.readouterr().out == (
        '==> The shortcuts were not removed (see above); delete any Foo App shortcuts that are left by hand.\n'
    )


def test_windows_messages_go_to_the_log(step, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(step, 'WINDOWS', True)
    monkeypatch.setattr(step, 'COLOUR', True)
    monkeypatch.setattr(step, 'LOG', tmp_path / 'install.log')
    step.say('Adding shortcuts')
    step.warn('Zellz\u00e4hlung')
    assert capsys.readouterr().out == '\033[36m==> Adding shortcuts\033[0m\n\033[33mZellz\u00e4hlung\033[0m\n'
    assert (tmp_path / 'install.log').read_text(encoding='utf-8') == '==> Adding shortcuts\nZellz\u00e4hlung\n'


def test_source_fits_both_scripts():
    """the source goes into a quoted sh here-document and a PowerShell here-string, and is a Jinja template"""
    text = SOURCE.read_text(encoding='utf-8')
    assert text.isascii()
    lines = text.splitlines()
    assert 'PY' not in lines
    assert not any(line.lstrip().startswith("'@") for line in lines)
    assert text.count('[[') == 1 and '[%' not in text and '[#' not in text


def test_utf8_output(step, monkeypatch):
    # Python on Windows writes the ANSI code page into a pipe
    stdout = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
    monkeypatch.setattr(sys, 'stdout', stdout)
    monkeypatch.setattr(sys, 'stderr', io.TextIOWrapper(io.BytesIO(), encoding='cp1252'))
    environ = {}
    step.utf8_output(environ)
    print('Zellzählung')
    stdout.flush()
    assert stdout.buffer.getvalue() == 'Zellzählung\n'.encode()
    assert environ == {'PYTHONIOENCODING': 'utf-8'}
