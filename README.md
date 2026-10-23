# usbharu-lab

Komodo 2.3.4で管理するサーバーの構成。`komodo`ブランチを同期元とし、GitHubへのpush後に設定とComposeを反映する。

日常の変更は「変更を反映する」、認証情報の更新は「機密情報の置き場所」を参照する。操作には、このリポジトリへのpush権限とKomodoの管理権限が必要。

## 変更を反映する

1. `komodo/resources.toml`、`stacks/ocms/compose.yaml`、`stacks/management/`を編集する。
2. 差分を確認して`komodo`ブランチにコミット・pushする。
3. [Komodo](https://komodo.usbharu.dev)のProcedure `usbharu-lab-gitops`で実行結果を確認する。

ProcedureはResource Sync `usbharu-lab`でリソース定義を同期し、続いてOCMS、管理用スタックの順に`DeployStackIfChanged`を実行する。GitHub Webhookは`komodo`ブランチのpushに反応する。5分ごとのスケジュールも同じProcedureを実行するため、Webhookの取りこぼしからも復帰できる。

Gitを設定の基準にするため、KomodoのUIで行った構成変更は次の同期で戻る。Secretの更新はUIで行う。Resource Syncの`managed`（UIからGitへ書き戻す機能）、リソースの自動削除、変数・ユーザーグループの同期は無効。

## 管理する構成

| 場所 | 内容 |
| --- | --- |
| `komodo/resources.toml` | 2つのStack、Resource Sync、GitOps用Procedure |
| `stacks/management/compose.yaml` | Komodo Core・Periphery、MongoDB、Pocket ID、Traefik |
| `stacks/management/routes.yaml` | OCMS・Pocket ID・KomodoへのHTTPルーティング |
| `stacks/ocms/compose.yaml` | OCMSの6サービス。ホスト上のPostgreSQL 17とCloudflare R2を使用 |

既存のServerリソース`ocms-ubuntu`を使用する。Composeのプロジェクト名`ocms`・`ocms-management`はデータ用ボリュームの名前に関わるため、移行なしで変更しない。

OCMSはGitHub Actionsが公開する`ghcr.io/usbharu/ocms:v0.1.1`と`ghcr.io/usbharu/ocms-runtime:v0.1.1`を指定する。Release公開とpublish成功を確認してからComposeのタグを更新し、pushする。タグの変更でKomodoがpull・デプロイする。ソースのビルド・DBマイグレーション・モデル取得はこのGitOpsには含まれない。

現在のサーバーは空き容量が約5.2 GiBで、GHCR Runtimeは圧縮状態でも約3.36 GiBある。Runtimeはホストの`ocms.env`に`OCMS_RUNTIME_IMAGE=ocms-runtime:local`を設定し、検証済みのCPU版を継続使用する。十分なディスク容量を確保した後、この変数を削除してKomodoでOCMSを手動デプロイし、モデルのロードとメディア処理を確認する。ホスト変数はGitのファイル差分として検知されない。

Cloudflare Tunnelはホストのsystemdサービスで動く。3つの公開ホスト名の転送先は`http://127.0.0.1:18080`。Tunnel・ホストのPostgreSQL・既存の他アプリは、このリポジトリから変更しない。

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
3. Pending差分を確認してSyncを実行する。Stack定義とProcedureが作成される。
4. Procedure `usbharu-lab-gitops`を実行する。必要ならGitHubのpush用Webhookを`https://komodo.usbharu.dev/listener/github/procedure/usbharu-lab-gitops/komodo`へ登録する。Content typeはJSON、Secretはサーバーの`KOMODO_WEBHOOK_SECRET`と同じ値を安全な経路で設定する。

自動反映を止める場合は、GitHub側のWebhookを無効にし、Gitの`procedure.config.schedule_enabled`と`webhook_enabled`を`false`へ変更して同期する。緊急時はまずKomodoのUIで同じ設定を無効にしてからGitにも反映する。

障害時はKomodoのProcedure・Sync・StackのUpdatesを確認する。管理画面へ接続できなければSSHで`docker ps`と`systemctl status cloudflared`を確認する。構成を戻す場合は対象コミットを`git revert`してpushする。DB・ボリューム・暗号鍵の状態はGitのrevertでは戻らない。

用語: Resource SyncはTOMLとKomodoのリソースを同期する機能、Procedureは順番に実行する処理、StackはComposeのデプロイ単位。
