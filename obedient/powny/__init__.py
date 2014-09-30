import os
import textwrap

import yaml

from dominator.utils import aslist, resource_string, stoppable
from dominator.entities import (Image, SourceImage, Container, Shipment, LocalShip,
                                Door, DataVolume, ConfigVolume, LogVolume, LogFile, YamlFile, TextFile)

from obedient import zookeeper


# =====
def namespace():
    return os.environ.get('OBEDIENT_GNS_NAMESPACE', 'yandex')


def make_local():
    ships = [LocalShip()]
    zookeepers = zookeeper.create(ships)
    builder = make_builder(
        ssh_keys=get_ssh_keys(),
        zookeepers=zookeepers,
        helpers={
            'configure': ['powny.helpers.email'],
            'email': {
                'noop': True,
            },
        },
    )
    pownies = builder.build(ships)
    return Shipment('local', containers=(zookeepers + pownies))


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


def make_builder(
    zookeepers,
    ssh_keys=(),
    gitapi_port=2022,
    userapi_port=7887,
    dataapi_port=7888,
    elasticsearch_urls=(),
    extra_scripts=(),
    helpers=None,
    pownyversion='latest',
):

    logging_config = yaml.load(resource_string('logging.yaml'))

    if elasticsearch_urls:
        elog_config = yaml.load(resource_string('logging.elog.yaml'))
        logging_config['handlers']['elog'] = elog_config
        elog_config['urls'] = elasticsearch_urls
        logging_config['root']['handlers'].append('elog')

    rules = DataVolume(
        dest='/var/lib/powny/rules',
        path='/var/lib/powny/rules',
    )
    rulesgit = DataVolume(
        dest='/var/lib/powny/rules.git',
        path='/var/lib/powny/rules.git',
    )

    def make_logs(*files):
        return LogVolume(dest='/var/log/powny', files={name: LogFile() for name in files})

    gitapi_logs = make_logs('gitapi.log')
    userapi_logs = make_logs('userapi.log')
    dataapi_logs = make_logs('dataapi.log')
    powny_logs = make_logs('powny.log', 'powny.debug.log')

    # Temporary stub (config volume will differ between containers)
    config_volume = ConfigVolume(dest='/etc/powny', files={
        'powny.yaml': None,
    })

    gitconfig_volume = ConfigVolume(dest='/etc/gitsplit', files={
        'gitsplit.conf': TextFile(text=textwrap.dedent('''
            RULES_GIT_PATH={rulesgit_dest}
            RULES_PATH={rules_dest}
            MODULE_NAME=rules
            REV_LIMIT=10
        ''').strip().format(
            rulesgit_dest=rulesgit.dest,
            rules_dest=rules.dest,
        )),
    })

    parent = Image(namespace='yandex', repository='trusty')

    pownyimage = SourceImage(
        name='powny',
        parent=parent,
        env={
            'PATH': '$PATH:/opt/pypy3/bin',
            'LANG': 'C.UTF-8',
        },
        scripts=[
            'curl http://buildbot.pypy.org/nightly/py3k/pypy-c-jit-latest-linux64.tar.bz2 2>/dev/null | tar -jxf -',
            'mv pypy* /opt/pypy3',
            'curl https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py 2>/dev/null | pypy',
            'easy_install pip==1.4.1',
            'pip install elog powny{}'.format('' if pownyversion == 'latest' else '=={}'.format(pownyversion)),
        ] + list(extra_scripts),
        volumes={
            'config': config_volume.dest,
            'rules': rules.dest,
            'logs': powny_logs.dest,
        },
        command=stoppable('powny-$POWNY_APP -c {}'.format(os.path.join(config_volume.dest, 'powny.yaml'))),
    )

    gitimage = SourceImage(
        name='gitsplit',
        parent=parent,
        files={
            '/post-receive': resource_string('post-receive'),
            '/etc/ssh/sshd_config': resource_string('sshd_config'),
            '/root/run.sh': resource_string('run.sh'),
        },
        ports={'ssh': 22},
        volumes={
            'rules': rules.dest,
            'rules.git': rulesgit.dest,
            'logs': powny_logs.dest,
        },
        command='bash /root/run.sh',
        scripts=[
            'apt-get install -y openssh-server',
            'useradd --non-unique --uid 0 --system --shell /usr/bin/git-shell -d / git',
            'mkdir /run/sshd',
            'chmod 0755 /run/sshd',
            'sed -i -e "s/session    required     pam_loginuid.so//g" /etc/pam.d/sshd'
        ],
    )

    keys_volume = ConfigVolume(dest='/var/lib/keys', files={
        'authorized_keys': TextFile(text='\n'.join(ssh_keys)),
    })

    def make_config(ship):
        config = {
            'core': {
                'rules-dir': rules.dest,
            },
            'backend': {
                'nodes': ['{}:{}'.format(z.ship.fqdn, z.getport('client')) for z in zookeepers],
            },
            'api': {
                'input-limit': 5000,
                'run': {
                    'host': '0.0.0.0',
                },
            },
            'logging': logging_config,
        }
        if helpers is not None:
            config['helpers'] = helpers
        return config

    def make_powny_container(
        ship,
        name,
        logs,
        app=None,
        memory=1024**3,
        doors=None,
        files=None,
        with_rules=True,
    ):
        app = app or name
        doors = doors or {}
        files = files or {}

        files = files.copy()
        files['powny.yaml'] = YamlFile(make_config(ship))

        volumes = {
            'config': ConfigVolume(dest=config_volume.dest, files=files),
            'logs': logs,
        }
        if with_rules:
            volumes.update({'rules': rules})

        return Container(
            name=name,
            ship=ship,
            image=pownyimage,
            memory=memory,
            volumes=volumes,
            env={'POWNY_APP': app},
            doors=doors,
        )

    class Builder:
        @staticmethod
        def gitapi(ship):
            return Container(
                name='gitapi',
                ship=ship,
                image=gitimage,
                memory=128*1024*1024,
                volumes={
                    'rules.git': rulesgit,
                    'rules': rules,
                    'keys': keys_volume,
                    'logs': gitapi_logs,
                    'gitconfig': gitconfig_volume,
                },
                doors={'ssh': Door(schema='ssh', port=gitimage.ports['ssh'], externalport=gitapi_port)},
            )

        @staticmethod
        def api(ship, name, logs, port):
            return make_powny_container(ship, name, app='api', logs=logs,
                                        doors={'http': Door(schema='http', port=7887, externalport=port)})

        @staticmethod
        def worker(ship):
            return make_powny_container(ship, 'worker', logs=powny_logs)

        @staticmethod
        def collector(ship):
            return make_powny_container(ship, 'collector', logs=powny_logs, with_rules=False)

        @classmethod
        @aslist
        def build(cls, ships):
            for ship in ships:
                yield cls.gitapi(ship)
                yield cls.api(ship, 'userapi', userapi_logs, userapi_port)
                yield cls.api(ship, 'dataapi', dataapi_logs, dataapi_port)
                yield cls.worker(ship)
                yield cls.collector(ship)

    return Builder
