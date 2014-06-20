import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.gns',
        version='0.1',
        url='https://github.com/nikicat/obedient-gns',
        license='GPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='GNS obedient for Dominator',
        platforms='linux',
        packages=['obedient.gns'],
        namespace_packages=['obedient'],
        package_data={'obedient.gns': ['logging.yaml']},
        install_requires=['dominator==0.3'],
    )
