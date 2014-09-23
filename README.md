obedient.powny
============

Dominator obedient for [https://github.com/yandex-sysmon/powny](Powny).

Usage
=====
Install in developer mode:
```
git clone https://github.com/yandex-sysmon/obedient.powny
pip install --user -e obedient.powny
```
Run the local instance:
```
dominator shipment generate obedient.powny local > powny.local.yaml
dominator -c powny.local.yaml image build
dominator -c powny.local.yaml container start
```
The service will be available at the following ports:
```
localhost:2022  # Git (remote `ssh://git@localhost:2022/var/lib/powny/rules.git`)
localhost:7887  # HTTP-API
```
