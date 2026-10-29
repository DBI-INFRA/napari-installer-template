from pathlib import Path

import yaml
from copier import run_copy

TEMPLATE = Path(__file__).resolve().parent.parent
DUMMY = TEMPLATE / 'tests/fixtures/napari-dummy'


def test_dummy_plugin_answers(tmp_path):
    """the answers CI renders the dummy plugin with are valid and fit the plugin"""
    answers = yaml.safe_load((DUMMY.parent / 'napari-dummy.yml').read_text(encoding='utf-8'))
    manifest = yaml.safe_load((DUMMY / 'src/napari_dummy/napari.yaml').read_text(encoding='utf-8'))
    assert manifest['name'] == answers.get('plugin', answers['dist'])
    assert answers['extra_setup_command'] == '-m napari_dummy fetch-model'
    assert not answers['app_name'].isascii()
    run_copy(str(TEMPLATE), tmp_path, data=answers, defaults=True, quiet=True, vcs_ref='HEAD')
    iss = (tmp_path / 'installer/windows/napari-dummy.iss').read_text(encoding='utf-8-sig')
    assert f'#define AppName "{answers["app_name"]}"' in iss
