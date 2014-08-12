import os
import os.path
import yaml
from pkg_resources import resource_stream
from dominator.utils import aslist
from dominator.entities import (Image, SourceImage, Container, DataVolume, ConfigVolume, TemplateFile,
                                YamlFile, TextFile, LocalShip)
from obedient import zookeeper


def namespace():
    return os.environ.get('OBEDIENT_GNS_NAMESPACE', 'yandex')


def getbuilder(
        zookeepers,
        ssh_keys=[],
        smtp_host='smtp.example.com',
        smtp_port=25,
        golem_url_ro='http://ro.admin.yandex-team.ru',
        golem_url_rw='https://golem.yandex-team.ru',
        threads=10,
        restapi_port=7887,
        gitapi_port=2022,
        elasticsearch_urls=[],
        pownyversion='0.3',
        ):

    logging_config = yaml.load(resource_stream(__name__, 'logging.yaml'))

    if elasticsearch_urls:
        elog_config = yaml.load(resource_stream(__name__, 'logging.elog.yaml'))
        logging_config['handlers']['elog'] = elog_config
        # FIXME: use all urls instead of only first
        elog_config['url'] = elasticsearch_urls[0]
        logging_config['root']['handlers'].append('elog')

    rules = DataVolume(
        dest='/var/lib/powny/rules',
        path='/var/lib/powny/rules',
    )

    rulesgit = DataVolume(
        dest='/var/lib/powny/rules.git',
        path='/var/lib/powny/rules.git',
    )

    logs = DataVolume(
        dest='/var/log/powny',
        path='/var/log/powny',
    )

    # Temporary stub (config volume will differ between containers)
    configvolume = ConfigVolume(dest='/etc/powny', files={
        'powny.yaml': None,
        'uwsgi.ini': TemplateFile(TextFile('uwsgi.ini')),
    })

    def stoppable(cmd):
        return 'trap exit TERM; {} & wait'.format(cmd)

    parent = Image('yandex/trusty')

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
            'pip install contextlog elog gns=={}'.format(pownyversion),
        ],
        volumes={
            'config': configvolume.dest,
            'rules': rules.dest,
            'logs': logs.dest,
        },
        command=stoppable('gns $POWNY_MODULE -c {}'.format(configvolume.getfilepath('powny.yaml'))),
    )
    apiimage = SourceImage(
        name='powny-cpython',
        parent=parent,
        env={'LANG': 'C.UTF-8'},
        scripts=[
            'apt-add-repository ppa:fkrull/deadsnakes -y',
            'apt-get update',
            'apt-get install python3-pip -yy',
            'pip3 install contextlog elog uwsgi gns=={}'.format(pownyversion),
        ],
        volumes={
            'config': configvolume.dest,
            'rules': rules.dest,
            'logs': logs.dest,
        },
        command=stoppable('uwsgi --ini {}'.format(configvolume.getfilepath('uwsgi.ini'))),
    )

    gitimage = SourceImage(
        name='gitsplit',
        parent=parent,
        files={
            '/post-receive': resource_stream(__name__, 'post-receive'),
            '/etc/ssh/sshd_config': resource_stream(__name__, 'sshd_config'),
            '/root/run.sh': resource_stream(__name__, 'run.sh'),
        },
        ports={'ssh': 22},
        volumes={
            'rules': rules.dest,
            'rules.git': rulesgit.dest,
            'logs': logs.dest,
        },
        command='/root/run.sh',
        scripts=[
            'apt-get install -y openssh-server',
            'useradd --non-unique --uid 0 --system --shell /usr/bin/git-shell -d / git',
            'mkdir /run/sshd',
            'chmod 0755 /run/sshd',
        ],
    )

    keys = ConfigVolume(dest='/var/lib/keys', files={'authorized_keys': TextFile(text='\n'.join(ssh_keys))})

    def make_config(ship):
        config = {
            'core': {'zoo-nodes': ['{}:{}'.format(z.ship.fqdn, z.getport('client')) for z in zookeepers]},
            'logging': logging_config,
        }
        return config

    def add_service(config, name):
        config[name] = {
            'workers': threads,
            'die-after': None,
        }

    def add_rules(config):
        config['core']['import-alias'] = 'rules'
        config['core']['rules-dir'] = rules.dest

    def add_output(config, email_from):
        config['golem'] = {'url-ro': golem_url_ro, 'url-rw': golem_url_rw}
        config['output'] = {
            'email': {
                'from': email_from,
                'server': smtp_host,
                'port': smtp_port,
            },
        }

    def container(ship, name, config, ports={}, backdoor=None, volumes={}, memory=1024**3, image=pownyimage,
                  files={}):
        if backdoor is not None:
            config['backdoor'] = {'enabled': True, 'port': backdoor}
            ports['backdoor'] = backdoor

        files = files.copy()
        files['powny.yaml'] = YamlFile(config)

        _volumes = {
            'config': ConfigVolume(dest=configvolume.dest, files=files),
            'logs': logs,
        }
        _volumes.update(volumes)

        return Container(
            name=name,
            ship=ship,
            image=image,
            memory=memory,
            volumes=_volumes,
            env={'POWNY_MODULE': name},
            ports=ports,
        )

    class Builder:
        @staticmethod
        def splitter(ship):
            config = make_config(ship)
            add_service(config, 'splitter')
            add_rules(config)
            return container(ship, 'splitter', config, volumes={'rules': rules}, backdoor=11002, ports={})

        @staticmethod
        def worker(ship):
            config = make_config(ship)
            add_service(config, 'worker')
            add_rules(config)
            add_output(config, 'powny@'+ship.fqdn)
            return container(ship, 'worker', config, volumes={'rules': rules}, backdoor=11001, ports={})

        @staticmethod
        def restapi(ship):
            config = make_config(ship)
            return container(ship, 'api', config, files={'uwsgi.ini': configvolume.files['uwsgi.ini']},
                             backdoor=None, ports={'http': restapi_port}, image=apiimage)

        @staticmethod
        def collector(ship):
            config = make_config(ship)
            add_service(config, 'collector')
            return container(ship, 'collector', config, backdoor=11003, ports={})

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
                    'keys': keys
                },
                ports=gitimage.ports,
                extports={'ssh': gitapi_port},
                env={
                    'rules_git_path': rulesgit.dest,
                    'rules_path': rules.dest,
                },
            )

        @staticmethod
        def reinit(ship):
            config = make_config(ship)
            return container(ship, 'reinit', config)

        @classmethod
        @aslist
        def build(cls, ships):
            for ship in ships:
                yield cls.worker(ship)
                yield cls.splitter(ship)
                yield cls.collector(ship)
                yield cls.restapi(ship)
                yield cls.gitapi(ship)

    return Builder


def _get_local_key():
    key_path = os.getenv('SSH_KEY', os.path.expanduser('~/.ssh/id_rsa.pub'))
    with open(key_path) as key_file:
        data = key_file.read()
    return data


def development():
    from obedient import exim
    ships = [LocalShip()]
    zookeepers = zookeeper.create(ships)
    mta = exim.create(ships)[0]
    builder = getbuilder(
        ssh_keys=[_get_local_key()],
        zookeepers=zookeepers,
        threads=1,
        smtp_host=mta.ship.fqdn,
        smtp_port=mta.getport('smtp'),
    )
    powny = builder.build(ships)

    return zookeepers + powny + [mta]


def development_reinit():
    ship = LocalShip()
    zookeepers = zookeeper.create([ship])
    reinit = getbuilder(zookeepers=zookeepers, ssh_keys=[_get_local_key()]).reinit(ship)
    return zookeepers + [reinit]
