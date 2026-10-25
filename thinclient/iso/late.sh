#!/bin/sh
# Runs at the end of the Debian install: stage the Cove first-boot enrolment.
set -e
install -m 0755 /cdrom/cove/firstboot.sh /target/usr/local/sbin/cove-firstboot
install -m 0644 /cdrom/cove/cove-firstboot.service /target/etc/systemd/system/cove-firstboot.service
install -m 0600 /cdrom/cove/firstboot.env /target/etc/cove-thin-client-firstboot.env
if [ -s /cdrom/cove/authorized_keys ]; then
  install -d -m 0700 /target/home/coveadmin/.ssh
  install -m 0600 /cdrom/cove/authorized_keys /target/home/coveadmin/.ssh/authorized_keys
  in-target chown -R coveadmin:coveadmin /home/coveadmin/.ssh
  # Keys only: no password logins over SSH.
  mkdir -p /target/etc/ssh/sshd_config.d
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' \
    > /target/etc/ssh/sshd_config.d/50-cove.conf
fi
if [ -s /cdrom/cove/ca.crt ]; then
  # Cove's private certificate: trusted before first boot contacts Cove.
  install -d /target/usr/local/share/ca-certificates
  install -m 0644 /cdrom/cove/ca.crt /target/usr/local/share/ca-certificates/cove-thin-client.crt
  in-target update-ca-certificates
fi
in-target systemctl enable cove-firstboot.service
