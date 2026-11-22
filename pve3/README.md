# pve3 ホスト

Proxmox VE 9.2 のホスト `pve3`（192.168.1.251）。HP ProLiant DL360 Gen9。

Komodo の管理対象外で、PVE ホスト上で直接設定する。監視は
[PVE LGTM の手順](../pve-lgtm/README.md)、DCS サーバーは
[DCS の手順](../pve-dcs/README.md) を参照する。

## ハードウェア

| 項目 | 値 |
| --- | --- |
| 機種 | HP ProLiant DL360 Gen9 |
| CPU | 16 論理 CPU |
| RAM | 31 GiB（ZFS ARC 上限 1.55 GiB、`/etc/modprobe.d/zfs.conf`） |
| ストレージコントローラ | HP Smart Array P440ar（ファーム 6.60、HBA モード） |
| ブート | `sda` Hanye Q60-512GST3 512GB SSD → LVM `pve`（`local-lvm`） |
| データ | `sdb`〜`sdf` ST500LM034-2GH17A 500GB HDD ×5 → ZFS `hdd` |
| 未使用 | `sdg` / `sdh` HP EG0300FCVBF 300GB SAS ×2 |

P440ar は HBA モードのため、物理ディスクがそのまま Linux に見える。
ZFS はハードウェア RAID を挟まず、ディスクを直接使う。

## ZFS プール `hdd`

2026-11-22 に HDD 5台で RAIDZ2 を構築した。

| 項目 | 値 |
| --- | --- |
| 構成 | RAIDZ2（5台中2台まで故障に耐える） |
| 生容量 | 2.27 TiB |
| 実効容量 | 約 1.30 TiB |
| デバイス | `/dev/disk/by-id/ata-ST500LM034-2GH17A_*` |
| プロパティ | `ashift=12`、`compression=lz4`、`atime=off`、`xattr=sa`、`acltype=posixacl`、`autotrim=on` |

ディスクのシリアルは `WN90XG1P`、`WN90CD68`、`WN90CV36`、`WN90EXGE`、`WN90CWEG`。
再起動後もパスが変わらないよう、プール作成時から by-id で指定している。

### データセットと PVE ストレージ

| PVE ストレージ | 種類 | 実体 | content |
| --- | --- | --- | --- |
| `hdd-vm` | zfspool | `hdd/vm` | `images`、`rootdir`（thin） |
| `hdd-backup` | dir | `/hdd/backup` | `backup`、`iso` |

`hdd-backup` は大きなバックアップファイル向けに `recordsize=1M`、prune は `keep-all=1`。
2つのストレージは同じプールを共有するため、`pvesm status` の空き容量は両方とも
プール全体の値を示す。

`/etc/pve/storage.cfg` の該当部分:

```text
zfspool: hdd-vm
	pool hdd/vm
	content rootdir,images
	mountpoint /hdd/vm
	sparse 1

dir: hdd-backup
	path /hdd/backup
	content iso,backup
	prune-backups keep-all=1
```

### 構築手順

既存の NTFS パーティションを消してから作成した。ディスク上のデータはすべて失われる。

```bash
for s in WN90XG1P WN90CD68 WN90CV36 WN90EXGE WN90CWEG; do
  d=/dev/disk/by-id/ata-ST500LM034-2GH17A_$s
  wipefs -a ${d}-part1 2>/dev/null || true
  wipefs -a "$d"
  sgdisk --zap-all "$d"
done

zpool create -o ashift=12 -o autotrim=on \
  -O compression=lz4 -O atime=off -O xattr=sa -O acltype=posixacl \
  -O mountpoint=/hdd hdd raidz2 \
  /dev/disk/by-id/ata-ST500LM034-2GH17A_WN90XG1P \
  /dev/disk/by-id/ata-ST500LM034-2GH17A_WN90CD68 \
  /dev/disk/by-id/ata-ST500LM034-2GH17A_WN90CV36 \
  /dev/disk/by-id/ata-ST500LM034-2GH17A_WN90EXGE \
  /dev/disk/by-id/ata-ST500LM034-2GH17A_WN90CWEG

zfs create hdd/vm
zfs create hdd/backup
zfs set recordsize=1M hdd/backup

pvesm add zfspool hdd-vm --pool hdd/vm --content images,rootdir --sparse 1
pvesm add dir hdd-backup --path /hdd/backup --content backup,iso --prune-backups keep-all=1
```

### 運用

- 月次 scrub は `/etc/cron.d/zfsutils-linux` が第2日曜、trim は第1日曜に実行する。手動は
  `zpool scrub hdd`。
- SMART は `pve-smart-collector` が5分ごとに収集し、LGTM の `pve_disk_health_status` で見る。
- ディスク交換は P440ar が HBA モードなので、ベイのディスクを差し替えてから
  `zpool replace hdd <old-by-id> <new-by-id>` を実行する。交換中は RAIDZ2 の冗長性が
  1台分に減るため、resilver 完了まで別のディスクを抜かない。
- HDD のランダム I/O は遅い。DCS VM 120 のディスクは引き続き SSD の `local-lvm` に置く。
- プールが自動で import されない場合は `zpool import hdd` を実行する。通常は
  `/etc/zfs/zpool.cache` と initramfs で起動時に import される。

### 構築時の確認結果（2026-11-22）

- `zpool scrub hdd` が 0 errors で完了
- `pvesm alloc hdd-vm` で 1GiB zvol を作成し、64MiB の書き込み後に削除できた
- `/hdd/backup` への 32MiB 書き込みが成功
- `pvesm status` で `hdd-vm` と `hdd-backup` が `active`
