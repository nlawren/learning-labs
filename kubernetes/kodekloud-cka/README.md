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

