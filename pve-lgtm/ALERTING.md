# Grafana Alerting

Grafana のアラート設定を Git で管理するための手順。対象は LXC 200 の LGTM スタックで、
自動同期はしない。変更は必ずこのリポジトリへコミットし、
`scripts/apply-grafana-alerting.sh` を手動で実行して反映する。

- 設定の管理元: `pve-lgtm/provisioning/alerting/*.yaml`
- 通知先: Discord Webhook（URL は Git に置かない）
- 適用先: LXC 200 `/opt/lgtm/config/grafana/provisioning/alerting/`
- Grafana: 13.2.6 OSS、Grafana-managed alerting、Mimir / Loki datasource

## 設計

```text
Git (pve-lgtm/provisioning/alerting/*.yaml)
        │  scripts/apply-grafana-alerting.sh（手動）
        ▼
LXC 200 /opt/lgtm/config/grafana/provisioning/alerting/
        │  provisioning reload
        ▼
Grafana Alerting ── Mimir (PromQL) / Loki (LogQL)
        │
        └── Discord Webhook
```

file provisioning で読み込んだアラートルール・コンタクトポイント・通知ポリシーは
Grafana の UI から編集できない。ダッシュボードも `allowUiUpdates: false` にして
読み取り専用にしている。変更は必ず Git の YAML / JSON を直し、スクリプトで反映する。

Grafana が停止するとアラート評価と通知も止まる。外部からの死活監視（#148）は
この仕組みの外側に必要で、まだ実装していない。

## 前提

### 1. Discord Webhook

1. Discord のチャンネル設定 → 連携サービス → Webhook → 新規作成。
2. URL をコピーする。
3. LXC 200 の `/opt/lgtm/.env`（0600）へ追記する。

   ```dotenv
   GRAFANA_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

4. Grafana サービスへ環境変数を渡す。`/opt/lgtm` の compose はこのリポジトリの
   管理外なので、ライブの `docker-compose.yml` を直接編集する。

   ```yaml
   services:
     grafana:
       environment:
         GRAFANA_DISCORD_WEBHOOK_URL: ${GRAFANA_DISCORD_WEBHOOK_URL}
   ```

5. Grafana を再作成する。

   ```sh
   pct exec 200 -- bash -c 'cd /opt/lgtm && docker compose up -d grafana'
   pct exec 200 -- bash -c 'cd /opt/lgtm && docker compose exec -T grafana printenv GRAFANA_DISCORD_WEBHOOK_URL'
   ```

URL は Webhook を知っている誰でも投稿できる秘密情報として扱う。漏洩したら Discord 側で
再生成し、`.env` と Grafana を更新する。Git には絶対に追加しない。

既存の UI 作成の `Discord` contact point から移行した場合、`discord-alerts` の動作を
確認したあとに古い `Discord` を削除してよい。通知ポリシーは `discord-alerts` だけを
参照する。

### 2. LXC 200 の node_exporter

`LgtmRootfs` と `TargetMissing` の node 期待数（3）に必要。

```sh
pct exec 200 -- apt-get update
pct exec 200 -- apt-get install -y prometheus-node-exporter
pct exec 200 -- systemctl enable --now prometheus-node-exporter
pct exec 200 -- curl -fsS http://127.0.0.1:9100/metrics | head
```

LXC 200 の node_exporter は LAN 内からのみ到達できるようにする。LXC 200 の
`/` と Docker ボリュームが別ファイルシステムの場合は、`LgtmRootfs` の
`mountpoint` を実際のデータ用 mountpoint に合わせる。

`pve-lgtm/examples/central-collector.yaml` の `prometheus/vms` には
`192.168.1.240:9100`（`vm=lgtm`, `vmid=200`）を追加済み。ライブの
`/opt/lgtm/config/otel-collector.yaml` へ反映し、Collector を再作成する。

### 3. Alloy（ocms-ubuntu）

`container_start_time_seconds` / `container_last_seen` と、Alloy 自身の
`otelcol_exporter_queue_size` / `otelcol_exporter_queue_capacity` を残すよう
`stacks/observability/alloy/config.alloy` を変更済み。既存の Komodo GitOps
Procedure で observability スタックをデプロイする。

反映後、Mimir で次を確認する。

```promql
count(container_start_time_seconds)
count(otelcol_exporter_queue_capacity)
```

`container_start_time_seconds` が 0 件の場合は、cAdvisor の metric kind か
Docker イベント方式への切り替えを検討する。`ContainerRestarting` は
`noDataState: OK` のため、系列が無い間は発火しない。

### 4. db-pve3 / ocms-pve3 の filesystem

`pve-lgtm/examples/db-pve3-otelcol.yaml` と
`pve-lgtm/examples/ocms-pve3-otelcol.yaml` の `hostmetrics` に
`filesystem: {}` を追加済み。各 VM の `/etc/otelcol-contrib/config.yaml` へ反映し、
`otelcol-contrib` を再起動する。

```sh
scp pve-lgtm/examples/db-pve3-otelcol.yaml root@192.168.1.253:/tmp/config.yaml
ssh root@192.168.1.253 'install -m 644 /tmp/config.yaml /etc/otelcol-contrib/config.yaml && systemctl restart otelcol-contrib'

scp pve-lgtm/examples/ocms-pve3-otelcol.yaml root@192.168.1.254:/tmp/config.yaml
ssh root@192.168.1.254 'install -m 644 /tmp/config.yaml /etc/otelcol-contrib/config.yaml && systemctl restart otelcol-contrib'
```

反映後、`system_filesystem_usage{job=~"usbharu-lab/(db-pve3|ocms-pve3)-host"}` が
Mimir に出ることを確認する。PostgreSQL のデータディレクトリと WAL の領域が
含まれていること。

### 5. LGTM canary（LXC 200）

```sh
scripts/install-lgtm-canary.sh
scripts/install-lgtm-canary.sh --update-baseline
```

`pve-lgtm/canary/*.py` を `/usr/local/lib/lgtm-canary/` に置き、1〜5 分間隔の
systemd timer を有効にする。`GRAFANA_MONITOR_TOKEN` が無い場合は Viewer の
サービスアカウント `external-monitor` とトークンを自動作成する。次の秘密値が必要。

- `/opt/lgtm/.env`: `GF_SECURITY_ADMIN_PASSWORD`（既存）、
  `KOMODO_API_KEY` / `KOMODO_API_SECRET`（GitOps heartbeat 用）
- `/root/lgtm-s3.env`（0600）: R2 の `S3_ACCESS_KEY`、`S3_SECRET_KEY`、
  `S3_ENDPOINT`、`S3_REGION`、`S3_BUCKET_{MIMIR,LOKI,TEMPO}`

Komodo の API キーは、administrator でログインして
`POST /auth/manage/CreateApiKey` に `{"name":"lgtm-gitops-canary","expires":0}` を
送ると作成できる。応答の `key` / `secret` を `.env` に追記する。
専用の読み取り専用ユーザーを作るまでは administrator 権限になるため、
LXC 200 の root と同等に扱う。

`--update-baseline` は現在の Grafana 設定を trusted baseline として保存する。
`scripts/apply-grafana-alerting.sh` は provisioning の reload 後に自動で
baseline を更新する。

### 6. PostgreSQL / バックアップ（db-pve3）

```sh
scripts/install-postgres-monitoring.sh
```

`prometheus-postgres-exporter` を `:9187` で起動し、`postgres_exporter` ロールを
`pg_monitor` に追加する。パスワードは `/etc/lgtm-canary/pg-exporter.env`（0600）に
生成する。あわせて `/var/backups/postgresql/` への日次 `pg_dump -Fc`、
`pg_restore --list` 検証、7 世代保持、Mimir への成功・失敗メトリクス送信を
`postgres-backup.timer` で有効にする。初回は手動で 1 回実行される。

LXC 200 の Collector に `192.168.1.253:9187` の scrape を追加するため、
`scripts/apply-lgtm-collector.sh` も実行する。

```sh
scripts/apply-lgtm-collector.sh
```

### 7. ZFS scrub（pve3）

```sh
scripts/install-pve3-zfs.sh
```

`zpool status -j hdd` から scrub エラー、最終 scrub 時刻、vdev の
read/write/checksum エラーを 1 時間ごとに Mimir へ送る。

### 8. oCIS コンテナ（VM101）

```sh
scripts/install-ocis-docker-stats.sh
```

VM101 の `ocis-metrics` Collector に `docker_stats` receiver を追加し、
Docker socket を read-only でマウントする。`container_state_status`、
`container_restarts`、`container_uptime` が Mimir に出ることを確認する。

### 9. 外部 dead-man switch（ocms-ubuntu VPS）

```sh
scripts/install-external-monitor.sh
```

VPS の `/etc/lab-external-monitor.env`（0600）に Grafana の Discord Webhook、
`GRAFANA_MONITOR_TOKEN`、`OTLP_ENDPOINT=http://192.168.1.240:4318` を置き、
1 分間隔で公開 URL と OTLP を確認する。2 回連続で失敗したら Discord へ直接通知する。
`GRAFANA_MONITOR_TOKEN` は Grafana の Viewer サービスアカウント
`external-monitor` のトークンで、`/api/datasources/uid/{mimir,loki,tempo}/health`
から保存先の健全性も確認する。トークンは LXC 200 の `/opt/lgtm/.env` に
`GRAFANA_MONITOR_TOKEN` として置き、再作成する場合は
`pve-lgtm/canary/create_grafana_monitor_token.py` を LXC 200 で実行する。
自宅 LAN の外から `192.168.1.240:4318` へ到達できる必要がある
（WireGuard の経路と pve2 の MASQUERADE を確認する）。

## 反映

```sh
# ローカル検証のみ
scripts/apply-grafana-alerting.sh --check

# アラート設定を反映
scripts/apply-grafana-alerting.sh

# ダッシュボードの provider.yaml（allowUiUpdates: false）も反映
scripts/apply-grafana-alerting.sh --dashboards
```

スクリプトは次を行う。

1. `scripts/check-alerting.py` で YAML と参照関係を検証する
   （PyYAML が必要。CI ではインストール済み）。
2. LXC 200 の Grafana コンテナに `GRAFANA_DISCORD_WEBHOOK_URL` があるか確認する。
3. provisioning が `Alerts` フォルダを作成する（既にあればそのまま）。
4. `pve-lgtm/provisioning/alerting/*.yaml` を LXC 200 の
   `/opt/lgtm/config/grafana/provisioning/alerting/` へコピーする。
   Git から削除した YAML はライブからも削除する。
5. `POST /api/admin/provisioning/alerting/reload` で反映する。
6. reload に失敗した場合は Grafana を再起動する。

環境変数で接続先を上書きできる。

```sh
PVE_HOST=root@192.168.1.250 LXC_ID=200 scripts/apply-grafana-alerting.sh
```

`--no-reload` を付けるとファイルのコピーだけ行う。`--skip-check` は PyYAML が
無い環境向けの非常手段で、通常は使わない。

`--rules-only` は `10-*.yaml` から `26-*.yaml` だけをコピーし、通知ポリシーと
コンタクトポイントは変更しない。Discord Webhook を設定する前にルールだけを
先に反映し、評価状態を確認する場合に使う。このモードでは Git から削除した
ルールファイルはライブに残るため、削除は通常モードで行う。

`--dashboards` は `pve-lgtm/provisioning/dashboards.yaml` をライブの
`provider.yaml` としてコピーし、ダッシュボードの provisioning を reload する。

PyYAML の導入:

```sh
python3 -m pip install --user pyyaml
```

## 通知ポリシー

| 条件 | receiver | group_wait | group_interval | repeat_interval |
| --- | --- | --- | --- | --- |
| ルート | `discord-alerts` | 30s | 5m | 4h |
| `severity=critical` | `discord-alerts` | 10s | 2m | 1h |
| `severity=warning` | `discord-alerts` | 30s | 5m | 4h |

- グルーピング: `grafana_folder` と `alertname`。
- critical と warning を別チャンネルにしたい場合は、Webhook を2つ用意し、
  `01-contact-points.yaml` と `02-notification-policies.yaml` に receiver を追加する。
- `maintenance-window`（土日 02:00–04:00）の mute timing を定義しているが、
  既定ではどのルートにも付けていない。計画メンテ時はポリシーへ追加するか、
  期限付き silence を使う。

通知本文は `usbharu-lab` テンプレートで、summary / description / severity / issue を
含める。`runbook_url` はこのファイルの該当セクションを指す。

本文末尾の「アラート詳細を開く」は `.GeneratorURL`（Grafana が組み立てる
`/alerting/grafana/<ルールUID>/view`）へのリンクで、ルール詳細ページへ飛ぶ。
Discord 連携の埋め込みタイトルに付くリンクはレシーバー実装で
`/alerting/list` に固定されており、テンプレートからは変更できない。ルール一覧では
なく詳細を開きたいので、本文側にリンクを置いている。

## ルール一覧

すべてフォルダ `Alerts`、評価間隔 1 分。`NoData` は `noDataState` の値。
`Alerting` は「データが無い = 異常」、`OK` は「データが無い = 正常」の意味。
`execErrState` は全ルール `Alerting`。2026-11-27 時点で 99 ルール。
`10-*.yaml` 〜 `19-*.yaml` が既存、`20-*.yaml` 〜 `26-*.yaml` が今回追加した
PostgreSQL、GitOps、Grafana 改ざん検知、canary、R2、ZFS scrub、oCIS コンテナの
ルール。

<a id="targets"></a>

### #149 targets

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `TargetDown` (`tgt-down`) | critical | 2m | `up < 1` | Alerting |
| `TargetMissing` (`tgt-missing`) | warning | 10m | 各 job の `absent(up)`、`node` は `count(up) < 3` | Alerting |
| `PveExporterDown` | critical | 2m | `pve_exporter_up < 1` | Alerting |
| `PveNodeDown` | critical | 2m | `pve_node_up < 1` | Alerting |
| `CriticalVmDown` | critical | 5m | `pve_vm_status{vmid=~"100\|101\|110\|121\|122"} < 1` | Alerting |

`TargetMissing` は job 名を `label_replace` で残す。`node` は LXC 200 を含めて
3 ターゲットを期待する。node_exporter 追加前は warning が発火する。

<a id="push-sources"></a>

### #150 push-sources

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `PushSourceMissing` | warning | 0s | `absent_over_time(...[5m])`（ocms-ubuntu / db-pve3 / ocms-pve3 / VM110 worker / ocis） | OK |
| `OcmsServiceMissing` | warning | 0s | 各 OCMS サービスの `target_info` の `absent_over_time(...[5m])` | OK |
| `HostMetricsMissing` | warning | 1m | pve2 / pve3 の `target_info` が 3 分欠落 | OK |
| `HostFilesystemMissing` | warning | 5m | pve2 / pve3 の `system_filesystem_usage` が 10 分欠落 | OK |

worker は `host_name="ocms-pve3"` と `host_name="ocms-worker-vm110"` を別々に判定する。
embedding-runtime は `job="usbharu-lab/embedding-runtime"` を使う。
`HostMetricsMissing` / `HostFilesystemMissing` は #162 の pve2 / pve3 hostmetrics 欠落検知。

<a id="containers"></a>

### #151 containers

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `ContainerMissing` | warning | 5m | 7 サービスの `absent(container_memory_working_set_bytes)` | OK |
| `ContainerRestarting` | warning | 0s | `changes(container_start_time_seconds[10m]) > 2` | OK |

対象は `ocms-management`（core / periphery / mongo / pocket-id / traefik）、
`vaultwardern`（vaultwarden）、`ocms-observability`（alloy）。

<a id="ocms-slo"></a>

### #152 ocms-slo

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `OcmsHttp5xxRatio` | warning | 10m | 5xx / 全リクエスト > 1%（分母は最低 1 req/s） | OK |
| `OcmsLatencyP95` | warning | 10m | p95 > 8 秒 | OK |
| `EmbeddingLatencyP95` | warning | 10m | embedding の p95 > 30 秒 | OK |
| `OcmsJobFailures` | critical | 5m | `sum by (host_name, error_code) (count_over_time({service_namespace="usbharu-lab",service_name="worker"} \|= "media.job_failed" \| status="failed" [15m])) > 5` | OK |
| `OcmsJobRetries` | warning | 5m | `sum by (host_name, error_code) (count_over_time({service_namespace="usbharu-lab",service_name="worker"} \|= "media.job_failed" \| status="retry" [15m])) > 50` | OK |

5xx は `http_response_status_code` ラベルを持つ bff / catalog / transfer / search /
worker が対象。embedding-runtime / recommendation-engine はラベルが無いため対象外で、
死活は #150 の `OcmsServiceMissing` で見る。

Job 失敗・retry は Mimir の `media_job_completed` ではなく Loki の
`media.job_failed` ログで評価する。2026-11-25 より前は pve3 worker の
メトリクスが `lab_host=ocms-worker-vm110` に合流し、2 ホストの cumulative
counter が 62↔39 と振動して `increase()` が実際の数十倍に過大評価されていた。
collector 側は `lab_host` を `host.name` から導出するよう修正済みで、Mimir の
`media_job_completed` もホスト別に分かれている。Grafana は host_name と
error_code を直接扱える Loki ログベースのままにしている。失敗ログには
`job_type` が無いので、`host_name` と `error_code` で group by している。

<a id="pve-hardware"></a>

### #153 pve-hardware

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `PveSmartHealth` | critical | 5m | `pve_disk_health_status < 1` | Alerting |
| `PveDiskTempSata` | warning | 15m | SATA / unknown > 60°C | Alerting |
| `PveDiskTempNvme` | warning | 15m | NVMe > 70°C | Alerting |
| `PveSsdWear` | warning | 1h | `pve_disk_percentage_used > 90` | OK |
| `PveSsdSpare` | critical | 1h | `pve_disk_available_spare_percent < 10` | OK |
| `PveNodeRootfs` | warning | 10m | rootfs > 80% | Alerting |
| `PveNodeRootfsCritical` | critical | 10m | rootfs > 90% | Alerting |
| `PveNodeMemory` | warning | 10m | memory > 90% | Alerting |
| `PveNodeSwap` | warning | 15m | swap > 50% | Alerting |
| `PveZfsHealth` | critical | 5m | `pve_zfs_pool_health_status < 1` | Alerting |
| `PveZfsFrag` | warning | 1h | frag > 80% | Alerting |
| `PveZfsUsage` | warning | 10m | alloc / size > 80% | Alerting |
| `PveZfsUsageCritical` | critical | 10m | alloc / size > 90% | Alerting |
| `PveStorageUsage` | warning | 10m | `pve_storage_used_fraction > 0.8` | Alerting |
| `PveStorageUsageCritical` | critical | 10m | `> 0.9` | Alerting |
| `PveSensorTemp` | warning | 10m | sensor > 80°C | Alerting |
| `PveSensorTempCritical` | critical | 10m | sensor > 90°C | Alerting |
| `OcmsUbuntuRootfs` | warning | 10m | ocms-ubuntu `/` 空き < 15% | Alerting |
| `OcmsUbuntuRootfsCritical` | critical | 10m | 空き < 10% | Alerting |
| `HostFilesystem` | warning | 10m | ext4 / xfs / zfs / btrfs の使用率 > 80% | Alerting |
| `HostFilesystemCritical` | critical | 10m | 使用率 > 90% | Alerting |

`HostFilesystem` は `/mnt/winiso` と `/mnt/virtioiso` を除外する。
db-pve3 / ocms-pve3 は filesystem collector 追加後に自動で対象になる。
`pve_disk_health_status{type="unknown"}` の 2 台は値が実健康状態を反映していない
可能性があるため、別途 `smartctl` で確認する。

<a id="exporters"></a>

### #154 exporters

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `ExporterMetricFailures` | warning | 10m | `rate(otelcol_exporter_send_failed_metric_points[5m]) > 0` | OK |
| `ExporterLogFailures` | warning | 10m | `send_failed_log_records > 0` | OK |
| `ExporterSpanFailures` | warning | 10m | `send_failed_spans > 0` | OK |
| `NewRelicNoSuccess` | warning | 10m | New Relic の送信成功 0 かつ Mimir は成功 | OK |
| `ExporterQueueHigh` | warning | 10m | queue_size / capacity > 0.8 | OK |

<a id="lgtm"></a>

### #158 lgtm

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `MimirCompactorStale` | warning | 10m | 最終成功から 1 時間超 | Alerting |
| `MimirCompactorFailed` | warning | 10m | `rate(cortex_compactor_runs_failed_total[10m]) > 0` | OK |
| `MimirDiskOutOfSpace` | critical | 0s | `increase(cortex_compactor_disk_out_of_space_errors_total[10m]) > 0` | OK |
| `TempoCompactionErrors` | warning | 10m | `rate(tempodb_compaction_errors_total[10m]) > 0` | Alerting |
| `TempoCompactionBacklog` | warning | 30m | `tempodb_compaction_outstanding_blocks > 10` | Alerting |
| `LgtmRootfs` | warning | 10m | LXC 200 `/` 空き < 15% | Alerting |
| `LgtmRootfsCritical` | critical | 10m | 空き < 10% | Alerting |

<a id="ingestion"></a>

### #161 ingestion

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `LokiIngestionStopped` | warning | 0s | `sum(rate(loki_distributor_lines_received_total[5m])) < 0.001` | Alerting |
| `MimirIngestionStopped` | warning | 0s | `sum(rate(cortex_distributor_received_samples_total[5m])) < 0.001` | Alerting |
| `TempoIngestionStopped` | warning | 15m | `sum(rate(tempo_distributor_spans_received_total[5m])) < 0.001` | Alerting |

ログ系ルール（`KomodoErrorLogs` / `PostgresFatalLogs`）は「エラー 0 件 = 正常」のため
`noDataState: OK` のまま。Loki の取り込み停止は `LokiIngestionStopped` で別途検知する。
Tempo はアイドル時の誤検知を避けるため `for: 15m` にしている。

<a id="ocis"></a>

### #160 ocis

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `OcisErrorRate` | warning | 10m | `sum(rate(ocis_proxy_errors_total[5m])) > 0.1`（トラフィックあり） | OK |
| `OcisErrorRatio` | warning | 10m | エラー比率 > 1%（トラフィック > 0.1 req/s） | OK |
| `OcisLatencyP95` | warning | 10m | p95 > 2 秒（トラフィック > 0.1 req/s） | OK |

oCIS は現在トラフィック 0 のため、エラー率・レイテンシは NoData → OK。
コンテナ状態・再起動の検知は VM101 への cAdvisor 追加が別途必要。

<a id="logs"></a>

### #155 / #156 logs

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `KomodoErrorLogs` | warning | 5m | `count_over_time({service_name=~"komodo-.*"} \|~ "(?i)(error\|failed\|panic\|fatal)" [5m]) > 0` | OK |
| `PostgresFatalLogs` | warning | 5m | `count_over_time({service_name="postgresql"} \|~ "(?i)(PANIC\|FATAL\|out of memory\|no space left\|terminating connection due to)" [5m]) > 0` | OK |

<a id="postgresql"></a>

### #156 PostgreSQL / backup

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `PostgresDown` (`postgres-down`) | critical | 2m | `pg_up < 1` | Alerting |
| `PostgresExporterDown` (`postgres-exporter-down`) | critical | 5m | `up{job="postgres"} < 1` | Alerting |
| `PostgresConnectionsHigh` (`postgres-connections-high`) | warning | 10m | 接続数 / `max_connections` > 80% | Alerting |
| `PostgresLongTransaction` (`postgres-long-transaction`) | warning | 5m | `pg_stat_activity_max_tx_duration > 600` | Alerting |
| `PostgresRollbacks` (`postgres-rollbacks`) | warning | 10m | ロールバック率 > 0.1/s | Alerting |
| `PostgresDeadlocks` (`postgres-deadlocks`) | warning | 10m | デッドロック率 > 0 | Alerting |
| `PostgresBackupStale` (`postgres-backup-stale`) | critical | 10m | 最終成功から 26 時間超 | Alerting |
| `PostgresBackupFailed` (`postgres-backup-failed`) | critical | 0s | 15 分以内に失敗がある | OK |
| `PostgresBackupRestoreUnverified` (`postgres-backup-unverified`) | critical | 1h | `postgres_backup_restore_verified < 1` | Alerting |

`postgres_exporter` は `db-pve3` の `:9187` で動かし、LXC 200 の Collector から
30 秒間隔で scrape する。`pg_up`、接続数、トランザクション、ロールバック、
デッドロック、DB サイズを取得する。`pg_stat_activity_max_tx_duration` は
10 分を超えるトランザクションを検知する。

バックアップは `db-pve3` の `/var/backups/postgresql/` に日次で
`pg_dump -Fc` を書き、`pg_restore --list` で読み出しを検証する。7 世代を保持する。
最後の成功時刻、所要時間、サイズ、検証結果を Mimir に送る。失敗時は
`postgres_backup_last_failure_timestamp_seconds` と
`postgres_backup_restore_verified=0` を送り、成功時刻は更新しない。
同じホスト上のバックアップなので、別ホスト / R2 への退避は別途必要。

<a id="gitops"></a>

### #155 GitOps heartbeat

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `GitopsHeartbeatStale` (`gitops-heartbeat-stale`) | warning | 5m | 最終成功から 15 分超 | Alerting |
| `GitopsLastRunFailed` (`gitops-last-run-failed`) | critical | 5m | `gitops_state{state="failed"}` | OK |
| `GitopsScheduleDisabled` (`gitops-schedule-disabled`) | warning | 10m | `gitops_schedule_enabled < 1` | Alerting |
| `GitopsCanaryStale` (`gitops-canary-stale`) | critical | 0s | canary が 10 分更新なし | Alerting |
| `GitopsApiFailing` (`gitops-api-failing`) | warning | 10m | `gitops_api_ok < 1` | Alerting |

LXC 200 の `gitops_canary.py` が Komodo API の `ListProcedures` / `GetProcedure` を
呼び、`usbharu-lab-gitops` の `last_run_at`（最後に成功した時刻）と state、
スケジュール有効状態を取得する。`last_run_at` は成功時だけ更新されるため、
「失敗が通知されない」「スケジュールが止まった」の両方を検知できる。
Komodo の `failure_alert = true` は残し、通知経路が二重になることを許容する。

Komodo の API キーは LXC 200 の `/opt/lgtm/.env` に
`KOMODO_API_KEY` / `KOMODO_API_SECRET` として置く。読み取り専用の専用ユーザーを
作れない場合は administrator のキーを使うため、LXC 200 の root 権限と同等に扱う。

<a id="grafana-config"></a>

### #159 Grafana 改ざん検知

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `LgtmConfigCanaryStale` (`lgtm-config-canary-stale`) | critical | 0s | canary が 10 分更新なし | Alerting |
| `LgtmConfigApiFailing` (`lgtm-config-api-failing`) | warning | 10m | `lgtm_config_api_ok < 1` | Alerting |
| `LgtmConfigBaselineMissing` (`lgtm-config-baseline-missing`) | warning | 10m | baseline なし | Alerting |
| `LgtmConfigDrift` (`lgtm-config-drift`) | warning | 5m | カテゴリ別ハッシュが baseline と不一致 | Alerting |
| `LgtmAlertRulesLow` (`lgtm-alert-rules-low`) | critical | 10m | ルール数 < 70 | Alerting |
| `LgtmAlertRulePaused` (`lgtm-alert-rule-paused`) | warning | 10m | paused のルール > 0 | Alerting |
| `LgtmSilenceCreated` (`lgtm-silence-created`) | warning | 0s | 15 分以内に新しい silence | Alerting |
| `LgtmSilenceLong` (`lgtm-silence-long`) | warning | 10m | silence が 7 日超 | Alerting |

`config_canary.py` は Grafana の provisioning API からアラートルール、
コンタクトポイント、通知ポリシー、mute timing、データソース、ダッシュボードを取得し、
カテゴリごとの SHA-256 を baseline と比較する。baseline は
`scripts/apply-grafana-alerting.sh` の適用後に更新する。
サイレンスは ID の増加と最大期間を別に記録する。UI でルールを削除・無効化・
コンタクトポイント変更・サイレンス作成をしても、次の 2 分で検知できる。

Grafana OSS は audit logging を持たないため、API の状態差分で代替する。
この canary 自身を止められると検知できないが、`LgtmConfigCanaryStale` と
外部 dead-man switch で多重化する。

<a id="canary"></a>

### #161 取り込み canary

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `LgtmMetricCanaryStale` (`lgtm-metric-canary-stale`) | critical | 0s | canary metric が 5 分更新なし | Alerting |
| `LgtmLogCanaryMissing` (`lgtm-log-canary-missing`) | critical | 0s | `count_over_time({service_name="lgtm-canary"}[5m])` が 0 / NoData | Alerting |
| `LgtmLogCanaryInvisible` (`lgtm-log-canary-invisible`) | critical | 5m | Loki から canary ログを検索できない | Alerting |
| `LgtmTraceCanaryInvisible` (`lgtm-trace-canary-invisible`) | critical | 10m | Tempo から canary トレースを検索できない | Alerting |
| `LgtmMimirCanaryInvisible` (`lgtm-mimir-canary-invisible`) | critical | 5m | Mimir から canary metric を検索できない | Alerting |
| `ExternalMonitorStale` (`external-monitor-stale`) | critical | 0s | 外部 monitor が 10 分更新なし | Alerting |
| `ExternalCheckFailing` (`external-check-failing`) | critical | 5m | 外部チェックのいずれかが失敗 | Alerting |

`ingestion_canary.py` は 1 分ごとに OTLP/HTTP で
`lgtm_canary_timestamp_seconds`、`lgtm-canary` ログ、canary トレースを送り、
Grafana の datasource proxy 経由で Mimir / Loki / Tempo から読めることを確認する。
書き込みと読み出しの両方を検証するため、Collector の exporter 停止、
Loki のテナント設定、Tempo の querier 停止なども検知できる。

`lgtm_canary_*_visible` は直前の canary を検索した結果で、今回のサンプルが
まだ見えないことによる誤検知を避けるため、5〜10 分の猶予を持たせている。
ログ系の既存ルール（`KomodoErrorLogs` / `PostgresFatalLogs`）は
「エラー 0 件 = 正常」のため `noDataState: OK` のままにし、取り込み停止は
この canary で検知する。

<a id="external-monitor"></a>

### #148 外部 dead-man switch

`external_monitor.py` は自宅 LAN の外にある ocms-ubuntu VPS で動く。
Grafana の `/api/health`、Grafana 経由の Mimir / Loki / Tempo データソース health、
OTLP 4318（WireGuard 経由）、Komodo、Pocket ID、OCMS の `/healthz` を 1 分ごとに
確認し、2 回連続で失敗したら Discord に直接通知する。
Grafana / Mimir / Loki / Tempo を経由しないため、LXC 200 や pve2 が停止しても
通知できる。復旧時も Discord に送る。同じ結果は Mimir にも送り、
`ExternalCheckFailing` / `ExternalMonitorStale` で履歴を残す。

外部 VPS 自体が停止した場合は通知できない。VPS の監視は別途必要。

<a id="r2"></a>

### #158 R2 / 保存量

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `R2MultipartUploads` (`r2-multipart-uploads`) | warning | 10m | 未完了 multipart > 100 | OK |
| `R2MultipartUploadsCritical` (`r2-multipart-uploads-critical`) | critical | 5m | 未完了 multipart > 1,000 | OK |
| `R2MultipartStale` (`r2-multipart-stale`) | warning | 30m | 最古の未完了が 24 時間超 | OK |
| `R2UsageCanaryStale` (`r2-usage-canary-stale`) | critical | 0s | canary が 1 時間更新なし | Alerting |
| `R2BucketGrowth` (`r2-bucket-growth`) | warning | 30m | 6 時間の傾きから 1 日 2 GiB 超 | Alerting |

`r2_usage.py` は `aws s3api list-multipart-uploads` で Mimir / Loki / Tempo の
未完了アップロードを数え、最古の経過時間を出す。バケットサイズとオブジェクト数は
1 時間に 1 回 `aws s3 ls --summarize --recursive` で取得する。
2026-11-19 の Tempo multipart 失敗（5,465 件 / 約 1.08 TiB）を、
Tempo の完了済みブロックのメトリクスだけに頼らず検知する。

認証情報は LXC 200 の `/root/lgtm-s3.env`（0600）を使う。
未完了アップロードの削除は自動化しない。閾値を超えたら中身を確認してから
`aws s3api abort-multipart-upload` を実行する。

<a id="zfs-scrub"></a>

### #153 ZFS scrub / vdev エラー

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `PveZfsScrubErrors` (`pve-zfs-scrub-errors`) | critical | 5m | scrub errors > 0 | OK |
| `PveZfsVdevErrors` (`pve-zfs-vdev-errors`) | critical | 5m | vdev の read/write/checksum > 0 | OK |
| `PveZfsScrubStale` (`pve-zfs-scrub-stale`) | warning | 1h | 最終 scrub から 35 日超 | Alerting |
| `PveZfsScrubMetricsStale` (`pve-zfs-scrub-metrics-stale`) | critical | 0s | メトリクスが 3 時間更新なし | Alerting |
| `PveZfsScrubApiFailing` (`pve-zfs-scrub-api-failing`) | warning | 10m | `zpool status -j` 失敗 | Alerting |

`zfs_scrub.py` は pve3 で `zpool status -j hdd` を実行し、scrub のエラー数、
最終 scrub 時刻、プール状態、vdev ごとの read/write/checksum エラーを出す。
`pve_zfs_pool_health_status` はプール全体の状態しか見ないため、
RAIDZ2 の 1 台がエラーを出し始めた段階を vdev 単位で検知する。

<a id="ocis-container"></a>

### #160 oCIS コンテナ

| ルール (UID) | severity | for | 条件 | NoData |
| --- | --- | --- | --- | --- |
| `OcisContainerDown` (`ocis-container-down`) | critical | 2m | `count(container_uptime{container_name="ocis"}) < 1` | Alerting |
| `OcisContainerRestarting` (`ocis-container-restarting`) | warning | 0s | 10 分間の再起動 > 0 | OK |
| `OcisContainerMissing` (`ocis-container-missing`) | warning | 5m | `count(container_uptime{container_name="ocis"}) < 1` | Alerting |

VM101 の `ocis-metrics` Collector に `docker_stats` receiver を追加し、
Docker socket を read-only でマウントする。`container_state_status`、
`container_restarts`、`container_uptime` を Mimir に送る。
コンテナ名は `ocis` を対象にする。

## 導入直後に発火するアラート

2026-11-27 時点の実データでは、次のルールが導入直後に発火する。

| ルール | 理由 |
| --- | --- |
| `ExporterMetricFailures` | New Relic が HTTP 402 で約 22 points/s 失敗 |
| `NewRelicNoSuccess` | New Relic の送信成功 0、Mimir は成功 |
| `PushSourceMissing` / `OcmsServiceMissing` | VM110 の runtime/worker を CPU 調査のため意図的に停止中 |
| `OcmsLatencyP95` | 現在の p95 が閾値 8 秒を超過 |
| `PveSensorTemp` | pve2 の CPU Package が 79°C |
| `LgtmSilenceLong` | `NewRelicNoSuccess` を 14 日、VM110 を 7 日抑制する silence が存在 |

`OcmsJobFailures` / `OcmsJobRetries` は、Mimir のラベル衝突による過大評価を
避けるため Loki ログベースに変更した。実失敗は 24 時間で約 100 件、
retry は約 70 件であり、平常時にこの 2 つが発火することはない。

これらはアラート経路が動いていることの確認になる。原因を修正するか、
作業中だけ期限付き silence（例: 24 時間）を使う。silence は #159 の観点で
抜け道になるため、期限を短くし、理由と follow-up を残す。

## 検証

### 通知経路

1. Grafana → Alerting → Contact points → `discord-alerts` → Test。
2. Discord にテスト通知が届くことを確認する。
3. 一時ルール `AlertingTest`（`vector(1) > 0`、`for: 0s`、severity warning）を
   作成し、1 分以内に届くことと、削除後に resolve が届くことを確認する。
4. 一時ルールは必ず削除する。provisioning のルールと混ざらないよう、
   検証用は別の一時フォルダか、適用前の短時間で行う。

### 故障注入

| Issue | 操作 | 期待 | リスク |
| --- | --- | --- | --- |
| #149 | VM100 の `prometheus-node-exporter` を停止 | `TargetDown` が 2 分以内 | 低。停止後に再起動 |
| #149 | 存在しない job を `absent()` で評価 | `TargetMissing` | 低 |
| #150 | VM110 の `otelcol-contrib` を停止 | `ocms-worker-vm110` の欠落が 5 分以内 | 低。再起動 |
| #151 | `docker stop ocms-management-vaultwarden-1` | `ContainerMissing` が 5 分以内 | 低。再起動 |
| #151 | pocket-id を 10 分に 3 回再起動 | `ContainerRestarting` | 低 |
| #152 | 追加注入不要（既に失敗中） | `OcmsJobFailures` | なし |
| #154 | 追加注入不要（既に失敗中） | `ExporterMetricFailures` | なし |
| #155 | `systemctl stop lgtm-gitops-canary.timer` | `GitopsCanaryStale` が 10 分以内 | 低。再開 |
| #155 | Komodo の Procedure を一時的に失敗させる | `GitopsLastRunFailed` が 5 分以内 | 低。次回成功で resolve |
| #156 | `systemctl stop postgresql` | `PostgresDown` が 2 分以内 | 中。DB を使う OCMS を止める |
| #156 | `mv /var/backups/postgresql` して timer を待つ | `PostgresBackupStale` が 26 時間後 | 低。戻す |
| #158 | R2 にテスト用の multipart upload を残す | `R2MultipartUploads` が 10 分以内 | 低。abort する |
| #159 | Grafana UI で一時ルールを削除 / silence を作成 | `LgtmConfigDrift` / `LgtmSilenceCreated` が 5 分以内 | 低。silence は削除 |
| #161 | `systemctl stop lgtm-ingestion-canary.timer` | `LgtmMetricCanaryStale` が 5 分以内 | 低。再開 |
| #161 | Loki の `/ready` を一時的に落とす | `LgtmLogCanaryMissing` | 中。Collector も影響 |
| #148 | LXC 200 を停止（計画メンテ） | VPS から Discord へ 2 分以内 | 高。復旧手順を先に確認 |
| #153 | `zpool scrub` を止める / テスト用エラーを注入 | `PveZfsScrubStale` / `PveZfsScrubErrors` | 中。実プールを壊さない |
| #160 | `docker stop ocis` | `OcisContainerDown` が 2 分以内 | 中。oCIS が止まる |
| #160 | `docker restart ocis` | `OcisContainerRestarting` | 中。短時間停止 |

PVE ホスト、Mimir、Tempo の停止など影響の大きいテストは、別のメンテナンス枠で行う。
ルールのクエリと閾値は Grafana のルールプレビューで確認できる。

## ルールの追加・変更

1. `pve-lgtm/provisioning/alerting/` の該当ファイルを編集する。
2. すべてのルールに `severity` / `category` / `issue` ラベルと、
   `summary` / `description` / `runbook_url` を付ける。
3. `scripts/apply-grafana-alerting.sh --check` で検証する。
4. `scripts/apply-grafana-alerting.sh` で反映する。
5. Grafana → Alerting → Alert rules で状態を確認し、このファイルの一覧を更新する。

`noDataState` は次を基準にする。

- 単一のメトリクスが消えたら異常（`up`、PVE の閾値、compaction、LGTM の FS）: `Alerting`
- `absent` / `absent_over_time` で欠落を明示的に検出し、正常時に空になるもの
  （`PushSourceMissing` / `OcmsServiceMissing` / `ContainerMissing`）: `OK`
- トラフィックや失敗が 0 件のときに系列が消えるもの: `OK`
  （死活は `TargetMissing` / `PushSourceMissing` / `OcmsServiceMissing` が別途見る）
- ログ系の `OK` は、`LokiIngestionStopped` / `MimirIngestionStopped` /
  `TempoIngestionStopped` が取り込み停止を別途検知する

`OK` にしても、クエリ自体が失敗した場合は `execErrState: Alerting` で通知される。

ダッシュボードを変更するときは `pve-lgtm/dashboards/` または
`stacks/observability/dashboards/` の JSON を編集し、`--dashboards` で
`provider.yaml` を反映する。UI からの編集は `allowUiUpdates: false` により無効。

## 機密情報

| 場所 | 内容 |
| --- | --- |
| `/opt/lgtm/.env`（LXC 200、0600） | `GRAFANA_DISCORD_WEBHOOK_URL`、Grafana admin パスワード、New Relic キー、`KOMODO_API_KEY` / `KOMODO_API_SECRET`、`GRAFANA_MONITOR_TOKEN` |
| `/root/lgtm-s3.env`（LXC 200、0600） | R2 の endpoint / region / access key / secret / bucket 名 |
| `/etc/lab-external-monitor.env`（ocms-ubuntu、0600） | Discord Webhook、`GRAFANA_MONITOR_TOKEN`、OTLP endpoint |
| `/etc/lgtm-canary/pg-exporter.env`（db-pve3、0600） | `postgres_exporter` のパスワード |
| Grafana のサービス環境変数 | `GRAFANA_DISCORD_WEBHOOK_URL` を compose から渡す |

Webhook URL はこのリポジトリに置かない。`01-contact-points.yaml` は
`$GRAFANA_DISCORD_WEBHOOK_URL` を参照するだけにしている。
`GRAFANA_MONITOR_TOKEN` は Grafana の `external-monitor` サービスアカウント
（Viewer）のトークンで、データソース health の読み取りだけに使う。

## 既知の制限と次の作業

- **#148 外部監視の単一障害点**: ocms-ubuntu VPS の `lab-external-monitor` が
  自宅 LAN の外から公開 URL と OTLP を確認し、Discord へ直接通知する。
  ただし VPS 自体が停止すると通知できない。VPS の死活は別の外部サービスで見る。
- **#156 バックアップの保管場所**: 日次 `pg_dump` と `pg_restore --list` 検証は
  db-pve3 上で動く。同じホスト・同じディスクなので、ディスク故障や VM 喪失には
  耐えない。別ホストまたは R2 への退避が次の作業。
- **#158 R2 の保存量**: multipart の件数・最古時刻は 5 分ごと、バケットサイズは
  1 時間ごと。オブジェクト一覧を合計するため、バケットが大きくなると所要時間が
  増える。R2 の Analytics API が使える場合はそちらへ置き換える。
- **#159 改ざん検知の限界**: Grafana OSS は audit logging 非対応（Enterprise / Cloud
  のみ）。API canary は UI からの編集・削除・サイレンスを検知するが、canary 自身を
  停止されると検知できない。外部 dead-man switch と `LgtmConfigCanaryStale` で
  多重化している。silence は UI から作成できるため、期限付き運用にする。
- **#155 API キー**: Komodo の API キーは `usbharu-lab-gitops` の読み取りに使う。
  専用の読み取り専用ユーザーを作るまでは administrator 権限のキーを LXC 200 に置く。
- **#152 canary**: recommendation / embedding の機能 canary は未実装。
  BFF の `/healthz` は外部監視で見るが、推薦・埋め込みの実リクエストは検証しない。
- **#154 原因**: New Relic の送信失敗（402 / キー / 支払い）はアラートでは直らない。
  別途修正が必要。
- **#151 restart**: OCMS / 管理系のコンテナ再起動は既存の
  `ContainerRestarting` が見る。oCIS は `docker_stats` の
  `container_restarts` で別途見る。

## 参考

- Grafana file provisioning:
  <https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/>
- Alerting provisioning API:
  <https://grafana.com/docs/grafana/latest/developers/http_api/alerting_provisioning/>
