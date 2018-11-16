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

from collections import OrderedDict as OD
from textwrap import dedent

from evergreen_config_lib import ConfigObject


def func(func_name, **kwargs):
    od = OD([('func', func_name)])
    if kwargs:
        od['vars'] = OD(sorted(kwargs.items()))

    return od


def bootstrap(VERSION='latest', TOPOLOGY=None, **kwargs):
    if TOPOLOGY:
        return func('bootstrap mongo-orchestration',
                    VERSION=VERSION,
                    TOPOLOGY=TOPOLOGY,
                    **kwargs)

    return func('bootstrap mongo-orchestration',
                VERSION=VERSION,
                **kwargs)


def run_tests(URI=None, **kwargs):
    if URI:
        return func('run tests', URI=URI, **kwargs)

    return func('run tests', **kwargs)


def s3_put(remote_file, project_path=True, **kwargs):
    if project_path:
        remote_file = '${project}/' + remote_file

    od = OD([
        ('command', 's3.put'),
        ('params', OD([
            ('aws_key', '${aws_key}'),
            ('aws_secret', '${aws_secret}'),
            ('remote_file', remote_file),
            ('bucket', 'mciuploads'),
            ('permissions', 'public-read')]))])

    od['params'].update(kwargs)
    return od


def strip_lines(s):
    return '\n'.join(line for line in s.split('\n') if line.strip())


def shell_exec(script, test=True, errexit=True, xtrace=True, silent=False,
               continue_on_err=False, working_dir='mongoc'):
    dedented = ''
    if errexit:
        dedented += 'set -o errexit\n'

    if xtrace:
        dedented += 'set -o xtrace\n'

    dedented += dedent(strip_lines(script))
    command = OD([('command', 'shell.exec')])
    if test:
        command['type'] = 'test'

    command['params'] = OD()
    if silent:
        command['params']['silent'] = True

    if working_dir is not None:
        command['params']['working_dir'] = working_dir

    if continue_on_err:
        command['params']['continue_on_err'] = True

    command['params']['script'] = dedented
    return command


def targz_pack(target, source_dir, *include):
    return OD([
        ('command', 'archive.targz_pack'),
        ('params', OD([
            ('target', target),
            ('source_dir', source_dir),
            ('include', list(include))]))])


class Function(ConfigObject):
    def __init__(self, *commands):
        super(Function, self).__init__()
        self.commands = commands

    def to_dict(self):
        return list(self.commands)


build_path = '${build_variant}/${revision}/${version_id}/${build_id}'

all_functions = OD([
    ('fetch source', Function(
        OD([('command', 'git.get_project'),
            ('params', OD([
                ('directory', 'mongoc'),
            ]))]),
        OD([('command', 'git.apply_patch'),
            ('params', OD([
                ('directory', 'mongoc'),
            ]))]),
        shell_exec(r'''
        if [ "${is_patch}" = "true" ]; then
           VERSION=$(git describe --abbrev=7)-patch-${version_id}
        else
           VERSION=latest
        fi
        echo "CURRENT_VERSION: $VERSION" > expansion.yml
        ''', test=False),
        OD([('command', 'expansions.update'),
            ('params', OD([
                ('file', 'mongoc/expansion.yml'),
            ]))]),
        shell_exec(r'''
        rm -f *.tar.gz
        curl --retry 5 https://s3.amazonaws.com/mciuploads/${project}/${branch_name}/mongo-c-driver-${CURRENT_VERSION}.tar.gz --output mongoc.tar.gz -sS --max-time 120
        ''', test=False, continue_on_err=True, working_dir=None),
    )),
    ('upload release', Function(
        shell_exec(
            r'[ -f mongoc/mongo*gz ] && mv mongoc/mongo*gz mongoc.tar.gz',
            errexit=False, test=False, working_dir=None),
        s3_put(
            'mongo-c-driver/${branch_name}/mongo-c-driver-${CURRENT_VERSION}.tar.gz',
            project_path=False, aws_key='${aws_key}',
            aws_secret='${aws_secret}', local_file='mongoc.tar.gz',
            bucket='mciuploads', permissions='public-read',
            content_type='${content_type|application/x-gzip}'),
    )),
    ('upload build', Function(
        targz_pack('${build_id}.tar.gz', 'mongoc', './**'),
        s3_put('${build_variant}/${revision}/${task_name}/${build_id}.tar.gz',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='${build_id}.tar.gz', bucket='mciuploads',
               permissions='public-read',
               content_type='${content_type|application/x-gzip}'),
    )),
    ('release archive', Function(
        shell_exec(r'''
        # Need modern Sphinx for :caption: in literal includes.
        python -m virtualenv venv
        cd venv
        . bin/activate
        ./bin/pip install sphinx GitPython
        cd ..
        
        set -o xtrace
        export MONGOC_TEST_FUTURE_TIMEOUT_MS=30000
        export MONGOC_TEST_SKIP_LIVE=on
        export MONGOC_TEST_SKIP_SLOW=on
        sh .evergreen/check-release-archive.sh
        ''', xtrace=False),
    )),
    ('install ssl', Function(
        shell_exec(r'SSL=${SSL} sh .evergreen/install-ssl.sh', test=False),
    )),
    ('fetch build', Function(
        shell_exec(r'rm -rf mongoc', test=False, continue_on_err=True,
                   working_dir=None),
        OD([('command', 's3.get'),
            ('params', OD([
                ('aws_key', '${aws_key}'),
                ('aws_secret', '${aws_secret}'),
                ('remote_file',
                 '${project}/${build_variant}/${revision}/${BUILD_NAME}/${build_id}.tar.gz'),
                ('bucket', 'mciuploads'),
                ('local_file', 'build.tar.gz'),
            ]))]),
        shell_exec(r'''
        mkdir mongoc
        
        if command -v gtar 2>/dev/null; then
           TAR=gtar
        else
           TAR=tar
        fi
        
        $TAR xf build.tar.gz -C mongoc/
        ''', test=False, continue_on_err=True, working_dir=None),
    )),
    ('upload docs', Function(
        shell_exec(r'''
        export AWS_ACCESS_KEY_ID=${aws_key}
        export AWS_SECRET_ACCESS_KEY=${aws_secret}
        aws s3 cp doc/html s3://mciuploads/${project}/docs/libbson/${CURRENT_VERSION} --recursive --acl public-read --region us-east-1
        ''', test=False, silent=True, working_dir='mongoc/src/libbson',
                   xtrace=False),
        s3_put('docs/libbson/${CURRENT_VERSION}/index.html',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/src/libbson/doc/html/index.html',
               bucket='mciuploads', permissions='public-read',
               content_type='text/html', display_name='libbson docs'),
        shell_exec(r'''
        export AWS_ACCESS_KEY_ID=${aws_key}
        export AWS_SECRET_ACCESS_KEY=${aws_secret}
        aws s3 cp doc/html s3://mciuploads/${project}/docs/libmongoc/${CURRENT_VERSION} --recursive --acl public-read --region us-east-1
        ''', test=False, silent=True, working_dir='mongoc/src/libmongoc',
                   xtrace=False),
        s3_put('docs/libmongoc/${CURRENT_VERSION}/index.html',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/src/libmongoc/doc/html/index.html',
               bucket='mciuploads', permissions='public-read',
               content_type='text/html', display_name='libmongoc docs'),
    )),
    ('upload man pages', Function(
        shell_exec(r'''
        # Get "aha", the ANSI HTML Adapter.
        git clone --depth 1 https://github.com/theZiz/aha.git aha-repo
        cd aha-repo
        make
        cd ..
        mv aha-repo/aha .
        
        sh .evergreen/man-pages-to-html.sh libbson src/libbson/doc/man > bson-man-pages.html
        sh .evergreen/man-pages-to-html.sh libmongoc src/libmongoc/doc/man > mongoc-man-pages.html
        ''', test=False, silent=True, xtrace=False),
        s3_put('man-pages/libbson/${CURRENT_VERSION}/index.html',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/bson-man-pages.html', bucket='mciuploads',
               permissions='public-read', content_type='text/html',
               display_name='libbson man pages'),
        s3_put('man-pages/libmongoc/${CURRENT_VERSION}/index.html',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/mongoc-man-pages.html', bucket='mciuploads',
               permissions='public-read', content_type='text/html',
               display_name='libmongoc man pages'),
    )),
    ('upload coverage', Function(
        shell_exec(r'''
        export AWS_ACCESS_KEY_ID=${aws_key}
        export AWS_SECRET_ACCESS_KEY=${aws_secret}
        aws s3 cp coverage s3://mciuploads/${project}/%s/coverage/ --recursive --acl public-read --region us-east-1
        ''' % (build_path,), test=False, silent=True, xtrace=False),
        s3_put(build_path + '/coverage/index.html', aws_key='${aws_key}',
               aws_secret='${aws_secret}',
               local_file='mongoc/coverage/index.html', bucket='mciuploads',
               permissions='public-read', content_type='text/html',
               display_name='Coverage Report'),
    )),
    ('abi report', Function(
        shell_exec(r'''
        # Need Git module for calculating release version
        python -m virtualenv venv
        cd venv
        . bin/activate
        ./bin/pip install GitPython
        cd ..
        
        sh .evergreen/abi-compliance-check.sh
        
        export AWS_ACCESS_KEY_ID=${aws_key}
        export AWS_SECRET_ACCESS_KEY=${aws_secret}
        aws s3 cp abi-compliance/compat_reports s3://mciuploads/${project}/%s/abi-compliance/compat_reports --recursive --acl public-read --region us-east-1
        
        if [ -e ./abi-compliance/abi-error.txt ]; then
          exit 1
        else
          exit 0
        fi
        ''' % (build_path,), test=False, silent=True, xtrace=False),
        s3_put(build_path + '/abi-compliance/compat_report.html',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_files_include_filter='mongoc/abi-compliance/compat_reports/**/*.html',
               bucket='mciuploads', permissions='public-read',
               content_type='text/html', display_name='ABI Report:'),
    )),
    ('upload scan artifacts', Function(
        shell_exec(r'''
        if find scan -name \*.html | grep -q html; then
          (cd scan && find . -name index.html -exec echo "<li><a href='{}'>{}</a></li>" \;) >> scan.html
        else
          echo "No issues found" > scan.html
        fi
        '''),
        shell_exec(r'''
        export AWS_ACCESS_KEY_ID=${aws_key}
        export AWS_SECRET_ACCESS_KEY=${aws_secret}
        aws s3 cp scan s3://mciuploads/${project}/%s/scan/ --recursive --acl public-read --region us-east-1
        ''' % (build_path,), test=False, silent=True, xtrace=False),
        s3_put(build_path + '/scan/index.html', aws_key='${aws_key}',
               aws_secret='${aws_secret}', local_file='mongoc/scan.html',
               bucket='mciuploads', permissions='public-read',
               content_type='text/html', display_name='Scan Build Report'),
    )),
    ('upload mo artifacts', Function(
        shell_exec(r'''
        DIR=MO
        [ -d "/cygdrive/c/data/mo" ] && DIR="/cygdrive/c/data/mo"
        [ -d $DIR ] && find $DIR -name \*.log | xargs tar czf mongodb-logs.tar.gz
        ''', test=False),
        s3_put(build_path + '/logs/${task_id}-${execution}-mongodb-logs.tar.gz',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/mongodb-logs.tar.gz', bucket='mciuploads',
               permissions='public-read',
               content_type='${content_type|application/x-gzip}',
               display_name='mongodb-logs.tar.gz'),
        s3_put(build_path + '/logs/${task_id}-${execution}-orchestration.log',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongoc/MO/server.log', bucket='mciuploads',
               permissions='public-read',
               content_type='${content_type|text/plain}',
               display_name='orchestration.log'),
        shell_exec(r'''
        # Find all core files from mongodb in orchestration and move to mongoc
        DIR=MO
        MDMP_DIR=$DIR
        [ -d "/cygdrive/c/data/mo" ] && DIR="/cygdrive/c/data/mo"
        [ -d "/cygdrive/c/mongodb" ] && MDMP_DIR="/cygdrive/c/mongodb"
        core_files=$(/usr/bin/find -H $MO $MDMP_DIR \( -name "*.core" -o -name "*.mdmp" \) 2> /dev/null)
        for core_file in $core_files
        do
          base_name=$(echo $core_file | sed "s/.*\///")
          # Move file if it does not already exist
          if [ ! -f $base_name ]; then
            mv $core_file .
          fi
        done
        ''', test=False),
        targz_pack('mongo-coredumps.tgz', 'mongoc', './**.core', './**.mdmp'),
        s3_put(build_path + '/coredumps/${task_id}-${execution}-coredumps.log',
               aws_key='${aws_key}', aws_secret='${aws_secret}',
               local_file='mongo-coredumps.tgz', bucket='mciuploads',
               permissions='public-read',
               content_type='${content_type|application/x-gzip}',
               display_name='Core Dumps - Execution ${execution}',
               optional='True'),
    )),
    ('backtrace', Function(
        shell_exec(r'./.evergreen/debug-core-evergreen.sh', test=False),
    )),
    ('upload working dir', Function(
        targz_pack('working-dir.tar.gz', 'mongoc', './**'),
        s3_put(
            build_path + '/artifacts/${task_id}-${execution}-working-dir.tar.gz',
            aws_key='${aws_key}', aws_secret='${aws_secret}',
            local_file='working-dir.tar.gz', bucket='mciuploads',
            permissions='public-read',
            content_type='${content_type|application/x-gzip}',
            display_name='working-dir.tar.gz'),
    )),
    ('upload test results', Function(
        OD([('command', 'attach.results'),
            ('params', OD([
                ('file_location', 'mongoc/test-results.json'),
            ]))]),
    )),
    ('bootstrap mongo-orchestration', Function(
        shell_exec(r'''
        export MONGODB_VERSION=${VERSION}
        export TOPOLOGY=${TOPOLOGY}
        export IPV4_ONLY=${IPV4_ONLY}
        export AUTH=${AUTH}
        export AUTHSOURCE=${AUTHSOURCE}
        export SSL=${SSL}
        export ORCHESTRATION_FILE=${ORCHESTRATION_FILE}
        sh .evergreen/integration-tests.sh
        ''', test=False),
    )),
    ('run tests', Function(
        shell_exec(r'''
        export COMPRESSORS='${COMPRESSORS}'
        export CC='${CC}'
        export AUTH=${AUTH}
        export SSL=${SSL}
        export URI=${URI}
        export IPV4_ONLY=${IPV4_ONLY}
        export VALGRIND=${VALGRIND}
        export MONGOC_TEST_URI=${URI}
        export DNS=${DNS}
        sh .evergreen/run-tests.sh
        '''),
    )),
    ('run tests bson', Function(
        shell_exec(r'CC="${CC}" sh .evergreen/run-tests-bson.sh'),
    )),
    ('run auth tests', Function(
        shell_exec(r'''
        export AUTH_HOST='${auth_host}'
        export AUTH_PLAIN='${auth_plain}'
        export AUTH_MONGODBCR='${auth_mongodbcr}'
        export AUTH_GSSAPI='${auth_gssapi}'
        export AUTH_CROSSREALM='${auth_crossrealm}'
        export AUTH_GSSAPI_UTF8='${auth_gssapi_utf8}'
        export ATLAS_FREE='${atlas_free}'
        export ATLAS_REPLSET='${atlas_replset}'
        export ATLAS_SHARD='${atlas_shard}'
        export ATLAS_TLS11='${atlas_tls11}'
        export ATLAS_TLS12='${atlas_tls12}'
        export REQUIRE_TLS12='${require_tls12}'
        export OBSOLETE_TLS='${obsolete_tls}'
        export VALGRIND='${valgrind}'
        sh .evergreen/run-auth-tests.sh
        ''', silent=True, xtrace=False),
    )),
    ('run mock server tests', Function(
        shell_exec(
            r'CC="${CC}" VALGRIND=${VALGRIND} sh .evergreen/run-mock-server-tests.sh'),
    )),
    ('cleanup', Function(
        shell_exec(r'''
        cd MO
        mongo-orchestration stop
        ''', test=False),
    )),
    ('windows fix', Function(
        shell_exec(r'''
        for i in $(find .evergreen -name \*.sh); do
          cat $i | tr -d '\r' > $i.new
          mv $i.new $i
        done
        ''', test=False, xtrace=False),
    )),
    ('make files executable', Function(
        shell_exec(r'''
        for i in $(find .evergreen -name \*.sh); do
          chmod +x $i
        done
        ''', test=False, xtrace=False),
    )),
    ('prepare kerberos', Function(
        shell_exec(r'''
        if test "${keytab|}"; then
           echo "${keytab}" > /tmp/drivers.keytab.base64
           base64 --decode /tmp/drivers.keytab.base64 > /tmp/drivers.keytab
           cat .evergreen/kerberos.realm | $SUDO tee -a /etc/krb5.conf
        fi
        ''', test=False, silent=True, xtrace=False),
    )),
    ('link sample program', Function(
        shell_exec(r'''
        # Compile a program that links dynamically or statically to libmongoc,
        # using variables from pkg-config or CMake's find_package command.
        export BUILD_SAMPLE_WITH_CMAKE=${BUILD_SAMPLE_WITH_CMAKE}
        export ENABLE_SSL=${ENABLE_SSL}
        export ENABLE_SNAPPY=${ENABLE_SNAPPY}
        LINK_STATIC=  sh .evergreen/link-sample-program.sh
        LINK_STATIC=1 sh .evergreen/link-sample-program.sh
        '''),
    )),
    ('link sample program bson', Function(
        shell_exec(r'''
        # Compile a program that links dynamically or statically to libbson,
        # using variables from pkg-config or from CMake's find_package command.
        BUILD_SAMPLE_WITH_CMAKE=  LINK_STATIC=  sh .evergreen/link-sample-program-bson.sh
        BUILD_SAMPLE_WITH_CMAKE=  LINK_STATIC=1 sh .evergreen/link-sample-program-bson.sh
        BUILD_SAMPLE_WITH_CMAKE=1 LINK_STATIC=  sh .evergreen/link-sample-program-bson.sh
        BUILD_SAMPLE_WITH_CMAKE=1 LINK_STATIC=1 sh .evergreen/link-sample-program-bson.sh
        '''),
    )),
    ('link sample program MSVC', Function(
        shell_exec(r'''
        # Build libmongoc with CMake and compile a program that links
        # dynamically or statically to it, using variables from CMake's
        # find_package command.
        export ENABLE_SSL=${ENABLE_SSL}
        export ENABLE_SNAPPY=${ENABLE_SNAPPY}
        LINK_STATIC=  cmd.exe /c .\\.evergreen\\link-sample-program-msvc.cmd
        LINK_STATIC=1 cmd.exe /c .\\.evergreen\\link-sample-program-msvc.cmd
        '''),
    )),
    ('link sample program mingw', Function(
        shell_exec(r'''
        # Build libmongoc with CMake and compile a program that links
        # dynamically to it, using variables from pkg-config.exe.
        cmd.exe /c .\\.evergreen\\link-sample-program-mingw.cmd
        '''),
    )),
    ('link sample program MSVC bson', Function(
        shell_exec(r'''
        # Build libmongoc with CMake and compile a program that links
        # dynamically or statically to it, using variables from CMake's
        # find_package command.
        export ENABLE_SSL=${ENABLE_SSL}
        export ENABLE_SNAPPY=${ENABLE_SNAPPY}
        LINK_STATIC=  cmd.exe /c .\\.evergreen\\link-sample-program-msvc-bson.cmd
        LINK_STATIC=1 cmd.exe /c .\\.evergreen\\link-sample-program-msvc-bson.cmd
        '''),
    )),
    ('link sample program mingw bson', Function(
        shell_exec(r'''
        # Build libmongoc with CMake and compile a program that links
        # dynamically to it, using variables from pkg-config.exe.
        cmd.exe /c .\\.evergreen\\link-sample-program-mingw-bson.cmd
        '''),
    )),
    ('update codecov.io', Function(
        shell_exec(r'''
        export CODECOV_TOKEN=${codecov_token}
        curl -s https://codecov.io/bash | bash
        ''', test=False, xtrace=False),
    )),
    ('debug-compile-coverage-notest-nosasl-nossl', Function(
        shell_exec(r'''
        export EXTRA_CONFIGURE_FLAGS="-DENABLE_COVERAGE=ON -DENABLE_EXAMPLES=OFF"
        DEBUG=ON CC='${CC}' MARCH='${MARCH}' SASL=OFF SSL=OFF SKIP_TESTS=ON sh .evergreen/compile.sh
        '''),
    )),
    ('debug-compile-coverage-notest-nosasl-openssl', Function(
        shell_exec(r'''
        export EXTRA_CONFIGURE_FLAGS="-DENABLE_COVERAGE=ON -DENABLE_EXAMPLES=OFF"
        DEBUG=ON CC='${CC}' MARCH='${MARCH}' SASL=OFF SSL=OPENSSL SKIP_TESTS=ON sh .evergreen/compile.sh
        '''),
    )),
])
