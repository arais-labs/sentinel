#!/bin/sh
# The regular application identity exists even without a graphical desktop.
# The agent remains the root supervisor; desktop/browser processes never do.
set -eu
useradd --create-home --user-group --home-dir /home/sentinel --shell /bin/sh sentinel
install -d -m 755 /etc/sudoers.d
printf '%s\n' 'sentinel ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/sentinel
chmod 440 /etc/sudoers.d/sentinel
visudo --check --file /etc/sudoers.d/sentinel
test "$(id -u sentinel)" != 0
test "$(id -g sentinel)" != 0
