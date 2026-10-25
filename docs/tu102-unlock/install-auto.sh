#!/bin/sh
set -e
mkdir -p /lib/firmware/tu102-lab
ASSETS_DIR="${ASSETS_DIR:-/opt/tu102-unlock/assets}"
cp "$ASSETS_DIR"/*.bin /lib/firmware/tu102-lab/
cp /tmp/hook-final.sh /etc/initramfs-tools/hooks/tu102-unlock
cp /tmp/init-top-final.sh /etc/initramfs-tools/scripts/init-top/tu102_unlock
chmod 755 /etc/initramfs-tools/hooks/tu102-unlock /etc/initramfs-tools/scripts/init-top/tu102_unlock
update-initramfs -u -k 7.0.0-38-generic
lsinitramfs /boot/initrd.img-7.0.0-38-generic | grep -E 'tu102_unlock|tu102_execute\.ko|firmware/tu102-lab'
