import os
import textwrap

import yaml

from dominator.utils import aslist, resource_string, stoppable
from dominator.entities import (Image, SourceImage, Container, Shipment, LocalShip,
                                Door, DataVolume, ConfigVolume, LogVolume, LogFile, YamlFile, TextFile)

from obedient import zookeeper


# =====
def make_local():
    ships = [LocalShip()]
    zookeepers = zookeeper.create(ships)
    builder = make_builder(
        ssh_keys=get_ssh_keys(),
        zookeepers=zookeepers,
    )
    pownies = builder.build(ships)
    return Shipment('local', containers=(zookeepers + pownies))


def make_builder(
    zookeepers,
    ssh_keys=(),
    gitapi_port=2022,
    userapi_port=80,
    dataapi_port=8080,
    backdoor_start_port=None,
    api_workers=4,
    api_max_requests=5000,
    extra_scripts=(),
    helpers_config=None,
    elasticsearch_urls=(),
    powny_version='latest',
):

    powny_yaml_path = os.path.join('/etc/powny', 'powny.yaml')

    # === Images ===

    img_base = Image(namespace='yandex', repository='trusty')

    img_powny_base = SourceImage(
        name='powny-base',
        parent=img_base,
        env={
            'PATH': '$PATH:/opt/pypy3/bin',
            'LANG': 'C.UTF-8',
        },
        scripts=[
            'curl http://buildbot.pypy.org/nightly/py3k/pypy-c-jit-latest-linux64.tar.bz2 2>/dev/null | tar -jxf -',
            'mv pypy* /opt/pypy3',
            'curl https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py 2>/dev/null | pypy',
            'easy_install pip==1.4.1',
            'pip install elog powny{versfx}'.format(
                versfx=('' if powny_version == 'latest' else '=={}'.format(powny_version)),
            ),
        ] + list(extra_scripts),
    )

    img_powny_service = SourceImage(
        name='powny-service',
        parent=img_powny_base,
        command=stoppable('powny-$POWNY_APP -c {}'.format(powny_yaml_path)),
    )

    img_powny_gunicorn = SourceImage(
        name='powny-gunicorn',
        parent=img_powny_base,
        scripts=['pip install gunicorn'],
        command=stoppable(
            'gunicorn --workers {workers} --max-requests {max_requests}'
            ' -b :80 \'powny.core.apps.api:make_app(True, ["-c", "{powny_yaml}"])\''.format(
                workers=api_workers,
                max_requests=api_max_requests,
                powny_yaml=powny_yaml_path,
            ),
        ),
    )

    img_gitapi = SourceImage(
        name='gitapi',
        parent=img_base,
        files={
            '/post-receive': resource_string('post-receive'),
            '/etc/ssh/sshd_config': resource_string('sshd_config'),
            '/root/run.sh': resource_string('run.sh'),
        },
        ports={'ssh': 22},
        scripts=[
            'apt-get -q update && apt-get install -y openssh-server && apt-get clean',
            'useradd --non-unique --uid 0 --system --shell /usr/bin/git-shell -d / git',
            'mkdir /run/sshd',
            'chmod 0755 /run/sshd',
            'sed -i -e "s/session    required     pam_loginuid.so//g" /etc/pam.d/sshd'
        ],
        command='bash /root/run.sh',
    )

    # === Volumes ===

    dv_rules = DataVolume(dest='/var/lib/powny/rules', path='/var/lib/powny/rules')
    dv_rules_git = DataVolume(dest='/var/lib/powny/rules.git', path='/var/lib/powny/rules.git')

    cv_etc_gitapi = ConfigVolume(dest='/etc/gitsplit', files={
        'gitsplit.conf': TextFile(text=textwrap.dedent('''
            RULES_GIT_PATH={rules_git_dest}
            RULES_PATH={rules_dest}
            REV_LIMIT=10
        ''').strip().format(
            rules_git_dest=dv_rules_git.dest,
            rules_dest=dv_rules.dest,
        )),
    })
    cv_ssh_keys = ConfigVolume(dest='/var/lib/keys', files={
        'authorized_keys': TextFile(text='\n'.join(ssh_keys)),
    })

    # === Containers ===

    def make_logging_config():
        logging_config = yaml.load(resource_string('logging.yaml'))
        if elasticsearch_urls:
            elog_config = yaml.load(resource_string('logging.elog.yaml'))
            elog_config['urls'] = elasticsearch_urls
            logging_config['handlers']['elog'] = elog_config
            logging_config['root']['handlers'].append('elog')
        return logging_config

    logging_config = make_logging_config()

    def make_powny_config(ship, backdoor_port):
        config = {
            'core': {
                'rules_dir': dv_rules.dest,
            },
            'backdoor': {
                'enabled': backdoor_port is not None,
                'port': backdoor_port,
            },
            'backend': {
                'nodes': [
                    '{}:{}'.format(zk.ship.fqdn, zk.getport('client'))
                    for zk in zookeepers
                    if zk.ship.fqdn == ship.fqdn
                ],
            },
            'logging': logging_config,
        }
        if helpers_config is not None:
            config['helpers'] = helpers_config
        return config

    def make_logs_volume(*files):
        return LogVolume(dest='/var/log/powny', files={name: LogFile() for name in files})

    def make_powny_logs_volume():
        return make_logs_volume('powny.log', 'powny.debug.log')

    def make_powny_container(image, ship, name, backdoor_offset,
                             app=None, memory=1024**3, doors=None, with_rules=True):
        app = (app or name)

        doors = doors or {}
        backdoor_port = (backdoor_start_port and (backdoor_start_port + backdoor_offset))
        if backdoor_port:
            doors['backdoor'] = Door(schema='telnet', port=backdoor_port, externalport=backdoor_port)

        files = {os.path.basename(powny_yaml_path): YamlFile(make_powny_config(ship, backdoor_port))}
        volumes = {
            'config': ConfigVolume(dest=os.path.dirname(powny_yaml_path), files=files),
            'logs': make_powny_logs_volume(),
        }
        if with_rules:
            volumes.update({'rules': dv_rules})

        return Container(
            name=name,
            ship=ship,
            image=image,
            memory=memory,
            volumes=volumes,
            env={'POWNY_APP': app},
            doors=doors,
        )

    # === Builder ===

    class Builder:
        @staticmethod
        def gitapi(ship):
            return Container(
                name='gitapi',
                ship=ship,
                image=img_gitapi,
                memory=128*1024*1024,
                volumes={
                    'rules.git': dv_rules_git,
                    'rules': dv_rules,
                    'config': cv_etc_gitapi,
                    'keys': cv_ssh_keys,
                    'logs': make_logs_volume('gitapi.log'),
                },
                doors={'ssh': Door(schema='ssh', port=img_gitapi.ports['ssh'], externalport=gitapi_port)},
            )

        @staticmethod
        def api(ship, name, port, backdoor_offset):
            return make_powny_container(
                image=img_powny_gunicorn,
                ship=ship,
                name=name,
                app="api",
                memory=2048*1024*1024,
                doors={'http': Door(schema='http', port=80, externalport=port)},
                backdoor_offset=backdoor_offset,
            )

        @staticmethod
        def worker(ship, backdoor_offset):
            return make_powny_container(
                image=img_powny_service,
                ship=ship,
                name='worker',
                backdoor_offset=backdoor_offset,
            )

        @staticmethod
        def collector(ship, backdoor_offset):
            return make_powny_container(
                image=img_powny_service,
                ship=ship,
                name='collector',
                backdoor_offset=backdoor_offset,
            )

        @classmethod
        @aslist
        def build(cls, ships):
            for ship in ships:
                yield cls.gitapi(ship)
                yield cls.api(ship, 'userapi', userapi_port, backdoor_offset=0)
                yield cls.api(ship, 'dataapi', dataapi_port, backdoor_offset=1)
                yield cls.worker(ship, backdoor_offset=2)
                yield cls.collector(ship, backdoor_offset=3)

    return Builder


# =====
def get_ssh_keys():
    keys = []
    for key_path in ('~/.ssh/id_dsa.pub', '~/.ssh/id_rsa.pub', os.getenv('SSH_KEY')):
        if key_path is not None:
            key_path = os.path.expanduser(key_path)
            if os.path.exists(key_path):
                with open(key_path) as key_file:
                    keys.append(key_file.read())
    assert len(keys) > 0, "No SSH keys found"
    return keys
