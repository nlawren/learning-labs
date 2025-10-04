# Notes on the CKA course

## Useful websites

- Kller [Coda](https://killercoda.com/cka) <- excellent set of scenarios

## Certification tips

## Useful commands

### Imperative commands

```sh
kubectl config view <- check the context section (cluster, namespace,user)
kubectl config use-context k8s
kubectl get events -n node or just kubectl get events
kubectl logs
kubectl run nginx --image nginx
kubectl create deployment nginx --image=nginx
kubectl expose deployment nginx --port 80
kubectl edit deployment nginx
kubectl scale deployment nginx --replicas=3
kubectl set image deployment nginx nginx=nginx:1.18
kubectl run httpd --image=httpd:alpine --port=80 --expose <- this creates both a pod and a service called httpd and exposes the pod on port 80
```

### Declarative commands

* create objects: `kubectl apply -f nginx.yaml`
* update objects: `kubectl apply -f nginx.yaml` - yes, the same command, but with an updated nginx.yaml file.

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
# then
kubectl create -f pod-nginx.yaml
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

## 61 - Taints and Tolerations

Three types of taint: NoSchedule, PreferNoSchedule, NoExecute

`kubectl taint node node01 key = value:effect(eg NoSchedule)` creates a taint.
`kubectl taint node node01 key = value:effect-` removes the taint.
Note: you can't create a pod from a kubectl command on the cli, rather use --dry-run=client -o yaml and edit the yaml file with the appropriate toleration statements.
