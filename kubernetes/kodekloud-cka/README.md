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

