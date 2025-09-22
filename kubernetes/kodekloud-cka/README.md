# Notes on the CKA course

## Useful commands

### 29 - ReplicaSets and ReplicationController

```sh
kubectl create -f replicaset-definition.yaml
kubectl get replicaset
kubectl delete replicaset myapp-replicaset
kubectl replace -f replicaset-definition.yaml
kubectl scale --replicas=6 -f replicaset-definition.yaml
```

### 33 - Certification tip and using kubectl

```sh
kubectl run nginx --image nginx
kubectl run nginx --image nginx --dry-run=client -o yaml > pod-nginx.yaml
kubectl create deployment nginx --image=nginx
kubectl create deployment nginx --image=nginx --dry-run=client -o yaml --replicas=3 > nginx-deployment.yaml
```

## 42 - Namespaces

```sh
kubectl create namespace dev
kubectl create -f pod-definition.yaml --namespace=dev
kubectl config set-context $(kubectl config current-context) --namespace=dev
```

Or use a node-definition.yaml file that has `namespace: dev` in the metadata section (see 42.1.pod-definition.yaml)

To create a namespace, either use a 'kind: Namespace' in a definition yaml file (see 42.2.namespace-definition.yaml file) or the kubectl command above.

To set resource quota limits for a namespace, use a `ResourceQuota` yaml file (see 42.3 compute-quota.yaml).
