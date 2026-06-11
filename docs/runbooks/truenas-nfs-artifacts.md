# TrueNAS NFS artifacts

Karaoke artifacts live on the shared TrueNAS dataset from
`[[truenas_lxc_shared_dataset]]`:

- TrueNAS path: `/mnt/Odin/lxc-shared/karaoke`
- NFS export: `/mnt/Odin/lxc-shared` to `10.10.0.0/24`
- devbox: `10.10.0.13`
- TrueNAS NFS address used by Docker: `10.10.0.15`

This follows the `[[devbox_docker_nfs_volume_pattern]]`: devbox mounts the
share through Docker's local volume driver instead of a host-level fstab mount.

## Docker volume opts

The Dockhand stack volume on devbox is `karaoke_artifacts`:

```yaml
driver: local
driver_opts:
  type: nfs
  o: addr=10.10.0.15,nfsvers=4
  device: :/mnt/Odin/lxc-shared/karaoke
```

`docker volume inspect karaoke_artifacts` reports the mounted data path as
`/var/lib/docker/volumes/karaoke_artifacts/_data`.

## Probe

The live mount was verified from devbox with a throwaway container:

```sh
docker run --rm -v karaoke_artifacts:/artifacts busybox:latest sh -c \
  'set -eu; echo karaoke-nfs-probe > /artifacts/.probe; cat /artifacts/.probe; ls -ln /artifacts/.probe; rm /artifacts/.probe; test ! -e /artifacts/.probe'
```

Observed result:

```text
karaoke-nfs-probe
-rw-r--r--    1 0        0               18 Jun 11 11:25 /artifacts/.probe
```

The artifacts directory itself reports `1000:1000` and `0775` from inside the
probe container. The current app container runs as `uid=0,gid=0`, so today's
worker writes land as `0:0`; revisit this mapping before changing the runtime
user.
