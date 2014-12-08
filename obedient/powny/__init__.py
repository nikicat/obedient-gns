import os
import textwrap
import itertools

import yaml

from dominator.utils import resource_stream, resource_string, stoppable, aslist
from dominator.entities import (Image, SourceImage, Container,
                                Door, DataVolume, ConfigVolume, LogVolume, LogFile, YamlFile, TextFile)


def test(shipment):
    from obedient.zookeeper import build_zookeeper_cluster
    shipment.unload_ships()
    zookeepers = build_zookeeper_cluster(shipment.ships.values())
    containers = itertools.chain(*build_powny_cluster(shipment.ships.values()))
    attach_zookeeper_to_powny(containers, zookeepers)
    shipment.expose_ports(list(range(47000, 47100)))


def build_powny_cluster(ships, ssh_keys=[], workers=1, collectors=1, **kwargs):
    builder = make_builder(**kwargs)

    all_gitapis = []
    all_userapis = []
    all_dataapis = []
    all_workers = []
    all_collectors = []

    for ship in ships:
        all_gitapis.append(builder.gitapi(ssh_keys))
        ship.place(all_gitapis[-1])

        all_userapis.append(builder.api('userapi'))
        ship.place(all_userapis[-1])

        all_dataapis.append(builder.api('dataapi'))
        ship.place(all_dataapis[-1])

        for count in range(workers):
            all_workers.append(builder.worker('worker-{:0>2d}'.format(count)))
            ship.place(all_workers[-1])

        for count in range(collectors):
            all_collectors.append(builder.collector('collector-{:0>2d}'.format(count)))
            ship.place(all_collectors[-1])

    return all_gitapis, all_userapis, all_dataapis, all_workers, all_collectors


def attach_zookeeper_to_powny(pownies, zookeepers):
    alldoors = [zookeeper.doors['client'] for zookeeper in zookeepers]
    zkships = [zookeeper.ship for zookeeper in zookeepers]
    for powny in pownies:
        # Try to link powny to local zookeeper only
        if powny.ship in zkships:
            zkdoors = [door for door in alldoors if door.container.ship == powny.ship]
        else:
            zkdoors = alldoors
        powny.links['zookeeper'] = zkdoors


def attach_elasticsearch_to_powny(pownies, elasticsearches):
    elasticdoors = [elasticsearch.doors['http'] for elasticsearch in elasticsearches]
    for powny in pownies:
        powny.links['elasticsearch'] = elasticdoors


def make_builder(
    api_workers=4,
    api_max_requests=5000,
    extra_scripts=(),
    helpers_config=None,
    pip_pre=False,
    powny_version='==1.4.0',
    elog_version='==1.1',
    pypy_version='jit-74309-4ca3a10894aa',
):
    powny_yaml_path = os.path.join('/etc/powny', 'powny.yaml')

    # === Images ===

    img_base = Image(namespace='yandex', repository='trusty')

    img_powny = SourceImage(
        name='powny',
        parent=img_base,
        env={
            'PATH': '$PATH:/opt/pypy3/bin',
            'LANG': 'C.UTF-8',
        },
        ports={'backdoor': 10023},
        scripts=[
            'curl http://buildbot.pypy.org/nightly/py3k/pypy-c-{}-linux64.tar.bz2 2>/dev/null'.format(pypy_version) +
            '| tar -jxf -',
            'mv pypy* /opt/pypy3',
            'curl https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py 2>/dev/null | pypy3',
            'easy_install pip==1.4.1',
            # json-formatter is needed to store exceptions and log extra fields in json logs.
            'pip install --pre'
            ' json-formatter==0.1.0-alpha-20141128-1014-94cc025'
            ' powny{powny_version}'.format(powny_version=powny_version),
        ] + list(extra_scripts),
        entrypoint=['bash', '-c'],
        command=[stoppable('powny-$POWNY_APP -c {}'.format(powny_yaml_path))],
    )

    img_gitapi = SourceImage(
        name='gitapi',
        parent=img_base,
        files={
            '/post-receive': resource_stream('post-receive'),
            '/etc/ssh/sshd_config': resource_stream('sshd_config'),
            '/root/run.sh': resource_stream('run.sh'),
        },
        ports={'ssh': 22},
        scripts=[
            'apt-get -q update && apt-get install -y openssh-server && apt-get clean',
            'useradd --non-unique --uid 0 --system --shell /usr/bin/git-shell -d / git',
            'mkdir /run/sshd',
            'chmod 0755 /run/sshd',
            'sed -i -e "s/session    required     pam_loginuid.so//g" /etc/pam.d/sshd'
        ],
        command=['/root/run.sh'],
    )

    # === Volumes ===

    def make_rules_volume():
        return DataVolume(dest='/var/lib/powny/rules', path='/var/lib/powny/rules')

    # === Containers ===

    def make_logs_volume(*files):
        return LogVolume(dest='/var/log/powny', files={name: LogFile() for name in files})

    def make_powny_container(name, app, memory=1024**3):
        app = app or name
        container = Container(
            name=name,
            image=img_powny,
            memory=memory,
            volumes={
                'config': None,
                'logs': make_logs_volume('powny.log', 'powny.debug.log', 'powny.json.log'),
                'rules': make_rules_volume(),
            },
            env={'POWNY_APP': app},
            doors={'backdoor': Door(schema='telnet', port=img_powny.ports['backdoor'])},
        )

        def make_logging_config(container):
            logging_config = yaml.load(resource_string('logging.yaml'))
            return logging_config

        def make_powny_config(container=container, helpers_config=helpers_config):
            logging_config = make_logging_config(container)
            assert 'zookeeper' in container.links, "Powny should be linked with zookeeper cluster to work"

            config = {
                'core': {
                    'rules_dir': make_rules_volume().dest,
                },
                'api': {
                    'gunicorn': {
                        'bind': '0.0.0.0:80',
                        'workers': api_workers,
                        'max_requests': api_max_requests,
                    },
                },
                'backdoor': {
                    'enabled': (api_workers == 1),  # Backdoor failed for multiprocess app
                    'port': img_powny.ports['backdoor'],
                },
                'backend': {
                    'nodes': [door.hostport for door in container.links['zookeeper']],
                },
                'logging': logging_config,
            }
            config['helpers'] = helpers_config or {}
            return YamlFile(config)

        container.volumes['config'] = ConfigVolume(
            dest=os.path.dirname(powny_yaml_path),
            files={os.path.basename(powny_yaml_path): make_powny_config},
        )

        return container

    class Builder:
        @staticmethod
        def gitapi(ssh_keys):

            dv_rules_git = DataVolume(dest='/var/lib/powny/rules.git', path='/var/lib/powny/rules.git')

            cv_etc_gitapi = ConfigVolume(dest='/etc/gitsplit', files={
                'gitsplit.conf': TextFile(text=textwrap.dedent('''
                    RULES_GIT_PATH={rules_git_dest}
                    RULES_PATH={rules_dest}
                    REV_LIMIT=10
                ''').strip().format(
                    rules_git_dest=dv_rules_git.dest,
                    rules_dest=make_rules_volume().dest,
                )),
            })

            cv_ssh_keys = ConfigVolume(dest='/var/lib/keys', files={
                'authorized_keys': TextFile(text='\n'.join(ssh_keys)),
            })
            return Container(
                name='gitapi',
                image=img_gitapi,
                memory=128*1024*1024,
                volumes={
                    'rules.git': dv_rules_git,
                    'rules': make_rules_volume(),
                    'config': cv_etc_gitapi,
                    'keys': cv_ssh_keys,
                    'logs': make_logs_volume('gitapi.log'),
                },
                doors={'ssh': Door(schema='ssh', port=img_gitapi.ports['ssh'])},
            )

        @staticmethod
        def api(name):
            cont = make_powny_container(name, 'api', memory=4096*1024*1024)
            cont.doors['http'] = Door(schema='http', port=80)
            return cont

        @staticmethod
        def worker(name):
            return make_powny_container(name, 'worker')

        @staticmethod
        def collector(name):
            return make_powny_container(name, 'collector')

    return Builder


@aslist
def get_ssh_keys():
    for key_path in ('~/.ssh/id_dsa.pub', '~/.ssh/id_rsa.pub', os.getenv('SSH_KEY')):
        if key_path is not None:
            key_path = os.path.expanduser(key_path)
            if os.path.exists(key_path):
                with open(key_path) as key_file:
                    yield key_file.read()
