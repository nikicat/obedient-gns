#!/usr/bin/env bash


# Init bare repo
cd $RULES_GIT_PATH
git init --bare

# Copy hook
ln -fs /post-receive $RULES_GIT_PATH/hooks/
chmod +x /post-receive

cat /etc/pam.d/sshd

# Run sshd daemon
echo "Starting sshd"
/usr/sbin/sshd -D -e >/var/log/powny/gitapi.log 2>&1
