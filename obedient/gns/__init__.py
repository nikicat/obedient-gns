import yaml
from pkg_resources import resource_stream
from dominator import *
from obedient import exim
from obedient import zookeeper


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


def gns_builder(
        zookeepers,
        mtas=None,
        gns_repo='yandex/gns',
        gitapi_repo='yandex/gitsplit',
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

    def container(ship, name, config, backdoor=None, ports={}, volumes=[], memory=1024**3):
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
            repository=gns_repo,
            tag=get_image(gns_repo),
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
            return container(ship, 'api', config, backdoor=11004, ports={'http': restapi_port})

        @staticmethod
        def collector(ship):
            config = make_config()
            add_service(config, 'collector')
            return container(ship, 'collector', config, backdoor=11003, ports={})

        @staticmethod
        def gitapi(ship):
            image = get_image(gitapi_repo)
            rulesgit = DataVolume(
                dest='/var/lib/gns/rules.git',
                path='/var/lib/gns/rules.git',
            )

            return Container(
                name='gitapi',
                ship=ship,
                repository=gitapi_repo,
                tag=image,
                memory=128*1024*1024,
                volumes=[rulesgit, rules],
                ports={'ssh': image_ports(image)[0]},
                extports={'ssh': gitapi_port},
            )

        @staticmethod
        def reinit(ship):
            return container(ship, 'reinit', make_config())

    return Builder


@aslist
def make_containers(ships, builder):
    for ship in ships:
        yield builder.worker(ship)
        yield builder.splitter(ship)
        yield builder.collector(ship)
        yield builder.restapi(ship)
        yield builder.gitapi(ship)


def development():
    ships = [LocalShip()]
    zookeepers = zookeeper.make_containers(ships)
    mtas = exim.make_containers(ships)
    builder = gns_builder(
        gns_repo='gns',
        gitapi_repo='gitsplit',
        golem_sms_url='https://golem.yandex-team.ru/api/sms/send.sbml',
        zookeepers=zookeepers,
        mtas=mtas,
        threads=1,
        restapi_port=7887,
        gitapi_port=10022,
    )
    gns = make_containers(ships, builder)

    return zookeepers + mtas + gns


def reinit_development():
    ship = LocalShip()
    zookeepers = zookeeper.make_containers([ship])
    reinit = gns_builder(gns_repo='gns', zookeepers=zookeepers).reinit(ship)
    return zookeepers + [reinit]
