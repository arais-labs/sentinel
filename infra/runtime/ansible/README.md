# Sentinel Runtime Ansible Provisioning

Provision a running Lima VM over SSH:

```bash
ANSIBLE_CONFIG=infra/runtime/ansible/ansible.cfg \
ansible-playbook \
  -i lima-sentinel-runtime-test, \
  --ssh-common-args="-F $HOME/.lima/sentinel-runtime-test/ssh.config" \
  infra/runtime/ansible/sentinel-runtime.yml \
  -e sentinel_desktop=xfce
```

KDE can still use the same playbook with `sentinel_desktop=kde`, but the
default local runtime target is the lighter XFCE profile.

The playbook provisions desktop/VNC packages but leaves `sentinel-vnc.service`
disabled. Sentinel starts per-session VNC desktops from the backend runtime
manager so session state and cleanup stay scoped to the session workspace.
