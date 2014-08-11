#!/usr/bin/env bash


# Init bare repo
cd $rules_git_path
git init --bare

# Copy hook
ln -fs /post-receive $rules_git_path/hooks/

# Run sshd daemon
/usr/sbin/sshd -D -e
