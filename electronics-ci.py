#!/usr/bin/env python3

from pathlib import Path
import shutil
import os
import subprocess


def find_projects(path='.'):
    """
    Finds all CI projects, returns a dictionary where each item contains:
    {
       'name': name-of-project
       'path': /path/to/project/folder
       'cfg': /path/to/project.kibot.yaml
    }
    """

    print("Scanning for projects...")
    cfg_suffix = '.kibot.yaml'
    configs = list(Path(".").rglob("*" + cfg_suffix))

    if not len(configs):
        print("\tNo electronics projects found!")
        print("\tNote: each project should have a {}-file.".format(cfg_suffix))
        return {}

    elif len(configs) > 1:
        print("\tFound {} projects:".format(len(configs)))

    projects = {}

    for cfg in configs:
        project = cfg.name[:-len(cfg_suffix)]
        projects[project] = {'name': project, 'path': cfg.parent, 'cfg': cfg}
        print("\tFound '{}'".format(project))

    return projects

def run_CI(project):

    print("\n==== Running CI for '{}' ====".format(project['name']))
    out_dir = Path(ci_folder) / project['name']

    # Guard against deleting the whole filesystem
    if len(str(out_dir)) < 5:
        print("Error: path {} may be invalid".format(out_dir))
        return False

    # Clear previous output (if any)
    if out_dir.exists():
        if not out_dir.is_dir():
            print("Error: path {} is not a directory".format(out_dir))
            return False
        shutil.rmtree(Path(out_dir))

    Path.mkdir(out_dir, parents=True)
    return run_docker(project, out_dir)

def run_docker(project, out_dir):
    proc = subprocess.run(['docker', 'run',
        '--rm', '-it',
        '--volume', str(project['path'].resolve()) + ':/build/' + project['name'],
        '--volume', str(out_dir.resolve()) + ':/build/out_dir',
        '--env', 'HOST_UID={}'.format(os.getuid()),
        '--env', 'HOST_GID={}'.format(os.getgid()),
        'jittercompany/jitter_electronics_ci:0.1',
        '/jitter/run_ci.sh {} {}'.format(project['name'], 'out_dir')],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    print("\n++++ RESULT:")
    print(proc.stdout.decode('utf-8'))
    print(proc.stderr.decode('utf-8'))
    return proc.returncode == 0


projects = find_projects('.')
ci_folder = 'ci'

ok = True
for _, project in projects.items():

    if run_CI(project):
        print("__ OK __")
    else:
        print("__ FAIL __")
        ok = False

if not ok:
    print("__ CI FAILED! __")
    exit(1)
