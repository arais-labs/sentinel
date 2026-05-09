#cloud-config
timezone: __TIMEZONE__
package_update: false
package_upgrade: false
users:
  - default
  - name: builder
    gecos: Sentinel Builder
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - __SSH_AUTHORIZED_KEY__
ssh_pwauth: false
write_files:
  - path: /usr/local/bin/sentinel-image-provision.sh
    permissions: '0755'
    owner: root:root
    encoding: b64
    content: __PROVISION_SCRIPT_B64__
runcmd:
  - [ bash, -lc, "/usr/local/bin/sentinel-image-provision.sh > /var/log/sentinel-image-provision.log 2>&1" ]
