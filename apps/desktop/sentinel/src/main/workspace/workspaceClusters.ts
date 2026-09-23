import type { SetupStep } from './workspaceTools.js';

const kindImage = 'kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed';
const k3sImage = 'rancher/k3s:v1.36.4-k3s1';

function binary(tool: 'kind' | 'k3d'): string {
  const kind = tool === 'kind';
  const version = kind ? 'v0.33.0' : 'v5.9.0';
  const repo = kind ? 'kubernetes-sigs/kind' : 'k3d-io/k3d';
  const arm = kind ? '20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2' : '03cde5cf23e6e8e67de5a039ecf26e5b85aca82fba3e5d13dadf904cd218a250';
  const amd = kind ? 'aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d' : '06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0';
  return `
if ${tool} version 2>/dev/null | grep -F '${version}' >/dev/null; then exit 0; fi
case "$(uname -m)" in
  aarch64) arch=arm64; checksum=${arm} ;;
  x86_64) arch=amd64; checksum=${amd} ;;
  *) echo 'Unsupported cluster architecture' >&2; exit 1 ;;
esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 2 --connect-timeout 15 --max-time 300 'https://github.com/${repo}/releases/download/${version}/${tool}-linux-'"$arch" -o "$work/${tool}"
printf '%s  %s\\n' "$checksum" "$work/${tool}" | sha256sum -c -
install -m 755 "$work/${tool}" /usr/local/bin/${tool}
`;
}

const registry = String.raw`
umask 077
mkdir -p /etc/sentinel/containers
if ! docker container inspect sentinel-registry >/dev/null 2>&1; then
  docker run -d --name sentinel-registry --label dev.sentinel.managed=registry --restart unless-stopped \
    -p 127.0.0.1:5001:5000 -v sentinel-registry-data:/var/lib/registry registry:3.0.0
else
  test "$(docker inspect -f '{{index .Config.Labels "dev.sentinel.managed"}}' sentinel-registry)" = registry || { echo 'The name sentinel-registry is already in use.' >&2; exit 1; }
  docker start sentinel-registry >/dev/null
fi
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:5001/v2/ >/dev/null; then exit 0; fi
  sleep 1
done
echo 'The workspace image registry did not become ready.' >&2
exit 1
`;

const builder = String.raw`
umask 077
cat > /etc/sentinel/containers/buildkit.toml <<'CONFIG'
[registry."localhost:5001"]
  http = true
[registry."127.0.0.1:5001"]
  http = true
CONFIG
if ! docker buildx inspect sentinel >/dev/null 2>&1; then
  docker buildx create --name sentinel --driver docker-container \
    --driver-opt image=moby/buildkit:v0.33.0,network=host,default-load=true \
    --buildkitd-config /etc/sentinel/containers/buildkit.toml
fi
docker buildx inspect sentinel --bootstrap
# Select once; later provisioning preserves an agent's chosen builder.
if [ ! -f /etc/sentinel/containers/builder-ready ]; then
  docker buildx use sentinel
  touch /etc/sentinel/containers/builder-ready
fi
`;

const contextStart = String.raw`
umask 077
mkdir -p /root/.kube /etc/sentinel/containers
previous_context=$(kubectl config current-context 2>/dev/null || true)
restore_context() { if [ -n "$previous_context" ]; then kubectl config use-context "$previous_context" >/dev/null; fi; }
trap restore_context EXIT
wait_for_cluster() {
  # After a VM restart the API may answer before its authorization caches are ready.
  for i in $(seq 1 12); do
    if timeout 8 kubectl --context "$1" --request-timeout=5s get nodes -o name > /etc/sentinel/containers/nodes.next 2>/dev/null && [ -s /etc/sentinel/containers/nodes.next ]; then
      timeout 130 kubectl --context "$1" --request-timeout=5s wait --for=condition=Ready nodes --all --timeout=120s
      return
    fi
    sleep 2
  done
  timeout 8 kubectl --context "$1" --request-timeout=5s get nodes >&2 || true
  echo 'The Kubernetes API did not become ready.' >&2
  return 1
}
`;

const kindCluster = contextStart + `
export KIND_EXPERIMENTAL_PROVIDER=docker
# A stopped node container is not evidence that kubeadm finished bootstrapping.
# Recover only our single, empty control-plane container; never discard cluster data.
if docker container inspect sentinel-kind-control-plane >/dev/null 2>&1; then
  docker start sentinel-kind-control-plane >/dev/null
  bootstrap=$(timeout 15 docker exec sentinel-kind-control-plane sh -c '
    if test -s /var/lib/kubelet/config.yaml && test -s /etc/kubernetes/manifests/kube-apiserver.yaml; then
      echo initialized
    elif test ! -e /var/lib/kubelet/config.yaml && test ! -e /etc/kubernetes/manifests/kube-apiserver.yaml && test ! -e /etc/kubernetes/manifests/etcd.yaml && test ! -e /var/lib/etcd/member; then
      echo empty
    else
      echo partial
    fi
  ') || { echo 'Could not inspect the kind control plane. Cluster data was kept.' >&2; exit 1; }
  case "$bootstrap" in
    empty)
      if test -e /etc/sentinel/containers/kind-ready || test "$(kind get nodes --name sentinel-kind)" != sentinel-kind-control-plane; then
        echo 'The existing kind cluster is incomplete. Cluster data was kept; inspect it before retrying.' >&2
        exit 1
      fi
      test "$(docker inspect -f '{{index .Config.Labels "io.x-k8s.kind.cluster"}}' sentinel-kind-control-plane)" = sentinel-kind || exit 1
      test "$(docker inspect -f '{{index .Config.Labels "io.x-k8s.kind.role"}}' sentinel-kind-control-plane)" = control-plane || exit 1
      echo 'Recovering interrupted kind initialization: replacing its empty control-plane container.'
      docker rm -f sentinel-kind-control-plane >/dev/null
      ;;
    initialized) ;;
    *) echo 'The kind control plane is partially initialized. Cluster data was kept; inspect it before retrying.' >&2; exit 1 ;;
  esac
fi
if ! kind get clusters | grep -Fx sentinel-kind >/dev/null; then
  cat > /etc/sentinel/containers/kind.yaml <<'CONFIG'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: "127.0.0.1"
  apiServerPort: 6443
nodes:
  - role: control-plane
CONFIG
  kind create cluster --name sentinel-kind --image '${kindImage}' --config /etc/sentinel/containers/kind.yaml --wait 180s
else
  docker start sentinel-kind-control-plane >/dev/null
  kind export kubeconfig --name sentinel-kind
fi
docker update --restart unless-stopped sentinel-kind-control-plane >/dev/null
if ! docker network inspect kind -f '{{range .Containers}}{{println .Name}}{{end}}' | grep -Fx sentinel-registry >/dev/null; then
  docker network connect kind sentinel-registry
fi
docker exec sentinel-kind-control-plane mkdir -p /etc/containerd/certs.d/localhost:5001
docker exec -i sentinel-kind-control-plane sh -c 'cat > /etc/containerd/certs.d/localhost:5001/hosts.toml' <<'CONFIG'
[host."http://sentinel-registry:5000"]
  capabilities = ["pull", "resolve"]
CONFIG
wait_for_cluster kind-sentinel-kind
touch /etc/sentinel/containers/kind-ready
`;

const k3sCluster = contextStart + `
if ! k3d cluster list -o json | jq -e '.[] | select(.name == "sentinel-k3s")' >/dev/null; then
  cat > /etc/sentinel/containers/k3s-registries.yaml <<'CONFIG'
mirrors:
  "localhost:5001":
    endpoint:
      - "http://sentinel-registry:5000"
CONFIG
  k3d cluster create sentinel-k3s --servers 1 --agents 0 --no-lb \\
    --api-port 127.0.0.1:6445 --image '${k3sImage}' \\
    --registry-config /etc/sentinel/containers/k3s-registries.yaml --timeout 180s
else
  k3d cluster start sentinel-k3s --wait --timeout 180s
  k3d kubeconfig merge sentinel-k3s --kubeconfig-switch-context=false >/dev/null
fi
docker update --restart unless-stopped k3d-sentinel-k3s-server-0 >/dev/null
if ! docker network inspect k3d-sentinel-k3s -f '{{range .Containers}}{{println .Name}}{{end}}' | grep -Fx sentinel-registry >/dev/null; then
  docker network connect k3d-sentinel-k3s sentinel-registry
fi
wait_for_cluster k3d-sentinel-k3s
`;

export function clusterSetupSteps(tools: string[]): SetupStep[] {
  const steps: SetupStep[] = [];
  const add = (message: string, script: string, timeout = 600) => steps.push({ message, arguments: ['sh', '-ec', script], timeout });
  if (tools.includes('kind')) add('Installing kind…', binary('kind'), 360);
  if (tools.includes('k3s')) add('Installing k3d…', binary('k3d'), 360);
  if (tools.some(tool => ['kind', 'k3s', 'docker-builder'].includes(tool))) add('Preparing your image registry…', registry);
  if (tools.includes('docker-builder')) add('Preparing your Docker builder…', builder);
  if (tools.includes('kind')) add('Starting your kind cluster…', kindCluster, 900);
  if (tools.includes('k3s')) add('Starting your K3s cluster…', k3sCluster, 900);
  if (steps.length) {
    const contexts = [tools.includes('kind') ? 'kind-sentinel-kind' : '', tools.includes('k3s') ? 'k3d-sentinel-k3s' : ''].filter(Boolean);
    add('Finishing container setup…', `cat > /etc/sentinel/containers/README.md <<'DOC'
# Workspace containers

Private registry: localhost:5001
${tools.includes('docker-builder') ? 'Build and publish: docker buildx build --builder sentinel --push -t localhost:5001/my-app:dev .\n' : ''}
${contexts.length ? 'Kubernetes contexts: ' + contexts.join(', ') + '\nChoose: kubectl config use-context <context>\nUse localhost:5001/my-app:dev as the image in your manifests.\nExpose a service inside this workspace: kubectl port-forward service/<name> 8080:80\nUse Sentinel port_forward only when the user needs a preview outside the workspace.' : ''}

Storage and credentials stay on this workspace's private disk. Stop/start preserves them.
Ordinary tool setup reuses existing clusters and preserves their data and versions.
Reinstall erases the private Linux disk, including clusters, registry, build cache and credentials, then creates fresh selected tools.
Deleting the workspace also removes this private data. Neither operation deletes the mounted host project folder.
DOC
`, 10);
  }
  return steps;
}
