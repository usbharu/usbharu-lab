#!/usr/bin/sh

curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null
sudo apt-get install apt-transport-https --yes
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
sudo apt-get update
sudo apt-get install helm

sudo apt install -yqq nfs-common daemonize dbus-user-session fontconfig

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server" sh -s - --disable=traefik --disable=servicelb --cluster-init --disable=local-storage

sleep 5

sudo k3s kubectl create namespace argocd
sudo k3s kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

sleep 10

sudo kubectl config view --raw

sudo helm --kubeconfig /etc/rancher/k3s/k3s.yaml install init ./argocd

sleep 10

sudo k3s kubectl -n argocd get secret/argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d; echo