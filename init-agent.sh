#!/usr/bin/sh

curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null
sudo apt-get install apt-transport-https --yes
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
sudo apt-get update
sudo apt-get install helm

sudo apt install -yqq nfs-common daemonize dbus-user-session fontconfig


exit 1
# edit
#curl -sfL https://get.k3s.io | K3S_TOKEN=SECRET sh -s - server --server https://{server-ip}:6443 --disable=traefik --disable=servicelb --disable=local-storage