import os
import shutil
import subprocess
from pathlib import Path

import yaml
from copier import run_copy, run_update

TEMPLATE = Path(__file__).resolve().parent.parent
# no signing or user config from the machine running the tests
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'test',
    'GIT_AUTHOR_EMAIL': 'test@example.org',
    'GIT_COMMITTER_NAME': 'test',
    'GIT_COMMITTER_EMAIL': 'test@example.org',
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
}


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def commit(cwd, tag=None):
    git(cwd, 'add', '-A')
    git(cwd, 'commit', '-q', '-m', tag or 'change')
    if tag:
        git(cwd, 'tag', tag)


def test_copier_update(tmp_path):
    """a project made from one release of the template takes in the next with `copier update`"""
    template = tmp_path / 'template'
    template.mkdir()
    shutil.copy(TEMPLATE / 'copier.yml', template)
    shutil.copytree(TEMPLATE / 'template', template / 'template')
    shutil.copytree(TEMPLATE / 'include', template / 'include')
    git(template, 'init', '-q')
    commit(template, 'v0.0.1')

    project = tmp_path / 'project'
    answers = {'dist': 'napari-foo', 'repo': 'someone/napari-foo', 'app_name': 'Foo App'}
    run_copy(str(template), project, data=answers, defaults=True, quiet=True)
    git(project, 'init', '-q')
    commit(project)
    # a change of the project's own, which the update keeps
    readme = project / 'installer/README.md'
    readme.write_text(readme.read_text() + '\nSee also the plugin documentation.\n')
    commit(project)

    # a hidden value and a template file
    copier_yml = template / 'copier.yml'
    uv_version = yaml.safe_load(copier_yml.read_text())['uv_version']['default']
    text = copier_yml.read_text()
    assert f'default: "{uv_version}"' in text
    copier_yml.write_text(text.replace(f'default: "{uv_version}"', 'default: "99.0.0"', 1))
    smoke_test = template / 'template/installer/smoke_test.py.jinja'
    smoke_test.write_text(smoke_test.read_text() + '# from the next release\n')
    # the shared Python step, which both install scripts include
    step = template / 'include/install_step.py'
    step.write_text(step.read_text() + '# from the next release\n')
    commit(template, 'v0.0.2')

    # as the generated files tell: copier update -a installer/.copier-answers.yml
    run_update(project, answers_file='installer/.copier-answers.yml', defaults=True, overwrite=True, quiet=True)

    stored = yaml.safe_load((project / 'installer/.copier-answers.yml').read_text())
    assert stored['_commit'] == 'v0.0.2'
    assert stored['app_name'] == 'Foo App'
    install_sh = (project / 'installer/install.sh').read_text()
    assert 'UV_VERSION=99.0.0' in install_sh and '"app_name": "Foo App"' in install_sh
    assert (project / 'installer/smoke_test.py').read_text().endswith('# from the next release\n')
    for script in ('install.sh', 'install.ps1'):
        assert '\n# from the next release\n' in (project / 'installer' / script).read_text()
    assert readme.read_text().endswith('\nSee also the plugin documentation.\n')
