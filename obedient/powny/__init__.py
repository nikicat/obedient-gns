import os
import os.path
import yaml
from dominator.utils import aslist, resource_string
from dominator.entities import (Image, SourceImage, Container, DataVolume, ConfigVolume, LogVolume, TemplateFile,
                                YamlFile, TextFile, LogFile, LocalShip, Shipment, Task, Door)
from obedient import zookeeper


def getbuilder(
        zookeepers,
        ssh_keys=(),
        smtp_host='smtp.example.com',
        smtp_port=25,
        golem_url_ro='http://ro.admin.yandex-team.ru',
        golem_url_rw='https://golem.yandex-team.ru',
        threads=10,
        restapi_port=7887,
        gitapi_port=2022,
        elasticsearch_urls=(),
        pownyversion='0.4',
        memory=1024**3,
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

    restapilogs = LogVolume(
        dest='/var/log/powny',
        files={
            'uwsgi.log': LogFile('%Y%m%d-%H%M%S'),
        },
    )
    pownylogs = LogVolume(
        dest='/var/log/powny',
        files={
            'powny.log': LogFile(''),
        },
    )
    gitapilogs = LogVolume(
        dest='/var/log/powny',
        files={
            'gitapi.log': LogFile(''),
        },
    )

    configdest = '/etc/powny'
    uwsgi_ini_file = TemplateFile(TextFile('uwsgi.ini'))

    def stoppable(cmd):
        return 'trap exit TERM; {} & wait'.format(cmd)

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
            'pip install contextlog elog gns=={}'.format(pownyversion),
        ],
        volumes={
            'config': configdest,
            'rules': rules.dest,
            'logs': pownylogs.dest,
        },
        command=stoppable('gns $POWNY_MODULE -c /etc/powny/powny.yaml'),
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
            'config': configdest,
            'rules': rules.dest,
            'logs': restapilogs.dest,
        },
        command=stoppable('uwsgi --ini uwsgi.ini'),
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
            'logs': pownylogs.dest,
        },
        command='bash /root/run.sh',
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
            'core': {
                'zoo-nodes': ['{}:{}'.format(z.ship.fqdn, z.getport('client')) for z in zookeepers],
                'max-input-queue-size': 5000,
            },
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

    def container(ship, name, config, doors=None, backdoor=None, volumes=None, memory=memory, image=pownyimage,
                  files=None, logs=pownylogs):
        doors = doors or {}
        volumes = volumes or {}
        files = files or {}

        if backdoor is not None:
            config['backdoor'] = {'enabled': True, 'port': backdoor}
            doors['backdoor'] = Door(schema='http', port=backdoor, externalport=backdoor)

        files = files.copy()
        files['powny.yaml'] = YamlFile(config)

        _volumes = {
            'config': ConfigVolume(dest=configdest, files=files),
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
            doors=doors,
        )

    class Builder:
        @staticmethod
        def splitter(ship):
            config = make_config(ship)
            add_service(config, 'splitter')
            add_rules(config)
            return container(ship, 'splitter', config, volumes={'rules': rules}, backdoor=11002, doors={})

        @staticmethod
        def worker(ship):
            config = make_config(ship)
            add_service(config, 'worker')
            add_rules(config)
            add_output(config, 'powny@'+ship.fqdn)
            return container(ship, 'worker', config, volumes={'rules': rules}, backdoor=11001, doors={})

        @staticmethod
        def restapi(ship, name='api', port=restapi_port):
            config = make_config(ship)
            return container(ship, name, config, files={'uwsgi.ini': uwsgi_ini_file},
                             backdoor=None, doors={'http': port}, image=apiimage, logs=restapilogs)

        @staticmethod
        def collector(ship):
            config = make_config(ship)
            add_service(config, 'collector')
            return container(ship, 'collector', config, backdoor=11003, doors={})

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
                    'keys': keys,
                    'logs': gitapilogs,
                },
                doors={'ssh': Door(schema='ssh', port=gitimage.ports['ssh'], externalport=gitapi_port)},
                env={
                    'rules_git_path': rulesgit.dest,
                    'rules_path': rules.dest,
                },
            )

        @staticmethod
        def reinit(ship):
            config = make_config(ship)
            return Task(container(ship, 'reinit', config))

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


def make_local():
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
    reinit = getbuilder(zookeepers=zookeepers, ssh_keys=[_get_local_key()]).reinit(ships[0])

    return Shipment('local', containers=zookeepers+powny+[mta], tasks=[reinit])
