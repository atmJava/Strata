#!/bin/sh
set -e
rm -f /etc/initramfs-tools/hooks/tu102-unlock
rm -f /etc/initramfs-tools/scripts/init-top/tu102_unlock
update-initramfs -u -k all
echo "tu102-unlock disabled; next boot is locked"
