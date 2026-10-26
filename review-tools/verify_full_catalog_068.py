#!/usr/bin/env python3
"""Verify the complete proposed catalog entry through the real Copilot client.

Only new private HOME/config/fixture directories are written. A fixture catalog
name avoids replacing the client's real default catalog. Nothing is submitted.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

BASE = 'b0216dbc8f6f7ac1cfce85e925dfa5d5e423876a'
CANDIDATE = '565ddab4370f6644d8c67978bad8615ad8e7f156'
PRODUCT = '0ce43cd9a550a033a40b4e1f429a6c3c138802c2'
CORE = 'c7c9b395795f488dd3c48122ff916970438b67e6'
FIXTURE = 'b6fbf9862aa29fde57793c04894de5caa43a635a'
CATALOG = '.github/plugin/marketplace.json'
CLI_SHA = '506d032ae26882d40f1396e9b2080c6d9c3a5d5392404f9ef13eb7540efb55ec'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args])

def check_catalog(before,after):
    original = copy.deepcopy(before)
    current = copy.deepcopy(after)
    owned = [row for row in current['plugins'] if row['name'] == 'solaris-dev-shop']
    assert len(owned) == 1, 'Solaris must appear exactly once'
    entry = owned[0]
    current['plugins'] = [row for row in current['plugins'] if row['name'] != 'solaris-dev-shop']
    assert current == original, 'An existing entry or catalog owner/metadata changed'
    assert entry['version'] == '0.68.0' and entry['license'] == 'MIT'
    assert entry['author']['name'] == 'Solaris Technologies'
    assert entry['source'] == {'source':'github','repo':'Shaisolaris/solaris-dev-shop-plugins',
                               'path':'copilot/plugin','sha':PRODUCT}
    assert re.fullmatch('[0-9a-f]{40}',entry['source']['sha'])
    return entry

def controls(before,after):
    rows = []
    duplicate = copy.deepcopy(after)
    duplicate['plugins'].append(copy.deepcopy(duplicate['plugins'][-1]))
    rows.append(('duplicate product entry',duplicate))
    unpinned = copy.deepcopy(after)
    del unpinned['plugins'][-1]['source']['sha']
    rows.append(('unversioned public source',unpinned))
    changed = copy.deepcopy(after)
    changed['plugins'][0]['description'] = 'synthetic unrelated mutation'
    rows.append(('changed existing catalog entry',changed))
    for label,trial in rows:
        try:
            check_catalog(before,trial)
        except (AssertionError,KeyError):
            continue
        raise AssertionError('False acceptance: '+label)
    return [label for label,_ in rows]

def archive(root,ref):
    return tarfile.open(fileobj=io.BytesIO(git(root,'archive',ref)))

def check_public_fixture(repo, candidate):
    changed = git(repo,'diff','--name-only',CANDIDATE,FIXTURE).decode().splitlines()
    assert changed == [CATALOG], changed
    manifest = json.loads(git(repo,'show',FIXTURE+':'+CATALOG))
    assert manifest['name'] == 'solaris-catalog-qa'
    manifest['name'] = candidate['name']
    assert manifest == candidate, 'Public fixture changes more than catalog name'
    assert git(repo,'show',FIXTURE+':.claude-plugin/marketplace.json') == b'../.github/plugin/marketplace.json'
    mode = git(repo,'ls-tree',FIXTURE,'.claude-plugin/marketplace.json').decode().split()[0]
    assert mode == '120000', mode


def product_inventory(native):
    rows = {}
    with archive(native,PRODUCT+':copilot/plugin') as source:
        for item in source:
            if item.isdir():
                continue
            assert item.isfile() and not Path(item.name).is_absolute() and '..' not in Path(item.name).parts
            rows[item.name] = (source.extractfile(item).read(),bool(item.mode & 0o111))
    assert len(rows) == 1980
    provenance = json.loads(rows['SOLARIS-BUILD-SOURCE.json'][0])
    assert provenance['source_commit'] == CORE and len(provenance['canonical_files']) == 1201
    for name,value in provenance['canonical_files'].items():
        assert digest(rows[name][0]) == value,name
    return rows

def inventory(root):
    rows = {}
    for path in root.rglob('*'):
        assert not path.is_symlink(),str(path)
        if path.is_file():
            rows[path.relative_to(root).as_posix()] = (path.read_bytes(),bool(path.stat().st_mode & 0o111))
    return rows

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['catalog','native','cli','work']:
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    repo,native,cli,work = (getattr(args,name).resolve() for name in ['catalog','native','cli','work'])
    assert digest(cli.read_bytes()) == CLI_SHA
    before = json.loads(git(repo,'show',BASE+':'+CATALOG))
    after = json.loads(git(repo,'show',CANDIDATE+':'+CATALOG))
    entry = check_catalog(before,after)
    check_public_fixture(repo,after)
    rejected = controls(before,after)
    assert git(repo,'diff','--name-only',BASE,CANDIDATE).decode().splitlines() == [CATALOG,'README.md']
    expected = product_inventory(native)
    work.mkdir(parents=True,exist_ok=False)
    home = work/'home'
    home.mkdir()
    config = work/'config'
    gitconfig = work/'empty.gitconfig'
    gitconfig.touch()
    env = {k:os.environ[k] for k in ['TMPDIR','TEMP','TMP','SSL_CERT_FILE','SSL_CERT_DIR','LANG','LC_ALL'] if k in os.environ}
    env.update(HOME=str(home),COPILOT_HOME=str(config),COPILOT_AUTO_UPDATE='false',DO_NOT_TRACK='1',
               PATH=str(Path(sys.executable).parent)+':/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',NO_COLOR='1',
               GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=str(gitconfig),
               GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='credential.helper',GIT_CONFIG_VALUE_0='')
    def run(label,arguments):
        result = subprocess.run([str(cli),'--disable-client-telemetry',*map(str,arguments)],cwd=work,env=env,
                                capture_output=True,text=True,timeout=180)
        (work/(label+'.stdout')).write_text(result.stdout)
        (work/(label+'.stderr')).write_text(result.stderr)
        assert result.returncode == 0,label+': '+result.stderr[-3000:]
        return result.stdout
    assert run('version',['--version']).startswith('GitHub Copilot CLI 1.0.102.')
    run('add-marketplace',['plugin','marketplace','add','Shaisolaris/copilot-plugins#'+FIXTURE])
    run('install',['plugin','install','solaris-dev-shop@solaris-catalog-qa'])
    listed = json.loads(run('installed',['plugin','list','--json']))
    owned_plugins = [row for row in listed if row['name'] == 'solaris-dev-shop']
    assert len(owned_plugins) == 1, owned_plugins
    assert owned_plugins[0]['marketplace'] == 'solaris-catalog-qa' and owned_plugins[0]['version'] == '0.68.0'
    assert owned_plugins[0]['enabled'] is True, owned_plugins
    installed = [p.parent.parent for p in (config/'installed-plugins').rglob('native/skills.json')
                 if (p.parent.parent/'COPILOT-LAYOUT.json').is_file()]
    assert len(installed) == 1,installed
    root = installed[0]
    assert inventory(root) == expected,'Installed source files/modes differ'
    skills = json.loads(run('skills',['skill','list','--json']))
    owned = [row for row in skills if str(root) in json.dumps(row)]
    names = {row['name'] for row in json.loads(expected['native/skills.json'][0])}
    assert len(owned) == len(names) == 62 and {row['name'] for row in owned} == names
    helper = native/'tools/verify_copilot_layout.py'
    assert helper.read_bytes() == git(native,'show',PRODUCT+':tools/verify_copilot_layout.py')
    sys.path.insert(0,str(helper.parent))
    from verify_copilot_layout import smoke
    fixtures = work/'recovery-fixtures'
    fixtures.mkdir()
    outcomes = smoke(root,fixtures)
    assert inventory(root) == expected,'Product bytes changed during helper checks'
    report = {'candidateCatalog':CANDIDATE,'source':entry['source'],'version':'0.68.0',
              'host':'CopilotCLI1.0.102','matchingFilesAndModes':1980,'canonicalFiles':1201,
              'nativeWorkflows':62,'catalogRejectionControls':rejected,'actualHelperOutcomes':outcomes,
              'fixtureCatalogNameOnlyOverride':'solaris-catalog-qa',
              'fixtureCatalogGitSHA':FIXTURE,'catalogTransport':'publicGitHub',
              'officialCatalogDefault':False,'originalAwesomeRejectionOverturned':False,'forceFlagUsed':False,
              'modelCommandsIssued':0,'modelUsageMetered':False,'ownerProfileAccess':False,
              'upstreamApplicationSubmitted':False,'publicMarketplaceAccepted':False,'pass':True}
    (work/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    main()
