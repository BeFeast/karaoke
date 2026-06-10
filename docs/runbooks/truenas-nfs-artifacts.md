# TrueNAS NFS artifacts path

The artifact share for karaoke lives on TrueNAS Odin:

- TrueNAS path: `/mnt/Odin/lxc-shared/karaoke`
- NFS export: `/mnt/Odin/lxc-shared`
- devbox client: `10.10.0.13`
- TrueNAS NFS server: `10.10.0.15`

This follows the operator vault's `devbox_docker_nfs_volume_pattern`: use the
Docker local volume driver with NFS opts, then test through a throwaway
container rather than mounting by hand on the host.

## Docker volume opts

The devbox stack volume is:

```yaml
volumes:
  karaoke_artifacts:
    driver: local
    driver_opts:
      type: nfs
      o: addr=10.10.0.15,nfsvers=4
      device: :/mnt/Odin/lxc-shared/karaoke
```

`docker volume inspect karaoke_artifacts` should show:

```json
{
  "Driver": "local",
  "Options": {
    "device": ":/mnt/Odin/lxc-shared/karaoke",
    "o": "addr=10.10.0.15,nfsvers=4",
    "type": "nfs"
  }
}
```

## Probe from devbox

Run this on `devbox`:

```sh
docker run --rm -u 1000:1000 -v karaoke_artifacts:/artifacts busybox:latest sh -c '
  set -eu
  id
  printf karaoke-nfs-probe > /artifacts/.probe
  test "$(cat /artifacts/.probe)" = karaoke-nfs-probe
  ls -ln /artifacts/.probe
  rm /artifacts/.probe
  test ! -e /artifacts/.probe
'
```

Expected ownership evidence:

```text
uid=1000 gid=1000 groups=1000
-rw-r--r--    1 1000     1000            17 ... /artifacts/.probe
```

Confirm the mounted directory itself is owned by the worker UID/GID:

```sh
docker run --rm -v karaoke_artifacts:/artifacts busybox:latest sh -c '
  set -eu
  test ! -e /artifacts/.probe
  stat -c "%u:%g %a %n" /artifacts
'
```

Expected:

```text
1000:1000 755 /artifacts
```
