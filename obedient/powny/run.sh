#!/usr/bin/env bash


# Init bare repo
cd /var/lib/gns/rules.git
git init --bare

# Copy hook
ln -fs /post-receive /var/lib/gns/rules.git/hooks/

# Run sshd daemon
/usr/sbin/sshd -D -e
