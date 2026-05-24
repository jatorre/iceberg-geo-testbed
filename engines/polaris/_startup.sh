#!/bin/bash
set -eux
exec > /var/log/polaris-startup.log 2>&1
apt-get update
apt-get install -y docker.io
systemctl enable --now docker

# Pull and run Polaris with an in-memory backend.
# Bootstrap a realm-level root principal: realm=POLARIS, client_id=root, secret=s3cr3t
docker pull apache/polaris:latest
docker rm -f polaris 2>/dev/null || true
docker run -d \
  --name polaris \
  --restart always \
  -p 8181:8181 \
  -p 8182:8182 \
  -e POLARIS_BOOTSTRAP_CREDENTIALS='POLARIS,root,s3cr3t' \
  -e QUARKUS_LOG_CATEGORY__ORG_APACHE_POLARIS__LEVEL=DEBUG \
  apache/polaris:latest

echo "polaris container running:"
docker ps
