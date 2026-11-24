# OCMS remote worker on PVE VM110

PVE pve2 上の VM110 `ocms-worker` で、OCMS の worker と runtime を動かす。
`ocms-pve3` 上の worker と同じ PostgreSQL ジョブキューを共有し、`FOR UPDATE SKIP LOCKED`
でジョブを自動分散する。Komodo 管理外の手動スタック。

## 構成

| 場所 | 値 |
| --- | --- |
| VM110 | `ocms-worker`、`192.168.1.53`、4 vCPU / 6 GiB RAM / 51 GiB disk |
| OCMS の内部 API | `catalog` `192.168.1.254:18081`、`transfer` `:18082`、`search` `:18083`、`runtime` `:18084` |
| PostgreSQL | `192.168.1.253:5434`、DB `ocms`、ロール `ocms_media` |
| ローカル runtime | `http://runtime:8080` |
| 監視 | worker/runtime の OTLP を pve-lgtm `192.168.1.240:4318` へ送る |

```text
VM110 (192.168.1.53)
  worker ── PostgreSQL 5434 ──────────────┐
    │  └── catalog/transfer/search ───────┤
    └── runtime:8080                      │
                                          │ LAN 192.168.1.0/24
pve3                                      │
  db-pve3 192.168.1.253:5434 ─────────────┤
  ocms-pve3 192.168.1.254                  │
    catalog :18081 / transfer :18082 ─────┤
    search :18083 / runtime :18084 ───────┘
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

## WireGuard（任意）

OCMS の接続は LAN 経由に移行したため、VM110 の WireGuard は必須ではない。
LGTM など他の用途で使っていない場合は `sudo systemctl disable --now wg-quick@wg0` で停止してよい。
既存の `10.0.0.4` 設定は残しても OCMS の動作には影響しない。

## pve3 側の準備

### PostgreSQL 17（db-pve3）

`/etc/postgresql/17/main/postgresql.conf`:

```conf
listen_addresses = 'localhost,192.168.1.253'
port = 5434
```

`/etc/postgresql/17/main/pg_hba.conf`:

```conf
host    all    all    192.168.1.53/32     scram-sha-256
host    all    all    192.168.1.254/32    scram-sha-256
```

反映:

```sh
sudo pg_ctlcluster 17 main restart
```

### OCMS の内部 API（ocms-pve3）

`stacks/ocms/compose.yaml` で次のポートを `192.168.1.254` に公開している。
Komodo で反映すると `catalog` / `transfer` / `search` / `runtime` が再作成される。

```yaml
ports:
- 192.168.1.254:18081:8080  # catalog
- 192.168.1.254:18082:8080  # transfer
- 192.168.1.254:18083:8080  # search
- 192.168.1.254:18084:8080  # runtime
```

## 起動

`ocms-pve3` の `/etc/komodo/stacks/management/ocms.env` から `MEDIA_DB_PASSWORD` と
`INTERNAL_TOKEN` を取り、VM110 の `/opt/ocms-worker/.env` に 0600 で置く。
値は Git にコミットしない。テンプレートは `env.example`。

`ocms-worker/compose.yaml` の image タグは、`ocms-pve3` の `stacks/ocms/compose.yaml` と
揃える。`ocms-pve3` のデプロイ完了前に新しい worker を起動すると、内部 API の
バージョン不一致で `PROCESSING_FAILED` になる。

catalog のクエリ修正など、内部 API の互換性が変わらないパッチでは、`ocms-pve3` 側だけ
先に更新して worker を再起動しなくてよい。API の変更を伴う場合は、`ocms-pve3` の
デプロイ完了後に worker も同じタグへ更新する。

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
PGPASSWORD="$MEDIA_DB_PASSWORD" psql -h 192.168.1.253 -p 5434 -U ocms_media -d ocms \
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
`service.name=worker` に `host.name=ocms-worker-vm110` と
`service.namespace=usbharu-lab` を付ける。

- Mimir: `media_job_completed{lab_host="ocms-worker-vm110"}`
- Loki: `{service_name="worker"} | host_name="ocms-worker-vm110"`
- Tempo: `{ resource.service.name = "worker" }`

pve-lgtm の Mimir exporter は `sending_queue.num_consumers: 1` にしている。
複数 consumer で cumulative counter を送ると、Mimir が out-of-order として
サンプルを落とすことがあるため。

`usbharulab-ocms` ダッシュボードの Job 系パネルは `ocms-pve3` と
`ocms-worker-vm110` の両方を表示する。コンテナ CPU など VM110 側の cAdvisor は
収集していないため、そのパネルは VPS のみになる。
