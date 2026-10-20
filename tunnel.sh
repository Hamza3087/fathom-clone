#!/bin/bash
# Keeps a local-only tunnel to the Sounding pipeline API on the GPU box.
while true; do
  ssh -i /root/.ssh/hetzner_144 -o BatchMode=yes -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -N -L 127.0.0.1:8020:127.0.0.1:8020 root@5.9.117.80
  sleep 5
done
