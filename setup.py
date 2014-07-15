import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.gns',
        version='2.0',
        url='https://github.com/yandex-sysmon/obedient-gns',
        license='GPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='GNS obedient for Dominator',
        platforms='linux',
        packages=['obedient.gns'],
        namespace_packages=['obedient', 'obedient'],
        package_data={'obedient.gns': ['logging.yaml', 'post-receive', 'run.sh', 'sshd_config']},
        install_requires=[
            'dominator >=4, <5',
            'obedient.zookeeper',
            'obedient.exim',
        ],
    )
