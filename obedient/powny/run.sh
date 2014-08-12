#!/usr/bin/env bash


# Init bare repo
cd $rules_git_path
git init --bare

# Copy hook
ln -fs /post-receive $rules_git_path/hooks/

mkdir ~git/.ssh
echo "rules_path=$rules_path" >> ~git/.ssh/environment
echo "rules_git_path=$rules_git_path" >> ~git/.ssh/environment

# Run sshd daemon
/usr/sbin/sshd -D -e
