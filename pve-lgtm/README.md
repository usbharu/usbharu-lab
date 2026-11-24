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
| PVE pve2（192.168.1.250） | Go pve-exporter（systemd、`:9221`、クラスタ API と pve2 のローカルハードウェア）、OTel Collector（hostmetrics + journald）、WireGuard ゲートウェイ |
| PVE pve3（192.168.1.251） | Go pve-exporter（systemd、`:9221`、pve3 のローカルハードウェアのみ）、OTel Collector（hostmetrics + journald） |
| VM100 `picoclaw`（192.168.1.78） | node_exporter、OTel Collector（journald） |
| VM101 `ocis`（192.168.1.52） | node_exporter、OTel Collector（journald + oCIS ログ）、oCIS metrics sidecar、oCIS トレース |
| ocms-ubuntu VPS（外部VPS） | Alloy、Komodo、OCMS。メトリクス・ログ・トレースを WireGuard 経由で LXC 200 へ送る |
| VM110 `ocms-worker`（192.168.1.53） | OCMS remote worker/runtime。OTLP を LXC 200 へ送る |
| WireGuard | ocms-ubuntu `wg0` `10.0.0.1/24` ↔ pve2 `wg0` `10.0.0.3/32`、VM110 `10.0.0.4/32` |

公開 Grafana は `https://grafana.usbharu.dev`。Cloudflare Tunnel `lgtm-grafana` を
LXC 200 内の cloudflared で動かし、`http://grafana:3000` に転送している。
ログインは Pocket ID の SSO と、フォールバック用のローカル admin を併用する。

## データフロー

```
ocms-ubuntu Alloy ── WireGuard ── OTLP 4318 ─┐
VM110 ocms-worker ── OTLP 4318 ──────────────┤
VM101 oCIS metrics (sidecar) ── OTLP 4318 ───┤
PVE pve2 :9221 (cluster API + local) ────────┤
PVE pve3 :9221 (local hardware only) ────────┤
VM100/101 :9100 (node_exporter) ─────────────┼─ OTel Collector ─┬─ Mimir  (metrics) ─┐
VM101 oCIS traces ── OTLP 4317 ──────────────┤      (LXC 200)   ├─ Loki   (logs) ────┼─ Grafana
PVE/VM journald ── OTLP 4317 ────────────────┘                  ├─ Tempo  (traces) ──┘
                                                                └─ New Relic (metrics/logs/traces)
```

- pve-exporter の収集に約20秒かかるため、スクレイプは 60秒間隔・50秒 timeout。
- Proxmox API のメトリクスは pve2 の exporter に集約している。pve3 の exporter からは
  SMART、センサー、ディスク I/O、ZFS ARC などローカル系だけを Collector で残し、
  API 系の重複シリーズを落とす。
- node_exporter は 30秒間隔。`instance` ラベルはゲスト名（`picoclaw` / `ocis`）に正規化し、
  `vmid` ラベルも付与している。
- PVE ホスト（pve2 / pve3）と VM の journald は全ログ（カーネル含む）を OTLP で LXC 200 に送る。
- ocms-ubuntu の Alloy は Ubuntu・Docker・Komodo・OCMS のメトリクス・ログ・トレースを
  OTLP/HTTP で LXC 200 に送る。Grafana Cloud は使用しない。

## New Relic への送信

LXC 200 の中央 Collector から New Relic（US リージョン）へ、メトリクスと
warn 以上のログを送る。Mimir / Loki / Tempo への保存は全量のまま。
トレースは Tempo のみに保存し、New Relic へは送らない。

| シグナル | New Relic に届くもの | 届かないもの |
| --- | --- | --- |
| メトリクス | PVE、VM、oCIS、ocms-ubuntu（Alloy）、LGTM 自身 | なし |
| ログ | warn 以上のログ（PVE/VM の journald、oCIS、Komodo、OCMS） | info/debug ログ（Grafana Loki には全量） |
| トレース | 送らない | すべて（Grafana Tempo には全量） |

`logs/newrelic` pipeline は `filter/newrelic_logs` で
`severity_number < SEVERITY_NUMBER_WARN` を落とす。`traces/tempo` pipeline は
New Relic exporter を持たない。アカウントが HTTP 402 を返す間は New Relic 側で
受信できないため、`docker logs lgtm-otel-collector-1 | grep 402` で確認する。

### キーの作成

1. New Relic にログインし、[API keys](https://one.newrelic.com/api-keys) を開く。
   EU アカウントは `https://one.eu.newrelic.com/api-keys`。
2. **Create a key** を押し、Key type で **Ingest - License** を選ぶ。
   OTLP では User キーや Browser キーは使えない。
3. 名前（例: `usbharu-lab-otlp`）を入れて作成する。
4. 作成直後のバナーにキー全体が一度だけ表示される。**Copy key** でコピーする。
   40桁の16進数で、閉じると再表示できない。紛失したら新しいキーを作る。
5. キーはアカウント単位。データを入れたいアカウントで作る。
   US は `one.newrelic.com`、EU は `one.eu.newrelic.com` でログインする。

キー作成には API キーの作成権限が必要。ボタンが無い場合はアカウント管理者に確認する。
アカウント最初のキーは削除できないため、用途別に名前付きのキーを作ってローテーションする。

### 設定

Collector 側は `examples/central-collector.yaml` の `otlphttp/newrelic`。
`https://otlp.nr-data.net:4318` に `api-key` ヘッダーでライセンスキーを付け、
キーは Collector の環境変数 `NEW_RELIC_LICENSE_KEY` から読む。

1. `/opt/lgtm/.env` にキーを追記する（0600 のまま）。

   ```dotenv
   NEW_RELIC_LICENSE_KEY=REPLACE_WITH_LICENSE_KEY
   ```

2. `/opt/lgtm` の Compose で `otel-collector` に環境変数を渡す。`.env` は Compose の
   変数展開に使われるだけで、`environment` か `env_file` に書かないとコンテナには入らない。
   すでに `env_file: .env` を指定している場合は追記だけでよい。

   ```yaml
   services:
     otel-collector:
       environment:
         NEW_RELIC_LICENSE_KEY: ${NEW_RELIC_LICENSE_KEY}
   ```

3. 設定をコピーし、Collector を作り直す。`restart` では環境変数の変更が反映されないため
   `up -d` を使う。

   ```sh
   scp pve-lgtm/examples/central-collector.yaml root@192.168.1.250:/tmp/
   ssh root@192.168.1.250 '
     pct push 200 /tmp/central-collector.yaml /opt/lgtm/config/otel-collector.yaml --perms 644
     pct exec 200 -- bash -c "cd /opt/lgtm && docker compose up -d otel-collector"
   '
   ```

`NEW_RELIC_LICENSE_KEY` が未設定だと Collector は設定解決に失敗して起動しない。
その間は Mimir / Loki / Tempo への取り込みも止まるため、先に `.env` と Compose を更新する。

### 確認

Collector が起動し、New Relic 向けの送信が成功しているかを確認する。

```sh
pct exec 200 -- bash -c 'cd /opt/lgtm && docker compose ps otel-collector'
pct exec 200 -- docker logs --since 10m lgtm-otel-collector-1 2>&1 | grep -Ei 'newrelic|error'
```

New Relic UI では、反映直後から次のクエリで件数を確認できる（反映には数分かかることがある）。

```sql
FROM Metric SELECT count(*) SINCE 1 hour ago
FROM Log SELECT count(*) SINCE 1 hour ago
FROM Span SELECT count(*) SINCE 1 hour ago
```

`otelcol_exporter_send_failed_*{exporter="otlphttp/newrelic"}` が増え続ける場合は、
キーの種類（Ingest - License）、リージョン（US は `otlp.nr-data.net`）、
LXC 200 から `otlp.nr-data.net:443` への HTTPS 疎通を確認する。

New Relic は取り込み量で課金される。ログ量やメトリクスのカーディナリティが大きい場合は、
New Relic 専用の pipeline を分けて `filter` processor で削る。既存の Mimir / Loki / Tempo 向け
pipeline に filter を足すと、Grafana 側のデータも減る。

2026-11-19 に LXC 200 へ反映し、Collector の自己メトリクスで送信成功まで確認した。
New Relic 側の受信は上記の NRQL で確認する。

## ocms-ubuntu からのテレメトリ

ocms-ubuntu は PVE のゲストではなく外部 VPS。自宅 LAN 上にないため、pve2 との間に
WireGuard を張っている。pve2 はトンネルの入口と LGTM への転送だけを担う。

| 項目 | 値 |
| --- | --- |
| ocms-ubuntu `wg0` | `10.0.0.1/24`（既存の VPN ハブ） |
| pve2 `wg0` | `10.0.0.3/32` |
| VPS 側の経路 | `192.168.1.240/32` を `wg0` 経由 |
| pve2 側 | IPv4 forwarding を有効化し、`10.0.0.1` → `192.168.1.240` だけを `vmbr0` へ MASQUERADE |
| 送信先 | `http://192.168.1.240:4318`（LXC 200 の OTel Collector） |

設定例は `examples/wireguard-pve2.conf` と `examples/wireguard-vps-peer.conf`。
秘密鍵は各ホストの `/etc/wireguard/` に 0600 で置き、Git には入れない。
両ホストで `systemctl enable --now wg-quick@wg0` を実行し、サービスとして管理する。

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
| `dashboards/journal-logs.json` | `journal-logs` | `loki` | `source` = `pve2-journald` / `pve3-journald` / `vm100-journald` / `vm101-journald` / `db-pve3-journald` / `ocms-pve3-journald` / `postgresql` | 選択したホスト/VM の journal、PostgreSQL、カーネルログ |
| `dashboards/ocis.json` | `ocis` | `mimir` / `loki` / `tempo` | `metrics` / `logs` / `traces`（datasource） | oCIS のリクエスト、エラー、レイテンシ、転送、CPU、メモリ、ログ、トレース |
| `dashboards/lgtm.json` | `lgtm` | `mimir` / `loki` | `metrics` / `logs`（datasource） | LGTM のデータ量、バージョン、テナント、ストレージ、Grafana レイテンシ、遅い handler/route、コンポーネント資源 |
| `dashboards/opentelemetry-collector.json` | `BKf2sowmj` | `mimir` | `datasource` / `job` など | OpenTelemetry Collector 公式ダッシュボード（Grafana.com 15983） |

PVE 用の3つは `PVE`、oCIS と ocms-ubuntu 用の3つは `usbharu-lab`、LGTM 自身は
`LGTM` フォルダにプロビジョニングする。

LGTM フォルダには、自作の `LGTM Stack` と、Grafana.com の公式
`OpenTelemetry Collector`（ID 15983）を置いている。LGTM 全体のデータ量と
Grafana の遅さは自作側、Collector の receiver/processor/exporter 詳細は公式側で見る。
コミュニティの `LGTM Stats`（ID 25700）は Kubernetes の `cluster` / `namespace` /
`job` ラベルと Mimir/Loki/Tempo mixin の recording rules を前提にしており、
この単一ノード構成では大半が No data になるため採用していない。ただし、
バージョン・テナント数・保存量・遅い処理の見せ方は自作ダッシュボードに取り込んでいる。

### 変数の意味

- **Proxmox VE の `node`** は PVE ホスト（`pve2` / `pve3`）。VM の選択ではない。
- **Linux - Node Exporter Full の `node`** は VM の `instance`（`picoclaw` / `ocis`）。
  `job` は `node` 固定。VM を切り替えるとその VM の Linux 内部メトリクスだけが見える。
- **Journal & Kernel Logs の `source`** は Loki の `service_name` の生値。
  `ocis` を選ぶと oCIS コンテナのログだけを表示する。`db-pve3-journald` /
  `ocms-pve3-journald` / `postgresql` も選択できる。
  カーネルログは `{service_name="$source"} | json | _TRANSPORT="kernel"` で絞り込む。

### ocms-ubuntu のダッシュボード

`stacks/observability/dashboards/` の2つは pve-lgtm の Grafana で使う。
データソースを変数化してあるため、`mimir` / `loki` / `tempo` を選ぶ。

- `usbharulab-infra.json`: Ubuntu、Docker、Komodo、Alloy exporter のメトリクスとログ
- `usbharulab-ocms.json`: OCMS の HTTP、Job、コンテナ、ログ、トレース

この2つは `pve-lgtm/dashboards/` には複製せず、`stacks/observability/dashboards/` を
唯一の管理元にする。ocms-ubuntu のログとトレースも pve-lgtm に届くため、
全パネルを同じ Loki / Tempo で表示できる。LXC へは次のようにコピーする。

```sh
scp stacks/observability/dashboards/usbharulab-infra.json \
    stacks/observability/dashboards/usbharulab-ocms.json root@192.168.1.250:/tmp/
ssh root@192.168.1.250 '
  for f in usbharulab-infra usbharulab-ocms; do
    pct push 200 /tmp/$f.json /opt/lgtm/config/grafana/provisioning/dashboards/usbharu-lab/$f.json --perms 644
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
UID を固定し、`provider.yaml` で PVE、usbharu-lab、LGTM の3フォルダに分けて読み込む。

ライブ環境は LXC 200 の `/opt/lgtm`:

- `/opt/lgtm/config/grafana/provisioning/datasources/datasources.yaml`
- `/opt/lgtm/config/grafana/provisioning/dashboards/provider.yaml`
- `/opt/lgtm/config/grafana/provisioning/dashboards/pve/*.json`
- `/opt/lgtm/config/grafana/provisioning/dashboards/usbharu-lab/*.json`
- `/opt/lgtm/config/grafana/provisioning/dashboards/lgtm/*.json`
- `/opt/lgtm/config/otel-collector.yaml`

このリポジトリの JSON はバックアップと差分管理用。反映するときは PVE ホスト経由で
LXC にコピーし、Grafana を再起動する。PVE 用の3つは `pve/`、oCIS と ocms-ubuntu 用の
3つは `usbharu-lab/`、LGTM 用の1つは `lgtm/` に置く。

```sh
scp pve-lgtm/dashboards/*.json root@192.168.1.250:/tmp/
ssh root@192.168.1.250 '
  for f in proxmox-ve node-exporter-full journal-logs; do
    pct push 200 /tmp/$f.json /opt/lgtm/config/grafana/provisioning/dashboards/pve/$f.json --perms 644
  done
  pct push 200 /tmp/ocis.json /opt/lgtm/config/grafana/provisioning/dashboards/usbharu-lab/ocis.json --perms 644
  pct push 200 /tmp/lgtm.json /opt/lgtm/config/grafana/provisioning/dashboards/lgtm/lgtm.json --perms 644
  pct push 200 /tmp/opentelemetry-collector.json /opt/lgtm/config/grafana/provisioning/dashboards/lgtm/opentelemetry-collector.json --perms 644
  pct exec 200 -- bash -c "cd /opt/lgtm && docker compose restart grafana"
'
```

`usbharulab-infra.json` と `usbharulab-ocms.json` は前述のとおり
`stacks/observability/dashboards/` から `usbharu-lab/` へコピーする。

UI からインポートする場合も、データソース UID が `mimir` / `loki` / `tempo` と一致している必要がある。
Grafana の provider は `allowUiUpdates: true` だが、ファイルを更新すると上書きされるため、
恒久変更はこのリポジトリの JSON を直して再反映する。

## メトリクス収集

### PVE ホスト: Go pve-exporter

pve2 と pve3 の両方で同じ exporter を動かす。

- バイナリ: `bigtcze/pve-exporter` v1.14.4（`/usr/local/bin/pve-exporter`）
- 設定: `/etc/pve-exporter/config.yml`（root:pve-exporter 0640）
- systemd: `pve-exporter.service`
- 認証: PVE ユーザー `monitoring@pve`、トークン `pve-exporter`、ロール `PVEAuditor`
- 例: `examples/pve-exporter-go.yaml`、`examples/pve-exporter.service`

pve2 は `proxmox.host` を `192.168.1.250` にしてクラスタ全体の API メトリクスを担当する。
pve3 は `proxmox.host` を `192.168.1.251` にし、Collector の `pve-local` ジョブで
`pve_(disk|sensor|zfs_arc)_.*|pve_exporter_.*|up` だけを残す。
同じクラスタで API を二重に取ると `pve_node_vm_count` などの集計が倍になるため、
pve3 からはローカルハードウェアのシリーズだけを使う。

SMART は [bigtcze/pve-exporter](https://github.com/bigtcze/pve-exporter) の `scripts/pve-smart-collector.sh` を
pve2 と pve3 の `/usr/local/bin` に置き、`/etc/cron.d/pve-smart-collector` で5分ごとに実行する。
SMART の `node` ラベルを Proxmox のノード名に合わせるため、スクリプト内の
`hostname -f` は `hostname -s` に変更している。
センサーは `lm-sensors` の `sensors -j` を読む。pve3 は HP ProLiant DL360 Gen9 のため、
`lm-sensors` では CPU（coretemp）、NIC（tg3）、消費電力（power_meter）を取得する。

### VM: node_exporter

Ubuntu 24.04 の `prometheus-node-exporter` パッケージを使う。
`/etc/default/prometheus-node-exporter` に `ARGS="--collector.systemd"` を設定し、
1860 の systemd パネルも表示できるようにしている。
LXC 200 の OTel Collector が `:9100` を 30秒ごとにスクレイプする。
例: `examples/central-collector.yaml`

### VM101: oCIS

oCIS はコンテナ内の `127.0.0.1:9205/metrics` に Prometheus メトリクスを出す。
`ocis-metrics` サイドカーを `network_mode: "service:ocis"` で動かし、同じネットワーク
名前空間からスクレイプして pve-lgtm に OTLP/HTTP で送る。9205 は LAN に公開しない。

- 設定例: `examples/ocis-metrics-collector.yaml`
- ライブ設定: `/home/ubuntu/ocis/ocis-metrics-collector.yaml` と
  `/home/ubuntu/ocis/compose.override.yaml`
- 送信先: `http://192.168.1.240:4318`
- ラベル: `job="ocis"`、`instance="ocis"`
- 主なメトリクス: `ocis_proxy_requests_total`、`ocis_proxy_errors_total`、
  `ocis_proxy_duration_seconds`、`ocis_ocdav_http_requests_total`、
  `reva_upload_active`、`reva_download_active`
- ダッシュボード: `dashboards/ocis.json`（Grafana の `usbharu-lab` フォルダ）

#### oCIS ログ

oCIS コンテナは Docker の `journald` ログドライバで動かす。ホストの
`otelcol-contrib` が journal を読み、`CONTAINER_NAME=ocis` のエントリだけを
`service.name=ocis` に変換して Loki へ送る。Loki では `{service_name="ocis"}` で
検索でき、`journal-logs` ダッシュボードの `source` に `ocis` を追加している。

ログ本文は journald の長いラッパーではなく、oCIS が出力した JSON メッセージに
置き換える。また、`user ... authenticated`、`listing public shares`、
`list public share`、`/status.php`、`/ocs/v2.php/cloud/capabilities` などの
定型ノイズは Collector の `filter/docker_noise` で落とす。

ホスト側の設定は `examples/vm-otelcol.yaml` の `transform/docker` と
`filter/docker_noise` を参照する。

#### oCIS トレース

oCIS の OTLP トレースを LXC 200 の OTel Collector に直接送る。

```dotenv
OCIS_TRACING_ENABLED=true
OCIS_TRACING_TYPE=otlp
OCIS_TRACING_ENDPOINT=192.168.1.240:4317
```

トレースは Tempo に保存され、`oCIS` ダッシュボードの「遅いリクエストのトレース」
パネル（`{ duration > 100ms }`）から確認できる。サービス名は `proxy`、`gateway`、
`storage-users` など oCIS の内部サービス名になる。

### ocms-ubuntu: Alloy

Komodo 管理の `stacks/observability/` が動かす Alloy が、Ubuntu ホスト、Docker コンテナ、
Komodo、OCMS のメトリクス・ログ・トレースを OTLP/HTTP で `192.168.1.240:4318` に送る。
Alloy の設定は `stacks/observability/alloy/config.alloy`。Grafana Cloud は使用しない。

### LGTM スタック

中央 Collector が Mimir、Loki、Tempo、Grafana、OTel Collector 自身の `/metrics` を
30秒ごとにスクレイプし、Mimir に保存する。`lgtm` ダッシュボードでは次を見られる。

- シリーズ数が多いメトリクス Top 20
- Active series、samples/s、series 作成数
- ログ行数・転送量、ログ量の多い service
- Tempo の spans/s・転送量
- Grafana の handler 別リクエスト数・p95、`/api/ds/query` p95、5xx 率
- Mimir / Loki / Tempo の query p95（route 別）
- コンポーネントの CPU・メモリ、OTel Collector の queue・送信失敗
- コンポーネントのバージョン
- テナント数、Grafana の Org・ユーザー・ダッシュボード・データソース数
- Loki / Tempo の保存量、Mimir compactor の最終成功・失敗
- 遅い Grafana handler / Mimir・Loki・Tempo route の Top 10 テーブル

自己監視のスクレイプ設定は `examples/central-collector.yaml` の `prometheus/lgtm`。
Collector の receiver/processor/exporter 詳細は、Grafana.com の公式
`OpenTelemetry Collector` ダッシュボード（ID 15983）で見る。
Grafana 13.2.6 の `/metrics` にはデータソース別のレイテンシが出ないため、
Grafana の遅さは handler 別 p95 と `/api/ds/query` の p95 を代理指標にしている。

### Tempo の R2 設定

Cloudflare R2 は multipart upload の非最終パートのサイズがすべて同じであることを
要求する。Tempo は `storage.trace.s3.part_size` が未設定だと Parquet の row group 単位で
可変サイズのパートを送るため、compaction が次のエラーで失敗する。

```text
error completing multipart upload ... All non-trailing parts must have the same length
```

失敗したアップロードのパートは R2 に残り、オブジェクト一覧には現れないまま
保存量だけが増える。`tempodb_backend_bytes_total` は完了済みブロックしか数えないため、
Tempo 側のメトリクスでは気づけない。

`part_size` を 100 MiB に固定するとパート長が揃い、compaction が通る。

```yaml
storage:
  trace:
    s3:
      part_size: 104857600
```

既に残っている未完了アップロードは R2 の既定（7日）で消える。すぐ回収する場合は
`aws s3api list-multipart-uploads` で確認し、`abort-multipart-upload` で削除する。

### Tempo の保存量削減

oCIS のトレースは gateway の内部ヘルパー span が大半を占める。リクエスト単位の
調査には不要なため、中央 Collector の `filter/ocis_noise` で次の span を落とす。

- `gateway`: `loadAttributes`、`ReadNode`、`ParentWithReader`、`Type`、`Child`、
  `getNodeIDFromCache`、`GetTreeSize`
- `ocdav`: `md_to_prop_response`

2026-11-19 時点で spans/s は約81から約33へ減り、完了ブロックは19GB/週から
約8GB/週になる見込み。内部処理を追いたいときは、該当する条件を
`examples/central-collector.yaml` から外して Collector を再起動する。

## ログ収集

PVE ホストと VM で `otelcol-contrib` の `journald` receiver を使う。
`units` は指定せず、全 journal（カーネル含む）を `priority: info` で送る。
oCIS コンテナのログは Docker の `journald` ログドライバ経由で同じ journal に入り、
`CONTAINER_NAME=ocis` のエントリは `service.name=ocis` として Loki に送られる。

- PVE ホストの設定例: `examples/pve-host-otelcol.yaml`（`<PVE_HOST>` を `pve2` / `pve3` に置換）
- VM の設定例: `examples/vm-otelcol.yaml`（`<VM_NAME>` / `<HOST_NAME>` を置換）
- 送信先: `192.168.1.240:4317`
- Loki の `service_name`: `pve2-journald`、`pve3-journald`、`vm100-journald`、`vm101-journald`

PVE ホスト側は `systemd-journal` グループに `otelcol-contrib` を追加する。
VM 側も同様。`/var/log/journal` が無い場合は persistent journal を有効にするか、
`/run/log/journal` を指定する。

### pve3 の DB / OCMS VM

`db-pve3`（VM121）と `ocms-pve3`（VM122）は、それぞれ `otelcol-contrib` を
systemd で動かして中央 Collector へ送る。

| VM | 収集するもの | `service_name` |
| --- | --- | --- |
| `db-pve3` | journald、`/var/log/postgresql/*.log`、hostmetrics | `db-pve3-journald`、`postgresql`、`db-pve3-host` |
| `ocms-pve3` | OCMS の OTLP（`192.168.1.254:4318`）、journald、hostmetrics | アプリの `service.name`、`ocms-pve3-journald`、`ocms-pve3-host` |

`ocms-pve3` の Collector は OCMS コンテナから OTLP を受け、`host.name=ocms-pve3`
と `service.namespace=usbharu-lab` を付けて中央 Collector へ転送する。これにより
中央 Collector の `transform/remote_worker` が pve3 の worker を VM110 の worker
として扱わない。OCMS の Compose は
`OTEL_EXPORTER_OTLP_ENDPOINT=http://192.168.1.254:4318` を指定する。

設定例: `examples/db-pve3-otelcol.yaml`、`examples/ocms-pve3-otelcol.yaml`。
`qemu-ga` の定期ログと `user-runtime-dir` のログは `filter/noise` で落とす。
`otelcol-contrib` は `systemd-journal` と `adm` グループに追加する（DB の
PostgreSQL ログ用）。Grafana では `{host_name="db-pve3"}` や
`{host_name="ocms-pve3"}` で検索できる。New Relic へは中央 Collector の
既存の `otlphttp/newrelic` が同じログを転送する。

## 機密情報

リポジトリに実値を置かない。以下はホスト側で権限を絞って保存する。

| 場所 | 内容 |
| --- | --- |
| `/etc/pve-exporter/config.yml`（pve2 / pve3） | PVE API トークン。root:pve-exporter 0640 |
| `/etc/wireguard/wg0.conf`（pve2） | pve2 の秘密鍵と VPS の公開鍵。0600 |
| `/etc/wireguard/wg0.conf`（ocms-ubuntu） | VPS の秘密鍵と pve2 ピア。0600 |
| `/opt/lgtm/.env` | Grafana admin パスワード、Cloudflare Tunnel トークン、Pocket ID の client secret、New Relic ライセンスキー。0600 |
| `/root/lgtm-s3.env` | R2 の endpoint、bucket、access key、secret。0600 |
| `/var/lib/pve-exporter/smart.json` | SMART 収集結果。0644（exporter が読む） |

`examples/` の設定はプレースホルダのみ。`token_secret` や
`GF_SECURITY_ADMIN_PASSWORD` などの実値をコミットしない。

## 運用

```sh
# PVE exporter
systemctl status pve-exporter
curl -fsS http://127.0.0.1:9221/metrics | grep '^pve_exporter_up'

# PVE exporter / OTel Collector on pve3
ssh root@192.168.1.251 'systemctl status pve-exporter otelcol-contrib'
ssh root@192.168.1.251 'curl -fsS http://127.0.0.1:9221/metrics | grep -E "^pve_(exporter_up|disk_health_status|sensor_temperature_celsius)"'

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

- **pve3 のセンサー**。HP ProLiant DL360 Gen9 では `lm-sensors` が
  coretemp（CPU）、tg3（NIC）、power_meter（消費電力）を読む。
  ディスク温度は SMART から取得する。
- **ZFS 行は空**。この PVE は ZFS プールを使っていない。
- **VM101 のディスクは 96%**。node_exporter / otelcol-contrib の導入で消費が増えている。
  ログは Loki に送っているので、必要なら `journalctl --vacuum-size=500M` でローカルを削減する。
- pve-exporter の収集は約20秒。スクレイプ間隔60秒・timeout50秒を下回らないようにする。
- 複数 PVE ホストの API 系メトリクスはクラスタ単位で1台の exporter に集約する。
  `biwako` クラスタでは pve2 が担当し、pve3 の exporter は Collector 側で
  ローカル系だけに絞っている。別クラスタを追加するときは、そのクラスタにも
  API 担当の exporter を1台置き、他のノードはローカル系だけを残す。
- Grafana の公開経路は LXC 200 の cloudflared に依存する。LXC が止まれば Grafana 自体も
  止まるため、追加の単一障害点にはならない。
- ocms-ubuntu の全シグナルは WireGuard に依存する。トンネルが切れると pve-lgtm への
  メトリクス・ログ・トレースが止まり、Alloy のメモリキューに溜まった後に破棄される。
- OTLP の 4318 は LAN/VPN 内向け。インターネットへ公開しない。
- New Relic への送信は追加の外部依存。`sending_queue` はメモリ上で、再試行の上限を
  超えたバッチは落ちる。New Relic 障害中も Mimir / Loki / Tempo への保存は続くが、
  Collector のキューが埋まる分だけメモリ使用量が増える。
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
- oCIS の `ocis_proxy_requests_total`、`ocis_proxy_errors_total`、
  `ocis_proxy_duration_seconds`、`ocis_frontend_http_requests_total`、
  `ocis_ocdav_http_requests_total`、`reva_upload_active`、`process_*` を Mimir で取得
- oCIS コンテナログを Loki の `{service_name="ocis"}` で取得
- oCIS のトレースを Tempo で取得（`proxy`、`gateway` などのサービス名）
- `ocis` ダッシュボードを Grafana の `usbharu-lab` フォルダにロードし、
  メトリクス・ログ・トレースのパネルを確認
- `up{job=~"mimir|loki|tempo|grafana|otelcol"}` がすべて1
- `lgtm` ダッシュボードを Grafana の `LGTM` フォルダにロード
- シリーズ数 Top 20、Grafana handler 別 p95、Mimir / Loki / Tempo の query p95 が
  データを返すことを確認
- Grafana.com 公式の `OpenTelemetry Collector` ダッシュボード（15983）を
  `LGTM` フォルダにロード
- `otelcol_process_uptime`、`otelcol_processor_incoming_items`、
  `otelcol_exporter_sent_metric_points` を Mimir で取得

### pve3 追加時（2026-11-17）

- pve3（192.168.1.251）の pve-exporter が `pve_exporter_up 1` を返す
- pve3 の SMART 8台（`sda`〜`sdh`）を `pve_disk_health_status` で取得
- pve3 の CPU/NIC 温度と消費電力を `pve_sensor_temperature_celsius` /
  `pve_sensor_power_watts` で取得
- `up{job="pve-local"}` が 1、`system_cpu_logical_count{job="pve3-hostmetrics"}` が 16
- Loki の `{service_name="pve3-journald"}` で pve3 の journal を取得
- `pve_node_up{node="pve3"}` が 1、`count(pve_node_vm_count)` が 2 のままで
  API メトリクスの二重化がない
- `journal-logs` ダッシュボードの `source` に `pve3-journald` が表示される

### New Relic 追加時（2026-11-19）

- `.env` のキー名を `NEW_RELIC_LICENSE_KEY` に修正し、`docker-compose.override.yml` の
  `otel-collector` に環境変数として渡した
- Collector を再作成し、`otlphttp/newrelic` exporter が metrics / logs / traces の
  3 pipeline で起動した
- `otelcol_exporter_sent_metric_points`、`otelcol_exporter_sent_log_records`、
  `otelcol_exporter_sent_spans` の `exporter="otlphttp/newrelic"` が増加した
- `otelcol_exporter_send_failed_*` と `otelcol_exporter_queue_size` は 0 のまま
- 同じ時点の Tempo 向け累計 span 数と New Relic 向け累計 span 数が一致した
- Mimir / Loki / Tempo への送信は継続している
- New Relic UI での受信確認は NRQL で行う（Collector 側は HTTP 送信成功まで確認済み）

### Grafana Cloud 廃止（2026-11-19）

- Alloy から Grafana Cloud の exporter と認証情報を削除し、全シグナルを PVE LGTM に切り替えた
- Alloy v1.21.2 の `alloy validate` が通った
- Komodo が observability スタックを反映し、Alloy が再起動した
- pve-lgtm Loki に Komodo ログ（`service_name=komodo-core` など、
  `host_name=ocms-ubuntu`）が入った
- Tempo に VPS worker のトレース（`resource.host.name=ocms-ubuntu`）が入った
- Mimir のメトリクス受信は継続している
- 中央 Collector の Mimir / Loki / Tempo 向け送信数と New Relic 向け送信数が一致し、
  `otelcol_exporter_send_failed_*` と queue は 0 のまま
- 反映直後に Tempo が約10秒停止したが、OOM ではなく、その後は正常に受信している

### Tempo の R2 未完了アップロード回収（2026-11-19）

- R2 の `lgtm-tempo` が1週間で約1TB増えていたが、Tempo の完了済みブロックは約19GBだった
- 原因は compaction の `All non-trailing parts must have the same length` 失敗で、
  未完了の multipart upload が5,465件（平均約202MiB、合計約1.08TiB）残っていたこと
- `storage.trace.s3.part_size: 104857600` を `tempo.yaml.tmpl` に追加して再生成し、
  Tempo を再起動した。再起動後は同じエラーが発生していない
- 修正前に開始された未完了アップロード5,465件を `abort-multipart-upload` で削除した
- 再起動後も進行中のアップロードは残るが、完了後にオブジェクトとして確定する
- oCIS の内部 span 8種を `filter/ocis_noise` で削除し、spans/s が約81から約33へ減った
- Tempo から `loadAttributes`、`ReadNode`、`md_to_prop_response` などが消えたことを確認
- `otelcol_processor_filter_spans_filtered{filter="filter/ocis_noise"}` が約49 spans/sで増加している
- 2026-11-19 に Tempo の R2 バケットとローカル WAL を全削除し、トレース履歴をリセットした
- パージ後、古い時間帯のトレースは0件、直近のトレースは取得できることを確認した
