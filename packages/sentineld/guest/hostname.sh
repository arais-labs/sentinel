#!/bin/sh
# The workspace's hostname ($1), run by sentineld's provisioning on Linux distributions (their
# distribution.json "hostname"): /etc/hostname, the running hostname, and a 127.0.1.1 line in
# /etc/hosts (with localhost added when the system ships no hosts file).
set -eu
printf '%s\n' "$1" > /etc/hostname
hostname "$1"
h=/etc/hosts
{
	grep -v '# sentinel$' $h 2>/dev/null || :
	grep -qs '^127\.0\.0\.1[[:space:]]' $h || printf '127.0.0.1\tlocalhost\n::1\tlocalhost\n'
	printf '127.0.1.1\t%s\t# sentinel\n' "$1"
} > $h.new && mv $h.new $h
