# NFS artifacts

Karaoke stores job artifacts on the TrueNAS Odin shared dataset:

- TrueNAS path: `/mnt/Odin/lxc-shared/karaoke`
- Operator path shorthand: `Odin/lxc-shared/karaoke`
- NFS endpoint for Docker volumes: `10.10.0.15`
- Export used by devbox: `/mnt/Odin/lxc-shared` exported to `10.10.0.0/24`
- Devbox address covered by the export: `10.10.0.13`

This follows the operator vault pattern `[[devbox_docker_nfs_volume_pattern]]`.
The stack should mount the parent export and point `KARAOKE_ARTIFACT_ROOT` at
the `karaoke` subdirectory inside the container, matching the app default
artifact root (`/srv/artifacts`).

## Docker volume opts

Verified driver opts:

```sh
--driver local \
--opt type=nfs \
--opt o=addr=10.10.0.15,nfsvers=4,rw \
--opt device=:/mnt/Odin/lxc-shared
```

Compose equivalent:

```yaml
volumes:
  karaoke-artifacts:
    driver: local
    driver_opts:
      type: nfs
      o: addr=10.10.0.15,nfsvers=4,rw
      device: :/mnt/Odin/lxc-shared
```

Service mount shape:

```yaml
services:
  karaoke:
    volumes:
      - karaoke-artifacts:/srv/nfs
    environment:
      KARAOKE_ARTIFACT_ROOT: /srv/nfs/karaoke
```

## Probe

Run this from devbox to verify the export, write/read/remove behavior, and
UID/GID mapping:

```sh
set -eux
cleanup() { docker volume rm karaoke_nfs_probe >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker volume rm karaoke_nfs_probe >/dev/null 2>&1 || true
docker volume create \
  --driver local \
  --opt type=nfs \
  --opt o=addr=10.10.0.15,nfsvers=4,rw \
  --opt device=:/mnt/Odin/lxc-shared \
  karaoke_nfs_probe

docker run --rm -v karaoke_nfs_probe:/mnt/share alpine:3.20 sh -euxc '
  mkdir -p /mnt/share/karaoke
  printf probe >/mnt/share/karaoke/.probe
  grep -qx probe /mnt/share/karaoke/.probe
  stat -c "%u:%g %a %n" /mnt/share/karaoke /mnt/share/karaoke/.probe
  rm /mnt/share/karaoke/.probe
  test ! -e /mnt/share/karaoke/.probe
'
```

Evidence from the provisioning probe:

```text
1000:1000 755 /mnt/share/karaoke
0:0 644 /mnt/share/karaoke/.probe
```

The directory is owned by `1000:1000`. The current coordinator image runs
without a non-root `USER`, so worker writes map to `0:0` over this NFS export.
