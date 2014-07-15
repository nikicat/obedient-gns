import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.gns',
        version='1.0',
        url='https://github.com/yandex-sysmon/obedient-gns',
        license='GPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='GNS obedient for Dominator',
        platforms='linux',
        packages=['obedient.gns.base', 'obedient.gns.development'],
        namespace_packages=['obedient', 'obedient.gns'],
        package_data={'obedient.gns.base': ['logging.yaml', 'post-receive', 'run.sh', 'sshd_config']},
        install_requires=[
            'dominator >=3, <4',
            'obedient.zookeeper',
        ],
    )
