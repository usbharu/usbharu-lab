# DCS World Dedicated Server (pve3)

DCS World の LAN 用ゲームサーバー。Proxmox VE ホスト `pve3`（192.168.1.251）上の VM 120。

## 構成

| 項目 | 値 |
| --- | --- |
| VM | `120` / `dcs` |
| OS | Windows Server 2022 Standard Evaluation（Desktop Experience） |
| CPU | 8 vCPU（1 socket、host） |
| RAM | 20GB |
| ディスク | 200GB（local-lvm） |
| IP | `192.168.1.252/24`（GW 192.168.1.1） |
| DCS | `C:\DCS World Server`（2.9.31.29434） |
| テレイン | Caucasus のみ |
| ミッション | `C:\Users\dcs\Saved Games\DCS.server\Missions\CaucasusDynamicSpawn.miz` |
| DCS ポート | 10308 TCP/UDP |
| SRS | `C:\SRS\ServerCommandLine-Windows`、5002 TCP/UDP |
| RDP | 3389 TCP（LAN のみ） |
| 公開設定 | `public = false`（LAN のみ） |

## ログイン

- Windows: ユーザー `dcs`（Administrators）
- パスワード: `pve3:/root/dcs-vm/credentials.txt` を参照（0600）。初回ログイン後に変更する。
- パスワードは `dcs` / `Administrator` 共通の一時パスワード。
- コンソール: PVE Web UI（`https://192.168.1.251:8006`）→ VM 120 → Console
- RDP: `192.168.1.252`、ユーザー `dcs`

## 起動・停止

- Windows 自動ログイン: `dcs`
- DCS 自動起動: `C:\Users\dcs\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\DCS Server Watchdog.lnk`
  - `C:\dcs-setup\watchdog.ps1` が `DCS_server.exe -w DCS.server` を起動し、落ちたら再起動する。
- SRS: タスクスケジューラ `SRS Server`（At startup / SYSTEM）
- VM の起動順: `onboot=1`、`startup order=20`

## 接続方法

### DCS

- クライアント: マルチプレイ → LAN タブ、または直接接続 `192.168.1.252:10308`
- サーバー名: `usbharu DCS (LAN)`
- パスワード: なし（`serverSettings.lua` で変更可能）
- 最大人数: 16
- ミッション未接続時は `ssPaused` で待機し、最初のクライアント接続で開始する（DCS の通常動作）。

### SRS

- サーバー: `192.168.1.252:5002`（TCP/UDP）
- クライアントの SRS で上記アドレスに接続する。

## 設定ファイル

| 用途 | パス |
| --- | --- |
| サーバー設定 | `C:\Users\dcs\Saved Games\DCS.server\Config\serverSettings.lua` |
| ミッション | `C:\Users\dcs\Saved Games\DCS.server\Missions\` |
| DCS ログ | `C:\Users\dcs\Saved Games\DCS.server\Logs\dcs.log` |
| DCS 本体 | `C:\DCS World Server\` |
| DCS 更新設定 | `C:\DCS World Server\autoupdate.cfg`（WORLD + CAUCASUS_terrain のみ。編集しない） |
| SRS 設定 | `C:\SRS\ServerCommandLine-Windows\server.cfg` |
| SRS ログ | `C:\SRS\ServerCommandLine-Windows\serverlog.txt` |
| セットアップログ | `C:\dcs-setup\` |

## ミッションの変更

1. `.miz` を `C:\Users\dcs\Saved Games\DCS.server\Missions\` に置く。
2. `serverSettings.lua` の `missionList` を書き換える。
3. DCS を再起動する（タスクマネージャで `DCS_server.exe` を終了するとウォッチドッグが再起動する）。

## 更新

- DCS: `DCS_server.exe` を停止 → `C:\DCS World Server\bin\DCS_updater.exe update` を管理者/SYSTEM で実行 → ウォッチドッグが DCS を再起動。
- Windows: 月次で手動更新。再起動後は自動ログインで DCS が復帰する。
- 更新前に PVE スナップショット: `qm snapshot 120 pre-update-YYYYMMDD`（1週間後に削除）。

## バックアップ

最低限、以下を日次で退避する。

- `C:\Users\dcs\Saved Games\DCS.server\Config\`
- `C:\Users\dcs\Saved Games\DCS.server\Missions\`
- `C:\SRS\ServerCommandLine-Windows\server.cfg`
- `C:\dcs-setup\watchdog.ps1`

フル VM バックアップは 200GB あるため、PBS/外部ストレージを用意してから設定する。

## 注意・既知の制限

- **NIC**: pve3 の物理 NIC `nic4` が 100Mbps でリンクフラップする。LAN クライアントが切断される可能性があるため、ケーブル/ポート交換を推奨。
- **WebGUI**: 8088 は DCS の HTTP コントロール API。静的 WebGUI（`C:\DCS World Server\WebGUI`）は 8088 では配信されず、`http://192.168.1.252:8088/` は 404 になる。管理は RDP で `serverSettings.lua` を編集する。WebGUI が必要なら静的サーバー＋API プロキシを追加する。
- **メモリ**: 20GB VM。Caucasus の軽いミッションでは DCS 約3.6GB、VM 空き約14.7GB。ヘビーミッションや Tacview/Olympus/LotATC を載せる場合は 24GB またはホスト 64GB 化を検討。
- **GPU**: 不要（専用サーバーは no-render）。GPU パススルーなし。
- **ライセンス**: Windows Server 2022 Evaluation（180日）。継続利用時はライセンス化。
- **未導入**: Tacview / Olympus / LotATC（必要になったら追加）。
- **自動ログイン**: パスワードを変更する場合は `AutoAdminLogon` の `DefaultPassword` も更新する必要がある。
