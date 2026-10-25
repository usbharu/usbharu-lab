# usbharu-lab

Komodo 2.3.4で管理するサーバーの構成。`komodo`ブランチを同期元とし、GitHubへのpush後に設定とComposeを反映する。

日常の変更は「変更を反映する」、認証情報の更新は「機密情報の置き場所」を参照する。操作には、このリポジトリへのpush権限とKomodoの管理権限が必要。

## 変更を反映する

1. `komodo/resources.toml`、`stacks/ocms/compose.yaml`、`stacks/management/`を編集する。
2. `python3 scripts/check-config.py`と差分を確認して`komodo`ブランチにコミット・pushする。
3. [Komodo](https://komodo.usbharu.dev)のProcedure `usbharu-lab-gitops`で実行結果を確認する。

ProcedureはResource Sync `usbharu-lab`でリソース定義を同期する。設定変更があればこの段階でもデプロイする。続いてOCMS、管理用スタックの順に`DeployStackIfChanged`を実行する。GitHub Webhookは`komodo`ブランチのpushに反応する。5分ごとのスケジュールも同じProcedureを実行するため、Webhookの取りこぼしからも復帰できる。

Gitを設定の基準にするため、KomodoのUIで行った構成変更は次の同期で戻る。Secretの更新はUIで行う。Resource Syncの`managed`（UIからGitへ書き戻す機能）、リソースの自動削除、変数・ユーザーグループの同期は無効。

## 管理する構成

| 場所 | 内容 |
| --- | --- |
| `bootstrap/sync.toml` | 初回に手動設定するResource Sync。実行中のSync自身は同期対象にしない |
| `komodo/resources.toml` | 2つのStack、GitOps用Procedure |
| `stacks/management/compose.yaml` | Komodo Core・Periphery、MongoDB、Pocket ID、Traefik |
| `stacks/management/traefik/routes.yaml` | OCMS・Pocket ID・KomodoへのHTTPルーティング |
| `stacks/ocms/compose.yaml` | OCMSの6サービスと起動前のマイグレーション。ホスト上のPostgreSQL 17とCloudflare R2を使用 |

既存のServerリソース`ocms-ubuntu`を使用する。Composeのプロジェクト名`ocms`・`ocms-management`はデータ用ボリュームの名前に関わるため、移行なしで変更しない。

OCMSはGitHub Actionsが公開する`ghcr.io/usbharu/ocms:v0.3.0`と`ghcr.io/usbharu/ocms-runtime:v0.3.0-cpu`を指定する。Release公開とpublish成功を確認してからComposeのタグを更新し、pushする。タグの変更でKomodoがpull・デプロイする。ソースのビルドは行わない。モデルはRuntimeの初回推論時に取得する。

RuntimeはONNXに統一したGHCR CPU版を使用する。旧`OCMS_RUNTIME_IMAGE=ocms-runtime:local`による置き換えは廃止した。既存の`ocms_models`ボリュームは保持し、新しいモデルのキャッシュにも使う。メモリ約3.8 GiBの試験環境で、Runtimeのメモリ上限2 GiB・swap込み8 GiBは継続する。推論の初回取得と処理には時間がかかる場合がある。

## DBマイグレーションを伴う更新

`migrate`サービスがアプリと同じタグの`ocms migrate up`を実行し、正常終了してからCatalogを起動する。後続サービスはCatalog経由で待機する。ホストのPostgreSQLは継続使用し、DBや暗号鍵を作り直さない。

管理接続はホストの`postgres`ユーザーと同じUID/GIDでPostgreSQLのUNIXソケットへ接続するpeer認証。ホストの`ocms.env`に`OCMS_MIGRATE_UID`・`OCMS_MIGRATE_GID`を設定する。管理者パスワードは不要で、この接続は`migrate`だけに渡す。通常サービスは引き続き`ocms_catalog`・`ocms_transfer`・`ocms_search`・`ocms_media`を使う。

更新前にBFF・Worker・Search・Transfer・Catalogを停止し、PostgreSQL 17付属の`/usr/lib/postgresql/17/bin/pg_dump -Fc`でOCMS DBをバックアップし、同じディレクトリの`pg_restore`で読み出しを検証する。暗号鍵・環境ファイル・旧Composeもサーバー上の権限700のディレクトリへ保存し、ファイルは権限600を維持する。バックアップに機密情報を含むため、Gitへ追加しない。PostgreSQL、Pocket ID、Komodo、Traefik、他アプリは停止しない。

旧方式のDBを初めて引き継ぐときだけ、適用済みSQLをスキーマと照合して`ocms migrate baseline <番号>`を実行する。baselineはSQLを再実行せず履歴を登録する。v0.3.0への更新時は006まで適用済みで、007・008が未適用だったため`baseline 6`を使用した。現在は008まで適用済みで、baselineを再実行しない。標準SQLが権限を付与する`catalog_app`・`transfer_app`・`search_app`・`media_app`はNOLOGINの互換ロールとして作成し、対応する既存`ocms_*`ロールへ継承させる。既存のログインロールやパスワードは変更しない。

その後`migrate up`で007（お気に入り）と008（レーティング）を適用する。008は既存の完了済みタグJobを処理待ちへ戻すため、更新後に既存画像・動画・PDFのレーティングを順次判定する。タグ・キャラクターの手動編集と既存ファイルは保持する。

移行後は`public.schema_migrations`が`version=8, dirty=false`であること、既存Asset・Blob・Original・Principalの件数と新テーブルの権限を確認してから、Komodoで新しい構成をデプロイする。以後の更新はアプリ停止・バックアップ後に同じ`migrate up`を使い、baselineを再実行しない。失敗時はアプリを停止したまま調査し、dirty状態を推測で解除しない。DB変更はGitのrevertだけでは戻らない。

Cloudflare Tunnelはホストのsystemdサービスで動く。3つの公開ホスト名の転送先は`http://127.0.0.1:18080`。Tunnel・ホストのPostgreSQLサービス設定・既存の他アプリは、このリポジトリから変更しない。

2026-10-25の更新では、`/var/backups/ocms/v0.3.0-20261025T073358Z`へDBと設定を保存し、読み出し・移行前後の件数一致・新テーブルの権限を確認した。移行履歴は`version=8, dirty=false`。

## 機密情報の置き場所

秘密値・生成済み環境ファイル・バックアップをGitに追加しない。`.gitignore`は環境ファイルや鍵の誤追加を防ぐ補助であり、コミット前にも差分を確認する。

| 保存場所 | 用途 |
| --- | --- |
| KomodoのSecret変数`OCMS_R2_ACCESS_KEY_ID`・`OCMS_R2_SECRET_ACCESS_KEY` | R2アクセス。TOMLには`[[変数名]]`の参照だけを記載 |
| `/etc/komodo/stacks/management/ocms.env` | DB各ロールのパスワード、内部認証、OCMSのOIDC設定 |
| `/etc/komodo/stacks/management/komodo.env` | Komodoの認証・OIDC・Webhook・DB資格情報 |
| `/etc/komodo/stacks/management/pocket.env` | Pocket IDの暗号鍵と設定 |
| `/etc/komodo/stacks/management/.env` | MongoDB起動時の資格情報 |
| `/etc/komodo/secrets/ocms/crypto.yaml` | 保存済みメディアの暗号鍵。Transferにのみマウント |
| `/etc/cloudflared/token` | Cloudflare Tunnelのトークン |

ホスト上の環境ファイルは権限600を維持する。暗号鍵は別途バックアップし、再セットアップ時も元の鍵を引き継ぐ。Komodoがclone先に生成する`.komodo.env`も秘密値を含むため、Gitには追加しない。

## 初回接続・復旧

稼働中の環境では設定済み。Core・Peripheryの初期導入、秘密ファイルの復元、DB・データ用ボリュームの復元は先に行う。

1. Server `ocms-ubuntu`が接続済みで、必要なイメージと秘密ファイルが存在することを確認する。
2. KomodoのResource Syncを`usbharu-lab`という名前で作成し、Repository=`usbharu/usbharu-lab`、Branch=`komodo`、Resource Path=`komodo`に設定する。Managed、Delete、Variables、User Groupsを無効にする。
3. `bootstrap/sync.toml`も参照して設定を確認する。Pending差分を確認してSyncを実行する。Stack定義とProcedureが作成される。
4. Procedure `usbharu-lab-gitops`を実行する。必要ならGitHubのpush用Webhookを`https://komodo.usbharu.dev/listener/github/procedure/usbharu-lab-gitops/komodo`へ登録する。Content typeはJSON、Secretはサーバーの`KOMODO_WEBHOOK_SECRET`と同じ値を安全な経路で設定する。

自動反映を止める場合は、GitHub側のWebhookを無効にし、Gitの`procedure.config.schedule_enabled`と`webhook_enabled`を`false`へ変更して同期する。緊急時はまずKomodoのUIで同じ設定を無効にしてからGitにも反映する。

障害時はKomodoのProcedure・Sync・StackのUpdatesを確認する。管理画面へ接続できなければSSHで`docker ps`と`systemctl status cloudflared`を確認する。構成を戻す場合は対象コミットを`git revert`してpushする。DB・ボリューム・暗号鍵の状態はGitのrevertでは戻らない。

2026-10-25にはPeriphery内の`docker compose up --wait`が長時間残り、OCMSのDeployStackとGitOpsのSync・Procedureが`Resource is busy`になった。OCMSを正常起動した後、Peripheryコンテナだけを再起動して滞留処理を解消した。続けてGitOps Procedureの全段階と、KomodoからのOCMSのDeployStackが成功することを確認した。Core・MongoDB・DB・稼働アプリのデータは作り直していない。正当なデプロイの実行中にもbusyになるため、復旧前に実行履歴とPeriphery内のプロセスを確認する。

v0.3.0反映後は6サービスがhealthy、migrateが終了コード0。既存4件のタグJob再処理とレーティングの保存、生成画像によるCPU版Taggerの応答、OCMS・Pocket ID・Komodoの公開URLのHTTP 200を確認した。Embedding・Whisperの新ONNXモデルは別途事前転送を進めており、完了後に固定コミットのSHA-256を照合して既存モデル用ボリュームへ配置する。

用語: Resource SyncはTOMLとKomodoのリソースを同期する機能、Procedureは順番に実行する処理、StackはComposeのデプロイ単位。
