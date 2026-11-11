# OCMS remote worker on PVE VM110

PVE pve2 上の VM110 `ocms-worker` で、OCMS の worker と runtime を動かす。
VPS 上の worker と同じ PostgreSQL ジョブキューを共有し、`FOR UPDATE SKIP LOCKED`
でジョブを自動分散する。Komodo 管理外の手動スタック。

## 構成

| 場所 | 値 |
| --- | --- |
| VM110 | `ocms-worker`、`192.168.1.53`、4 vCPU / 6 GiB RAM / 51 GiB disk |
| WireGuard (VM110) | `10.0.0.4/32` |
| WireGuard (VPS) | `10.0.0.1/24` |
| VPS の OCMS 内部 API | `catalog` `10.0.0.1:18081`、`transfer` `:18082`、`search` `:18083`、`runtime` `:18084` |
| PostgreSQL | `10.0.0.1:5434`、DB `ocms`、ロール `ocms_media` |
| ローカル runtime | `http://runtime:8080` |
| 監視 | worker/runtime の OTLP を pve-lgtm `192.168.1.240:4318` へ送る |

```text
VM110 (192.168.1.53)
  worker ── PostgreSQL 5434 ──────────────┐
    │  └── catalog/transfer/search ───────┤
    └── runtime:8080                      │
                                          │ WireGuard 10.0.0.4 ↔ 10.0.0.1
VPS (ocms-ubuntu)                         │
  PostgreSQL 17 :5434 ────────────────────┤
  catalog :18081 / transfer :18082 ───────┤
  search :18083 / runtime :18084 ─────────┘
```

## VM110 の作成

VM9001 テンプレートからクローンする。テンプレートの MAC・SMBIOS UUID・machine-id を
そのまま引き継ぐと DHCP が同じリースを返すため、次を必ず作り直す。

```sh
qm clone 9001 110 --name ocms-worker
qm set 110 --net0 virtio=<NEW_MAC>,bridge=vmbr0
qm set 110 --smbios1 uuid=<NEW_UUID>
qm set 110 --vmgenid <NEW_VMGENID>
qm set 110 --ipconfig0 ip=192.168.1.53/24,gw=192.168.1.1
qm cloudinit update 110
qm start 110
```

初回起動後に machine-id を新しい SMBIOS UUID から作り直す。

```sh
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
systemd-machine-id-setup
```

`ocms-runtime` の NumPy は x86-64-v2 を要求するため、VM の CPU タイプを `host` にする。

```sh
qm set 110 --cpu host
```

Docker と WireGuard を入れる。

```sh
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 wireguard-tools curl postgresql-client
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
```

## WireGuard

VM110 の `/etc/wireguard/wg0.conf` は `wireguard.conf.example` を元にする。
秘密鍵は VM110 で `wg genkey` して 0600 で保存する。

VPS の `/etc/wireguard/wg0.conf` に VM110 の peer を追加する。

```ini
[Peer]
# ocms-worker VM110
PublicKey = <VM110_PUBLIC_KEY>
AllowedIPs = 10.0.0.4/32
```

反映:

```sh
sudo wg set wg0 peer <VM110_PUBLIC_KEY> allowed-ips 10.0.0.4/32
sudo systemctl enable --now wg-quick@wg0
```

VM110 から `ping 10.0.0.1` と `nc -vz 10.0.0.1 5434` を確認する。

## VPS 側の準備

### PostgreSQL 17

`/etc/postgresql/17/main/postgresql.conf`:

```conf
listen_addresses = 'localhost,10.0.0.1'
```

`/etc/postgresql/17/main/pg_hba.conf`:

```conf
host    ocms    ocms_media    10.0.0.4/32    scram-sha-256
```

反映:

```sh
sudo systemctl restart postgresql@17-main
```

### UFW

```sh
sudo ufw allow in on wg0 from 10.0.0.4 to any port 5434 proto tcp
sudo ufw allow in on wg0 from 10.0.0.4 to any port 18081 proto tcp
sudo ufw allow in on wg0 from 10.0.0.4 to any port 18082 proto tcp
sudo ufw allow in on wg0 from 10.0.0.4 to any port 18083 proto tcp
sudo ufw allow in on wg0 from 10.0.0.4 to any port 18084 proto tcp
```

### OCMS の内部 API

`stacks/ocms/compose.yaml` で次のポートを `10.0.0.1` に公開している。
Komodo で反映すると `catalog` / `transfer` / `search` / `runtime` が再作成される。

```yaml
ports:
- 10.0.0.1:18081:8080  # catalog
- 10.0.0.1:18082:8080  # transfer
- 10.0.0.1:18083:8080  # search
- 10.0.0.1:18084:8080  # runtime
```

## 起動

VPS の `/etc/komodo/stacks/management/ocms.env` から `MEDIA_DB_PASSWORD` と
`INTERNAL_TOKEN` を取り、VM110 の `/opt/ocms-worker/.env` に 0600 で置く。
値は Git にコミットしない。テンプレートは `env.example`。

```sh
scp compose.yaml ubuntu@192.168.1.53:/opt/ocms-worker/
scp .env ubuntu@192.168.1.53:/opt/ocms-worker/
ssh ubuntu@192.168.1.53
cd /opt/ocms-worker
docker compose up -d --wait
```

## 確認

```sh
docker compose ps
docker logs --since 5m ocms-worker-worker-1
set -a; . /opt/ocms-worker/.env; set +a
PGPASSWORD="$MEDIA_DB_PASSWORD" psql -h 10.0.0.1 -p 5434 -U ocms_media -d ocms \
  -c "select status, job_type, count(*) from media.processing_jobs group by 1,2 order by 1,2;"
```

worker のログに `media.job_started` / `media.job_completed` が出て、queue の
`queued` が減れば分散できている。

## ジョブの振り分け

現在の OCMS は worker ごとの job_type フィルタを持たない。`SEMANTIC_SEARCH_ENABLED=false`
の worker は `media.process` と `tags.generate` を共有キューから取り合う。
`embedding.generate` は semantic search を有効にした worker だけが処理する。

特定の job_type だけを VM110 に寄せたい場合は、OCMS 側に worker 用の
job_type セレクタ（例: `MEDIA_WORKER_JOB_TYPES`）を追加するコード変更が必要。

## 監視

worker/runtime は OTLP を pve-lgtm に直接送る。pve-lgtm の中央 Collector が
`service.name=worker` に `host.name=ocms-worker-pve` と
`service.namespace=usbharu-lab` を付ける。

- Mimir: `media_job_completed{lab_host="ocms-worker-pve"}`
- Loki: `{service_name="worker"} | host_name="ocms-worker-pve"`
- Tempo: `{ resource.service.name = "worker" }`

`usbharulab-ocms` ダッシュボードの Job 系パネルは `ocms-ubuntu` と
`ocms-worker-pve` の両方を表示する。コンテナ CPU など VM110 側の cAdvisor は
収集していないため、そのパネルは VPS のみになる。
