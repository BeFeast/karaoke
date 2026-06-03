# TrueNAS NFS Artifacts Runbook

Refs: `[[devbox_docker_nfs_volume_pattern]]`, `[[truenas_lxc_shared_dataset]]`, PRD section 6.

## Live Path

- TrueNAS host: Odin, `10.10.0.15`
- Exported dataset path: `/mnt/Odin/lxc-shared`
- Karaoke artifact path: `/mnt/Odin/lxc-shared/karaoke`
- Devbox client: `10.10.0.13`

`showmount -e 10.10.0.15` from devbox confirmed `/mnt/Odin/lxc-shared` is exported to
`10.10.0.0/24`, which covers devbox.

## Docker Volume Driver Opts

Use Docker's local NFS volume driver on devbox:

```yaml
driver: local
driver_opts:
  type: nfs
  o: addr=10.10.0.15,nfsvers=4,rw
  device: :/mnt/Odin/lxc-shared
```

Runtime containers should use the `karaoke/` subdirectory within that exported path as the
artifact root, mounted at `/srv/artifacts` inside the coordinator/worker container.

## Probe

The live probe used this command shape on devbox:

```bash
docker volume rm karaoke_nfs_probe >/dev/null 2>&1 || true
docker volume create \
  --driver local \
  --opt type=nfs \
  --opt o=addr=10.10.0.15,nfsvers=4,rw \
  --opt device=:/mnt/Odin/lxc-shared \
  karaoke_nfs_probe

docker run --rm -v karaoke_nfs_probe:/mnt alpine:3.20 sh -c \
  'set -e; mkdir -p /mnt/karaoke; echo karaoke-nfs-probe > /mnt/karaoke/.probe; readback=$(cat /mnt/karaoke/.probe); test "$readback" = karaoke-nfs-probe; rm /mnt/karaoke/.probe; stat -c "%n %u:%g %a" /mnt/karaoke'

docker run --rm --user 1000:1000 -v karaoke_nfs_probe:/mnt alpine:3.20 sh -c \
  'set -e; echo uidgid-probe > /mnt/karaoke/.probe; readback=$(cat /mnt/karaoke/.probe); test "$readback" = uidgid-probe; stat -c "%n %u:%g %a" /mnt/karaoke/.probe; rm /mnt/karaoke/.probe'

docker run --rm -v karaoke_nfs_probe:/mnt alpine:3.20 sh -c \
  'set -e; test -d /mnt/karaoke; test ! -e /mnt/karaoke/.probe; stat -c "%n %u:%g %a" /mnt/karaoke'

docker volume rm karaoke_nfs_probe
```

Observed permissions:

```text
/mnt/karaoke 1000:1000 755
/mnt/karaoke/.probe 1000:1000 644
/mnt/karaoke 1000:1000 755
```

Direct SSH to TrueNAS was not available from the worktree key during this run; the path was
created and verified through the same NFS export that devbox Docker will use at runtime.
