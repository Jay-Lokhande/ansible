#!/usr/bin/env python

# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#

# This script is for testing modules without running through the
# entire guts of Ansible and is very helpful for when developing
# modules.

from __future__ import absolute_import, division, print_function

import argparse
import json
import os
import subprocess
import sys
import traceback

from ansible.module_utils.common.text.converters import to_text
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

from ansible.release import __version__
from ansible.executor import module_common
import ansible.constants as C


def parse():
    """Parse command-line arguments.

    :return : argparse.Namespace
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--module-path', dest='module_path',
                        help="REQUIRED: full path of module source to execute")
    parser.add_argument('-a', '--args', dest='module_args', default="",
                        help="module argument string")
    parser.add_argument('-D', '--debugger', dest='debugger',
                        help="path to python debugger (e.g. /usr/bin/pdb)")
    parser.add_argument('-I', '--interpreter', dest='interpreter',
                        help="path to interpreter to use for this module"
                             " (e.g. ansible_python_interpreter=/usr/bin/python)",
                        metavar='INTERPRETER_TYPE=INTERPRETER_PATH',
                        default="ansible_python_interpreter={}".format(sys.executable if sys.executable else '/usr/bin/python'))
    parser.add_argument('-c', '--check', dest='check', action='store_true',
                        help="run the module in check mode")
    parser.add_argument('-n', '--noexecute', dest='execute', action='store_false',
                        default=True, help="do not run the resulting module")
    parser.add_argument('-o', '--output', dest='filename',
                        help="Filename for resulting module",
                        default="~/.ansible_module_generated")
    return parser.parse_args()


def boilerplate_module(modfile, args, interpreters, check, destfile):
    """Simulate what Ansible does with new-style modules.

    :param modfile: str, path to the module source file
    :param args: str, module argument string
    :param interpreters: dict, interpreter information
    :param check: bool, check mode flag
    :param destfile: str, path to the destination file
    :return: (str, str, str), modified module file path, module name, module style
    """
    loader = DataLoader()
    complex_args = {}

    complex_args['_ansible_selinux_special_fs'] = C.DEFAULT_SELINUX_SPECIAL_FS
    complex_args['_ansible_tmpdir'] = C.DEFAULT_LOCAL_TMP
    complex_args['_ansible_keep_remote_files'] = C.DEFAULT_KEEP_REMOTE_FILES
    complex_args['_ansible_version'] = __version__

    if args.startswith("@"):
        complex_args = utils_vars.combine_vars(complex_args, loader.load_from_file(args[1:]))
        args = ''
    elif args.startswith("{"):
        complex_args = utils_vars.combine_vars(complex_args, loader.load(args))
        args = ''

    if args:
        parsed_args = module_common.parse_kv(args)
        complex_args = utils_vars.combine_vars(complex_args, parsed_args)

    task_vars = interpreters

    if check:
        complex_args['_ansible_check_mode'] = True

    modname = os.path.basename(modfile)
    modname = os.path.splitext(modname)[0]
    (module_data, module_style, shebang) = module_common.modify_module(
        modname,
        modfile,
        complex_args,
        Templar(loader=loader),
        task_vars=task_vars
    )

    if module_style == 'new' and '_ANSIBALLZ_WRAPPER = True' in to_text(module_data):
        module_style = 'ansiballz'

    modfile2_path = os.path.expanduser(destfile)
    print("* including generated source, if any, saving to: {}".format(modfile2_path))
    if module_style not in ('ansiballz', 'old'):
        print("* this may offset any line numbers in tracebacks/debuggers!")
    with open(modfile2_path, 'wb') as modfile2:
        modfile2.write(module_data)

    return modfile2_path, modname, module_style


def runtest(modfile, modname, module_style, interpreters):
    """Test run a module, piping its output for reporting.

    :param modfile: str, path to the module source file
    :param modname: str, module name
    :param module_style: str, module style
    :param interpreters: dict, interpreter information
    """
    invoke = ""
    if module_style == 'ansiballz':
        modfile, _ = ansiballz_setup(modfile, modname, interpreters)
        if 'ansible_python_interpreter' in interpreters:
            invoke = "{} ".format(interpreters['ansible_python_interpreter'])

    os.chmod(modfile, 0o755)
    invoke = "{}{}".format(invoke, modfile)

    cmd = subprocess.Popen(invoke, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = cmd.communicate()
    out, err = to_text(out), to_text(err)

    try:
        print("*" * 35)
        print("RAW OUTPUT")
        print(out)
        print(err)
        results = json.loads(out)
    except Exception:
        print("*" * 35)
        print("INVALID OUTPUT FORMAT")
        print(out)
        traceback.print_exc()
        sys.exit(1)

    print("*" * 35)
    print("PARSED OUTPUT")
    print(json.dumps(results, indent=4, sort_keys=True))


def ansiballz_setup(modfile, modname, interpreters):
    """Set up an AnsiBallZ module by exploding its contents.

    :param modfile: str, path to the module source file
    :param modname: str, module name
    :param interpreters: dict, interpreter information
    :return: (str, str), modified module file path, args file path
    """
    os.chmod(modfile, 0o755)

    if 'ansible_python_interpreter' in interpreters:
        command = [interpreters['ansible_python_interpreter']]
    else:
        command = []
    command.extend([modfile, 'explode'])

    cmd = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = cmd.communicate()
    out, err = to_text(out, errors='surrogate_or_strict'), to_text(err)
    lines = out.splitlines()
    if len(lines) != 2 or 'Module expanded into' not in lines[0]:
        print("*" * 35)
        print("INVALID OUTPUT FROM ANSIBALLZ MODULE WRAPPER")
        print(out)
        sys.exit(err)
    debug_dir = lines[1].strip()

    core_dirs = glob.glob(os.path.join(debug_dir, 'ansible/modules'))
    collection_dirs = glob.glob(os.path.join(debug_dir, 'ansible_collections/*/*/plugins/modules'))

    for module_dir in core_dirs + collection_dirs:
        for dirname, directories, filenames in os.walk(module_dir):
            for filename in filenames:
                if filename == modname + '.py':
                    modfile = os.path.join(dirname, filename)
                    break

    argsfile = os.path.join(debug_dir, 'args')

    print("* ansiballz module detected; extracted module source to: {}".format(debug_dir))
    return modfile, argsfile


def main():
    args = parse()
    init_plugin_loader()
    interpreters = module_common.get_interpreters(args.interpreter)
    modfile, modname, module_style = boilerplate_module(args.module_path, args.module_args, interpreters,
                                                        args.check, args.filename)

    if args.execute:
        if args.debugger:
            rundebug(args.debugger, modfile, modname, module_style, interpreters)
        else:
            runtest(modfile, modname, module_style, interpreters)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(C.DEFAULT_LOCAL_TMP, True)
