import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.powny',
        version='3.7.0',
        url='https://github.com/yandex-sysmon/obedient.powny',
        license='GPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='Powny obedient for Dominator',
        platforms='linux',
        packages=['obedient.powny'],
        namespace_packages=['obedient'],
        package_data={'obedient.powny': [
            'logging.yaml',
            'logging.elog.yaml',
            'post-receive',
            'run.sh',
            'sshd_config',
        ]},
        entry_points={'obedient': [
            'test = obedient.powny:test'
        ]},
        install_requires=[
            'dominator[full] >=15a',
            'obedient.zookeeper >=3.0.1a',
            'obedient.elk >=1.4a',
        ],
    )
