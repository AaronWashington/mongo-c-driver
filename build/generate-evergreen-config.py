#!/usr/bin/env python
#
# Copyright 2018-present MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate C Driver's config.yml for Evergreen testing.

We find that generating configuration from Python data structures is more
legible than Evergreen's matrix syntax or a handwritten file.

Written for Python 2.6+, requires PyYAML and yamlordereddictloader.
"""

from os.path import dirname, join as joinpath, normpath

from evergreen_config_lib import OD, yaml_dump
from evergreen_config_lib.functions import all_functions, shell_exec
from evergreen_config_lib.tasks import all_tasks
from evergreen_config_lib.variants import all_variants

this_dir = dirname(__file__)
evergreen_dir = normpath(joinpath(this_dir, '../.evergreen'))
print('.evergreen/config.yml')
f = open(joinpath(evergreen_dir, 'config.yml'), 'w+')
f.write('''####################################
# Evergreen configuration for mongoc
#
# Generated by build/generate-evergreen-config.py
#
# DO NOT EDIT THIS FILE
#
####################################

''')

config = OD([
    ('stepback', True),
    ('command_type', 'system'),
    # 40 minute max except valgrind tasks, which get 2 hours.
    ('exec_timeout_secs', 2400),
    ('timeout', [shell_exec('ls -la')]),
    ('functions', all_functions),
    ('pre', [
        OD([('func', 'fetch source')]),
        OD([('func', 'windows fix')]),
        OD([('func', 'make files executable')]),
        OD([('func', 'prepare kerberos')]),
    ]),
    ('post', [
        OD([('func', 'backtrace')]),
        OD([('func', 'upload working dir')]),
        OD([('func', 'upload mo artifacts')]),
        OD([('func', 'upload test results')]),
        OD([('func', 'cleanup')]),
    ]),
    ('tasks', all_tasks),
    ('buildvariants', all_variants),
])

f.write(yaml_dump(config))
