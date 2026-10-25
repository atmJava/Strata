#!/bin/sh
set -e
dkms build -m tu102-unlock -v 1.0 --force
dkms install -m tu102-unlock -v 1.0 --force
update-initramfs -u -k all
echo "tu102-unlock rebuilt and initrd updated"
