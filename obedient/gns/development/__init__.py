import os

from .. import base

from dominator.entities import *
from obedient import exim
from obedient import zookeeper


def create():
    ships = [LocalShip()]
    zookeepers = zookeeper.create(ships)
    mtas = exim.create(ships)
    builder = base.builder(
        zookeepers=zookeepers,
        mtas=mtas,
        threads=1,
        ssh_key=os.getenv("SSH_KEY", "~/.ssh/id_rsa.pub"),
    )
    gns = builder.build(ships)

    return zookeepers + mtas + gns


def create_reinit():
    ship = LocalShip()
    zookeepers = zookeeper.create([ship])
    reinit = base.builder(zookeepers=zookeepers).reinit(ship)
    return zookeepers + [reinit]
