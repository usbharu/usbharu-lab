## pveをインストールする

### pveのインストールのためにisoを作成する

https://qiita.com/rk76feWF/items/b5d9dfb7cf78cbd3cbb3#%E4%BD%BF%E3%81%84%E6%96%B9

自動インストール用iso作成ツールをインストール

```bash
wget http://download.proxmox.com/debian/pve/dists/bookworm/pve-no-subscription/binary-amd64/proxmox-auto-install-assistant_8.4.2_amd64.deb -O proxmox-auto-install-assistant.deb
sudo    dpkg -i proxmox-auto-install-assistant.deb
```

isoをダウンロード

```bash
https://enterprise.proxmox.com/iso/proxmox-ve_8.3-1.iso

```

isoにanswer.tomlを埋め込む

```bash
proxmox-auto-install-assistant prepare-iso proxmox-ve_8.3-1.iso --fetch-from iso --answer-file answer.toml.1-pve.toml --output pve1-autoinstall.iso
```

### pveの設定

言語を日本語に変更してログインする

pve1でクラスタを作成し、pve2でjoinする

enterpriseリポジトリを削除し、non-enterpriseリポジトリを追加してapt update とapt upgradeを実行する

### テンプレートの準備

https://blog.nishi.network/2020/11/05/proxmox-part3/

https://cloud-images.ubuntu.com/ から.imgのやつを選ぶ

qemu-guest-agentをあらかじめインストールしておく

isoは/var/lib/vz/template/isoにある

```bash
apt install libguestfs-tools

virt-customize -a jammy-server-cloudimg-amd64.img --install qemu-guest-agent
```

### テンプレートの作成

https://blog.nishi.network/2020/11/05/proxmox-part3/

```bash
qm create 9000 --memory 2048 --net0 virtio,bridge=vmbr0

qm importdisk 9000 focal-server-cloudimg-amd64.img local-lvm

qm set 9000 --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-9000-disk-0

qm set 9000 --ide2 local-lvm:cloudinit

qm set 9000 --boot c --bootdisk scsi0

qm set 9000 --serial0 socket --vga serial0

qm template 9000
```

Cloud-initのipとユーザー名、パスワードを設定する