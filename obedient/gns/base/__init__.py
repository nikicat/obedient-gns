import os
import yaml
from pkg_resources import resource_stream
from dominator import *
from dominator.settings import settings


def find_nearest(containers, ship):
    """Return container that is nearest to ship"""
    for container in containers:
        if container.ship == ship:
            return container
    else:
        for container in containers:
            if container.ship.datacenter == ship.datacenter:
                return container
        else:
            return containers[0]


def namespace():
    return os.environ.get('OBEDIENT_GNS_NAMESPACE', 'yandex')


def builder(
        zookeepers,
        mtas=None,
        gns_repo=namespace()+'/gns',
        gns_cpython_repo=namespace()+'/gns-cpython',
        gitapi_repo=namespace()+'/gitsplit',
        golem_sms_url='https://golem.yandex-team.ru/api/sms/send.sbml',
        threads=10,
        elasticsearch_url='http://elasticlog.yandex.net:9200',
        restapi_port=7887,
        gitapi_port=2022,
    ):

    logging_config = yaml.load(resource_stream(__name__, 'logging.yaml'))
    logging_config['handlers']['elasticsearch']['url'] = elasticsearch_url

    rules = DataVolume(
        dest='/var/lib/gns/rules',
        path='/var/lib/gns/rules',
    )

    gnsimage = Image(gns_repo)
    gnsapiimage = Image(gns_cpython_repo)
    gitapiimage = Image(gitapi_repo)

    def make_config():
        return {
            'core': {'zoo-nodes': ['{}:{}'.format(z.ship.fqdn, z.ports['client']) for z in zookeepers]},
            'logging': logging_config,
        }

    def add_service(config, name):
        config[name] = {
            'workers': threads,
            'die-after': None,
        }

    def add_rules(config):
        config['core']['import-alias'] = 'rules'
        config['core']['rules-dir'] = rules.dest

    def add_output(config, email_from, mta):
        config['output'] = {
            'sms': {'send-url': golem_sms_url},
            'email': {
                'from': email_from,
                'server': mta.ship.fqdn,
                'port': mta.ports['smtp'],
            },
        }

    def add_cherry(config):
        config['cherry'] = {'global': {'server.socket_port': restapi_port}}

    def container(ship, name, config, backdoor=None, ports={}, volumes=[], memory=1024**3, image=gnsimage):
        if backdoor is not None:
            config['backdoor'] = {'enabled': True, 'port': backdoor}
            ports['backdoor'] = backdoor

        config_volume = ConfigVolume(
            dest='/etc/gns',
            files=[YamlFile('gns.yaml', config)]
        )

        return Container(
            name='gns-'+name,
            ship=ship,
            image=image,
            memory=memory,
            volumes=volumes+[config_volume],
            env={'GNS_MODULE': name},
            ports=ports,
        )

    class Builder:
        @staticmethod
        def splitter(ship):
            config = make_config()
            add_service(config, 'splitter')
            add_rules(config)
            return container(ship, 'splitter', config, volumes=[rules], backdoor=11002, ports={})

        @staticmethod
        def worker(ship):
            config = make_config()
            add_service(config, 'worker')
            add_rules(config)
            add_output(config, 'gns@'+ship.fqdn, find_nearest(mtas, ship))
            return container(ship, 'worker', config, volumes=[rules], backdoor=11001, ports={})

        @staticmethod
        def restapi(ship):
            config = make_config()
            add_service(config, 'api')
            add_cherry(config)
            return container(ship, 'api', config, backdoor=11004, ports={'http': restapi_port}, image=gnsapiimage)

        @staticmethod
        def collector(ship):
            config = make_config()
            add_service(config, 'collector')
            return container(ship, 'collector', config, backdoor=11003, ports={})

        @staticmethod
        def gitapi(ship):
            rulesgit = DataVolume(
                dest='/var/lib/gns/rules.git',
                path='/var/lib/gns/rules.git',
            )

            return Container(
                name='gitapi',
                ship=ship,
                image=gitapiimage,
                memory=128*1024*1024,
                volumes=[rulesgit, rules],
                ports={'ssh': gitapiimage.ports[0]},
                extports={'ssh': gitapi_port},
                env={'KEY': open(os.path.expanduser('~/.ssh/id_rsa.pub')).read()}
            )

        @staticmethod
        def reinit(ship):
            return container(ship, 'reinit', make_config())


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
