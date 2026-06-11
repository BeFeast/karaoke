# TrueNAS NFS artifacts

Karaoke artifacts live on TrueNAS Odin, outside the git checkout and outside
the Dockhand stack directory. The locked path is:

```text
/mnt/Odin/lxc-shared/karaoke
```

The `lxc-shared` dataset is the shared TrueNAS dataset from
`[[truenas_lxc_shared_dataset]]`. The Docker volume pattern is the operator
vault entry `[[devbox_docker_nfs_volume_pattern]]`: create a Docker local
volume with `type=nfs`, an `addr=...` option for the TrueNAS host, and a
`device=...` export path.

## Export

From `devbox` (`10.10.0.13`), the TrueNAS host is `10.10.0.15` and exposes:

```text
/mnt/Odin/lxc-shared 10.10.0.0/24
```

This covers the `devbox` host. The karaoke subdirectory is mounted directly in
the stack so the app sees only its own artifact root.

## Docker volume

Use these driver options for the Dockhand compose volume:

```yaml
volumes:
  karaoke-artifacts:
    driver: local
    driver_opts:
      type: nfs
      o: addr=10.10.0.15,rw,nfsvers=4
      device: :/mnt/Odin/lxc-shared/karaoke
```

Mount it into the coordinator at `/srv/artifacts`, matching the default
`KARAOKE_ARTIFACT_ROOT`.

```yaml
services:
  karaoke:
    volumes:
      - karaoke-artifacts:/srv/artifacts
```

## Probe

Acceptance probe for the issue used the parent export and wrote
`karaoke/.probe`:

```sh
docker volume create \
  --driver local \
  --opt type=nfs \
  --opt o=addr=10.10.0.15,rw,nfsvers=4 \
  --opt device=:/mnt/Odin/lxc-shared \
  karaoke_nfs_probe_kar14

docker run --rm --user 1000:1000 \
  -v karaoke_nfs_probe_kar14:/mnt/nfs \
  alpine:3.20 \
  sh -euxc 'mkdir -p /mnt/nfs/karaoke; printf karaoke-nfs-probe > /mnt/nfs/karaoke/.probe; cat /mnt/nfs/karaoke/.probe; rm /mnt/nfs/karaoke/.probe'

docker volume rm karaoke_nfs_probe_kar14
```

Production-shape probe mounted the karaoke subpath at `/srv/artifacts`:

```sh
docker volume create \
  --driver local \
  --opt type=nfs \
  --opt o=addr=10.10.0.15,rw,nfsvers=4 \
  --opt device=:/mnt/Odin/lxc-shared/karaoke \
  karaoke_artifacts_nfs_probe_kar14

docker run --rm --user 1000:1000 \
  -v karaoke_artifacts_nfs_probe_kar14:/srv/artifacts \
  alpine:3.20 \
  sh -euxc 'ls -ldn /srv/artifacts; printf karaoke-artifacts-subpath > /srv/artifacts/.probe; cat /srv/artifacts/.probe; rm /srv/artifacts/.probe; ls -ldn /srv/artifacts'

docker volume rm karaoke_artifacts_nfs_probe_kar14
```

Verified permissions through Docker NFS: `drwxrwxr-x`, owner `1000:1000`.
