# PVE LGTM observability

このディレクトリは、Proxmox VE ホスト pve2 上の LXC 200 `lgtm` で手動運用している
ローカル LGTM スタックのダッシュボードと設定例を保存する。

Komodo 管理の `stacks/observability/`（`ocms-ubuntu` の Alloy）からは、WireGuard 経由で
メトリクスだけをこの LGTM に送る。LGTM スタック自体の管理は Komodo の対象外で、
`komodo/resources.toml` や GitOps Procedure には含めない。そのため `stacks/` には置かず、
この `pve-lgtm/` で管理する。

## 構成

| 場所 | 役割 |
| --- | --- |
| LXC 200 `lgtm`（192.168.1.240） | Grafana、Mimir、Loki、Tempo、OTel Collector、cloudflared |
| PVE pve2（192.168.1.250） | Go pve-exporter（systemd、`:9221`）、OTel Collector（hostmetrics + journald）、WireGuard ゲートウェイ |
| VM100 `picoclaw`（192.168.1.78） | node_exporter、OTel Collector（journald） |
| VM101 `ocis`（192.168.1.52） | node_exporter、OTel Collector（journald） |
| ocms-ubuntu VPS（外部VPS） | Alloy、Komodo、OCMS。メトリクスを WireGuard 経由で LXC 200 へ送る |
| WireGuard | ocms-ubuntu `wg0` `10.0.0.1/24` ↔ pve2 `wg0` `10.0.0.3/32` |
| PVE pve3（192.168.1.76） | 現在停止中。ダッシュボードにはオフラインとして表示される |

公開 Grafana は `https://grafana.usbharu.dev`。Cloudflare Tunnel `lgtm-grafana` を
LXC 200 内の cloudflared で動かし、`http://grafana:3000` に転送している。
ログインは Pocket ID の SSO と、フォールバック用のローカル admin を併用する。

## データフロー

```
ocms-ubuntu Alloy ── WireGuard ── OTLP 4318 ─┐
PVE pve2 :9221 (pve-exporter) ───────────────┤
VM100/101 :9100 (node_exporter) ─────────────┼─ OTel Collector (LXC 200) ─┬─ Mimir (metrics)
PVE/VM journald ── OTLP 4317 ────────────────┘                              ├─ Loki  (logs)
                                                                            └─ Tempo (traces)
                                                                                │
                                                                            Grafana
```

- pve-exporter の収集に約20秒かかるため、スクレイプは 60秒間隔・50秒 timeout。
- node_exporter は 30秒間隔。`instance` ラベルはゲスト名（`picoclaw` / `ocis`）に正規化し、
  `vmid` ラベルも付与している。
- PVE ホストと VM の journald は全ログ（カーネル含む）を OTLP で LXC 200 に送る。
- ocms-ubuntu の Alloy は Ubuntu・Docker・OCMS の全メトリクスを OTLP/HTTP で LXC 200 に送る。
  ログとトレースは Grafana Cloud に残る。

## ocms-ubuntu からのメトリクス

ocms-ubuntu は自宅 LAN 上にないため、pve2 との間に WireGuard を張っている。

| 項目 | 値 |
| --- | --- |
| ocms-ubuntu `wg0` | `10.0.0.1/24`（既存の VPN ハブ） |
| pve2 `wg0` | `10.0.0.3/32` |
| VPS 側の経路 | `192.168.1.240/32` を `wg0` 経由 |
| pve2 側 | IPv4 forwarding を有効化し、`10.0.0.1` → `192.168.1.240` だけを `vmbr0` へ MASQUERADE |
| 送信先 | `http://192.168.1.240:4318`（LXC 200 の OTel Collector） |

設定例は `examples/wireguard-pve2.conf` と `examples/wireguard-vps-peer.conf`。
秘密鍵は各ホストの `/etc/wireguard/` に 0600 で置き、Git には入れない。

pve2 では `/etc/sysctl.d/99-wireguard-forward.conf` に `net.ipv4.ip_forward = 1` を設定する。
`wg-quick@wg0` の PostUp が、VPS (`10.0.0.1`) から LGTM (`192.168.1.240`) への通信だけを
許可する FORWARD と MASQUERADE を追加するため、再起動後も経路が復元される。

疎通確認:

```sh
# ocms-ubuntu
wg show
nc -vz 192.168.1.240 4318
sudo docker exec ocms-observability-alloy-1 bash -ec \
  'exec 3<>/dev/tcp/192.168.1.240/4318; echo ok'

# pve2
wg show
sysctl net.ipv4.ip_forward
iptables -t nat -S POSTROUTING | grep 10.0.0.0/24
```

## ダッシュボード

| ファイル | UID | データソース | 変数 | 内容 |
| --- | --- | --- | --- | --- |
| `dashboards/proxmox-ve.json` | `proxmox-ve` | `mimir` | `node` = `pve2` / `pve3` | PVE クラスタ、ノード、VM/LXC、ストレージ、SMART、センサー |
| `dashboards/node-exporter-full.json` | `node-exporter-full` | `mimir` | `node` = `picoclaw` / `ocis` | VM 内部の Linux（CPU、メモリ、ディスク、FS、ネットワーク、systemd） |
| `dashboards/journal-logs.json` | `journal-logs` | `loki` | `source` = `pve2-journald` / `vm100-journald` / `vm101-journald` | 選択したホスト/VM の journal とカーネルログ |

### 変数の意味

- **Proxmox VE の `node`** は PVE ホスト（`pve2` / `pve3`）。VM の選択ではない。
- **Linux - Node Exporter Full の `node`** は VM の `instance`（`picoclaw` / `ocis`）。
  `job` は `node` 固定。VM を切り替えるとその VM の Linux 内部メトリクスだけが見える。
- **Journal & Kernel Logs の `source`** は Loki の `service_name` の生値。
  カーネルログは `{service_name="$source"} | json | _TRANSPORT="kernel"` で絞り込む。

### ocms-ubuntu のダッシュボード

`stacks/observability/dashboards/` の2つは Grafana Cloud と pve-lgtm の両方で使える。
データソースを変数化してあるため、pve-lgtm では `mimir` / `loki` / `tempo` を選ぶ。

- `usbharulab-infra.json`: Ubuntu、Docker、Komodo、Alloy exporter のメトリクスとログ
- `usbharulab-ocms.json`: OCMS の HTTP、Job、コンテナ、ログ、トレース

この2つは `pve-lgtm/dashboards/` には複製せず、`stacks/observability/dashboards/` を
唯一の管理元にする。ocms-ubuntu のログとトレースは Grafana Cloud に残しているため、
pve-lgtm 側ではメトリクス以外のパネルは空になる。LXC へは次のようにコピーする。

```sh
scp stacks/observability/dashboards/usbharulab-infra.json \
    stacks/observability/dashboards/usbharulab-ocms.json root@192.168.1.250:/tmp/
ssh root@192.168.1.250 '
  for f in usbharulab-infra usbharulab-ocms; do
    pct push 200 /tmp/$f.json /opt/lgtm/config/grafana/provisioning/dashboards/$f.json --perms 644
  done
  pct exec 200 -- bash -c "cd /opt/lgtm && docker compose restart grafana"
'
```

### コミュニティダッシュボードの出典と改変

- `proxmox-ve.json` は Grafana.com [**24550**](https://grafana.com/grafana/dashboards/24550-proxmox-ve-pve-exporter/)（Proxmox VE - pve-exporter）を元にしている。
  - datasource 変数を削除し、`${datasource}` を `mimir` に置換。
  - `node` 変数の初期値を `pve2` に設定。
- `node-exporter-full.json` は Grafana.com [**1860**](https://grafana.com/grafana/dashboards/1860-node-exporter-full/)（Node Exporter Full）を元にしている。
  - `ds_prometheus` と `nodename` 変数を削除し、`${ds_prometheus}` を `mimir` に置換。
  - `job` の初期値を `node`、`node` のクエリを
    `label_values(node_uname_info{job="$job"}, instance)`、初期値を `picoclaw` に変更。
- `journal-logs.json` はこの環境用の自作。`source` は
  `value : label` 形式ではなく生値のカスタム変数にしている。
  Grafana は `value : label` 形式だと Loki クエリに表示ラベル側を展開するため、
  `{service_name="VM100 picoclaw"}` になって No data になる。

## プロビジョニング

Grafana のデータソース UID は `mimir`、`loki`、`tempo`。ダッシュボードは
UID を固定して `/etc/grafana/provisioning/dashboards` から読み込む。

ライブ環境は LXC 200 の `/opt/lgtm`:

- `/opt/lgtm/config/grafana/provisioning/datasources/datasources.yaml`
- `/opt/lgtm/config/grafana/provisioning/dashboards/provider.yaml`
- `/opt/lgtm/config/grafana/provisioning/dashboards/*.json`
- `/opt/lgtm/config/otel-collector.yaml`

このリポジトリの JSON はバックアップと差分管理用。反映するときは PVE ホスト経由で
LXC にコピーし、Grafana を再起動する。

```sh
scp pve-lgtm/dashboards/*.json root@192.168.1.250:/tmp/
ssh root@192.168.1.250 '
  for f in proxmox-ve node-exporter-full journal-logs; do
    pct push 200 /tmp/$f.json /opt/lgtm/config/grafana/provisioning/dashboards/$f.json --perms 644
  done
  pct exec 200 -- bash -c "cd /opt/lgtm && docker compose restart grafana"
'
```

`usbharulab-infra.json` と `usbharulab-ocms.json` は前述のとおり
`stacks/observability/dashboards/` からコピーする。

UI からインポートする場合も、データソース UID が `mimir` / `loki` / `tempo` と一致している必要がある。
Grafana の provider は `allowUiUpdates: true` だが、ファイルを更新すると上書きされるため、
恒久変更はこのリポジトリの JSON を直して再反映する。

## メトリクス収集

### PVE ホスト: Go pve-exporter

- バイナリ: `bigtcze/pve-exporter` v1.14.4（`/usr/local/bin/pve-exporter`）
- 設定: `/etc/pve-exporter/config.yml`（root:pve-exporter 0640）
- systemd: `pve-exporter.service`
- 認証: PVE ユーザー `monitoring@pve`、トークン `pve-exporter`、ロール `PVEAuditor`
- 例: `examples/pve-exporter-go.yaml`、`examples/pve-exporter.service`

SMART は [bigtcze/pve-exporter](https://github.com/bigtcze/pve-exporter) の `scripts/pve-smart-collector.sh` を `/usr/local/bin` に置き、
`/etc/cron.d/pve-smart-collector` で5分ごとに実行する。
SMART の `node` ラベルを Proxmox のノード名に合わせるため、スクリプト内の
`hostname -f` は `hostname -s` に変更している。
センサーは `lm-sensors` の `sensors -j` を読む。

### VM: node_exporter

Ubuntu 24.04 の `prometheus-node-exporter` パッケージを使う。
`/etc/default/prometheus-node-exporter` に `ARGS="--collector.systemd"` を設定し、
1860 の systemd パネルも表示できるようにしている。
LXC 200 の OTel Collector が `:9100` を 30秒ごとにスクレイプする。
例: `examples/central-collector.yaml`

### ocms-ubuntu: Alloy

Komodo 管理の `stacks/observability/` が動かす Alloy が、Ubuntu ホスト、Docker コンテナ、
OCMS のメトリクスを OTLP/HTTP で `192.168.1.240:4318` に送る。
Alloy の設定は `stacks/observability/alloy/config.alloy`。ログとトレースは
Grafana Cloud に送るため、pve-lgtm にはメトリクスだけが届く。

## ログ収集

PVE ホストと VM で `otelcol-contrib` の `journald` receiver を使う。
`units` は指定せず、全 journal（カーネル含む）を `priority: info` で送る。

- PVE ホストの設定例: `examples/pve-host-otelcol.yaml`
- VM の設定例: `examples/vm-otelcol.yaml`（`<VM_NAME>` / `<HOST_NAME>` を置換）
- 送信先: `192.168.1.240:4317`
- Loki の `service_name`: `pve2-journald`、`vm100-journald`、`vm101-journald`

PVE ホスト側は `systemd-journal` グループに `otelcol-contrib` を追加する。
VM 側も同様。`/var/log/journal` が無い場合は persistent journal を有効にするか、
`/run/log/journal` を指定する。

## 機密情報

リポジトリに実値を置かない。以下はホスト側で権限を絞って保存する。

| 場所 | 内容 |
| --- | --- |
| `/etc/pve-exporter/config.yml` | PVE API トークン。root:pve-exporter 0640 |
| `/etc/wireguard/wg0.conf`（pve2） | pve2 の秘密鍵と VPS の公開鍵。0600 |
| `/etc/wireguard/wg0.conf`（ocms-ubuntu） | VPS の秘密鍵と pve2 ピア。0600 |
| `/opt/lgtm/.env` | Grafana admin パスワード、Cloudflare Tunnel トークン、Pocket ID の client secret。0600 |
| `/root/lgtm-s3.env` | R2 の endpoint、bucket、access key、secret。0600 |
| `/var/lib/pve-exporter/smart.json` | SMART 収集結果。0644（exporter が読む） |

`examples/` の設定はプレースホルダのみ。`token_secret` や
`GF_SECURITY_ADMIN_PASSWORD` などの実値をコミットしない。

## 運用

```sh
# PVE exporter
systemctl status pve-exporter
curl -fsS http://127.0.0.1:9221/metrics | grep '^pve_exporter_up'

# LGTM
pct exec 200 -- bash -c 'cd /opt/lgtm && docker compose ps'
pct exec 200 -- bash -c 'cd /opt/lgtm && docker compose logs -f grafana'

# WireGuard と ocms-ubuntu メトリクス
wg show
pct exec 200 -- docker exec lgtm-mimir-1 wget -qO- \
  'http://127.0.0.1:9009/prometheus/api/v1/query?query=up'

# VM 側
qm guest exec 100 -- systemctl status prometheus-node-exporter otelcol-contrib
qm guest exec 101 -- systemctl status prometheus-node-exporter otelcol-contrib
```

Grafana の API でダッシュボードの読み込みを確認できる。

```sh
PASS=$(pct exec 200 -- grep GF_SECURITY_ADMIN_PASSWORD /opt/lgtm/.env | cut -d= -f2)
curl -s -u "admin:$PASS" http://127.0.0.1:3000/api/dashboards/uid/proxmox-ve | jq -r .dashboard.title
curl -s -u "admin:$PASS" http://127.0.0.1:3000/api/dashboards/uid/node-exporter-full | jq -r .dashboard.title
curl -s -u "admin:$PASS" http://127.0.0.1:3000/api/dashboards/uid/journal-logs | jq -r .dashboard.title
curl -s -u "admin:$PASS" http://127.0.0.1:3000/api/dashboards/uid/usbharulab-infra | jq -r .dashboard.title
curl -s -u "admin:$PASS" http://127.0.0.1:3000/api/dashboards/uid/usbharulab-ocms | jq -r .dashboard.title
```

## 注意点

- **pve3 は停止中**。Go exporter は pve3 への API 呼び出しで
  `API request failed with status: 595` を出すが、これは停止中のノードに対する
  想定内のエラー。ダッシュボードでは `node/pve3` がオフラインになる。
- **ZFS 行は空**。この PVE は ZFS プールを使っていない。
- **VM101 のディスクは 96%**。node_exporter / otelcol-contrib の導入で消費が増えている。
  ログは Loki に送っているので、必要なら `journalctl --vacuum-size=500M` でローカルを削減する。
- pve-exporter の収集は約20秒。スクレイプ間隔60秒・timeout50秒を下回らないようにする。
- 複数 PVE ホストの場合、API 系メトリクスはクラスタ単位で1台の exporter に集約する。
  各ホストの SMART/センサー/ZFS はローカル exporter が必要だが、同じクラスタで
  複数 exporter を動かすと API メトリクスが重複する。ローカル系だけを分離するか、
  `instance` で重複排除する設計が必要。
- Grafana の公開経路は LXC 200 の cloudflared に依存する。LXC が止まれば Grafana 自体も
  止まるため、追加の単一障害点にはならない。
- ocms-ubuntu のメトリクスは WireGuard に依存する。トンネルが切れると pve-lgtm 側の
  メトリクスだけ止まり、Grafana Cloud へのログ・トレースは継続する。
- OTLP の 4318 は LAN/VPN 内向け。インターネットへ公開しない。
- `stacks/observability/` の Alloy 設定は Komodo 管理、LGTM スタックは手動管理。
  メトリクスの送信先だけを共有し、デプロイ経路は混ぜない。

## 検証済み

2026-11-11 時点で、以下を確認している。

- 5ダッシュボード（PVE、Node Exporter Full、Journal、Ubuntu/Docker、OCMS）が
  Grafana にロードされ、UID とパネルが一致する
- `https://grafana.usbharu.dev/login` が 200、Pocket ID リダイレクトが 302
- PVE の `pve_node_up`、`pve_vm_status`、`pve_storage_used_fraction`、
  `pve_disk_health_status`、`pve_sensor_temperature_celsius` を Mimir で取得
- `picoclaw` / `ocis` の `node_cpu_seconds_total`、`node_memory_*`、
  `node_filesystem_*`、`node_systemd_unit_state` を Mimir で取得
- PVE ホスト、VM100、VM101 の journal とカーネルログを Loki で取得
- ocms-ubuntu の Alloy から WireGuard 経由で `up{instance="ocms-ubuntu"}`、
  Ubuntu/Docker メトリクス、OCMS の HTTP/Job/転送メトリクスを Mimir で取得
- `usbharulab-infra` / `usbharulab-ocms` の主要クエリが pve-lgtm の Mimir でデータを返す
