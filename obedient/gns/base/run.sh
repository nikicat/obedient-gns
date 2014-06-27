#!/usr/bin/env bash


# Init bare repo
cd /var/lib/gns/rules.git
git init --bare

# Copy hook
ln -fs /post-receive /var/lib/gns/rules.git/hooks/

# Add authorized keys wich be able to push in this repo
echo $KEY >> /etc/ssh/authorized_keys

# Run sshd daemon
/usr/sbin/sshd -D -e
