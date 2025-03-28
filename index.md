## ネットワーク

[network/index.md](network/index.md)を実行する

## Proxmox VE

[pve/index.md](pve/index.md)を実行する

## クラスタの立ち上げ

3つVMを立ち上げ、そのうち1つで作業を始める

init.shを実行する

`argocd/values-local.yaml`にクレデンシャルを書き込む

```bash
sudo helm upgrade --kubeconfig /etc/rancher/k3s/k3s.yaml -f argocd/values-local.yaml -i init argocd/
```

を実行する

その他のVMでinit-agent.shを編集してから実行する

https://docs.k3s.io/ja/datastore/ha-embedded