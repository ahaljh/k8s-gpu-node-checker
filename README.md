# K8s GPU Node Checker

Kubernetes 클러스터에서 GPU 노드의 존재 여부와 Ready 상태를 점검하는 스크립트입니다.

## 🚀 기능

- **GPU 노드 탐지**: `nvidia.com/gpu`, `amd.com/gpu`, `gpu.intel.com/i915`, `intel.com/gpu` 리소스 기반
- **Ready 상태 확인**: NodeCondition의 Ready 상태 체크
- **슬랙 알림**: 웹훅을 통한 슬랙 메시지 전송
- **JSON 출력**: 머신 판독용 JSON 형식 지원
- **로컬 실행**: kubeconfig를 통한 로컬 환경에서 실행

## 📦 설치

```bash
# 의존성 설치
uv sync

# 또는 pip로 설치
pip install kubernetes requests
```

## 🎯 사용법

### 기본 사용

```bash
# 현재 GPU 노드 상태 확인
uv run python check-gpu-node.py

# kubeconfig 파일 직접 지정
uv run python check-gpu-node.py --kubeconfig /path/to/kubeconfig
```

### 슬랙 알림

```bash
# 슬랙 웹훅 URL 직접 지정
uv run python check-gpu-node.py --slack-webhook https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# 환경변수로 웹훅 URL 설정
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
uv run python check-gpu-node.py

# 에러 상황에서만 슬랙 메시지 전송
uv run python check-gpu-node.py --slack-webhook YOUR_URL --slack-only-on-error

# 슬랙 봇 사용자명 커스터마이징
uv run python check-gpu-node.py --slack-webhook YOUR_URL --slack-username "GPU-Monitor"
```

### JSON 출력

```bash
# JSON 형식으로 출력
uv run python check-gpu-node.py --json
```

## 📋 명령줄 옵션

### 기본 옵션

| 옵션 | 설명 |
|------|------|
| `--kubeconfig PATH` | kubeconfig 파일 경로 직접 지정 |
| `--json` | JSON 형태로만 출력 (머신 판독용) |

### 슬랙 알림 옵션

| 옵션 | 설명 |
|------|------|
| `--slack-webhook URL` | 슬랙 웹훅 URL (환경변수 `SLACK_WEBHOOK_URL`로도 설정 가능) |
| `--slack-username NAME` | 슬랙 봇 사용자명 (기본: k8s-gpu-checker) |
| `--slack-only-on-error` | GPU 노드가 없거나 Ready 상태가 아닐 때만 슬랙 메시지 전송 |

## 🔧 환경변수

| 변수명 | 설명 |
|--------|------|
| `KUBECONFIG` | kubeconfig 파일 경로 |
| `SLACK_WEBHOOK_URL` | 슬랙 웹훅 URL |

## 📊 출력 예시

### 콘솔 출력

```
✅ Ready 상태의 GPU 노드: 2개 / 전체 GPU 노드: 2개
NAME                READY  GPU(TOTAL)  GPU(KEYS)
----                -----  ----------  ---------
gpu-node-1          True   4           nvidia.com/gpu:4
gpu-node-2          True   8           nvidia.com/gpu:8
✅ 슬랙 메시지를 성공적으로 전송했습니다.
```

### JSON 출력

```json
{
  "total_nodes": 2,
  "ready_nodes": 2,
  "nodes": [
    {
      "name": "gpu-node-1",
      "ready": true,
      "gpus": 4,
      "gpu_breakdown": {
        "nvidia.com/gpu": 4
      },
      "labels": {
        "node.kubernetes.io/instance-type": "g4dn.xlarge"
      },
      "taints": []
    }
  ]
}
```

### 슬랙 메시지

```
✅ K8s GPU 노드 상태
Ready 상태의 GPU 노드: 2개 / 전체 GPU 노드: 2개

노드 상세 정보:
• `gpu-node-1`: ✅ Ready, GPU: 4 (nvidia.com/gpu:4)
• `gpu-node-2`: ✅ Ready, GPU: 8 (nvidia.com/gpu:8)
```

## 🚨 종료 코드

| 코드 | 의미 |
|------|------|
| `0` | Ready GPU 노드가 1개 이상 존재 |
| `1` | 기타 예외 (네트워크 오류, 권한 문제 등) |
| `2` | GPU 노드가 0개 |
| `3` | GPU 노드는 있으나 Ready 상태인 노드가 0개 |

## 🔐 권한 설정

### 로컬 실행

kubeconfig 파일이 필요하며, 다음 권한이 있어야 합니다:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: gpu-node-checker
rules:
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get", "list"]
```

## 📱 슬랙 웹훅 설정

1. 슬랙 워크스페이스에서 **Apps** → **Incoming Webhooks** 검색
2. **Add to Slack** 클릭
3. 메시지를 받을 채널 선택
4. **Add Incoming WebHooks integration** 클릭
5. **Webhook URL** 복사하여 사용

## 🛠️ 실제 사용 시나리오

### 1. CI/CD 파이프라인에서 GPU 가용성 확인

```bash
#!/bin/bash
# GPU 노드가 Ready 상태인지 확인 후 ML 작업 실행
if uv run python check-gpu-node.py --slack-only-on-error; then
    echo "GPU 노드가 준비되었습니다. ML 작업을 시작합니다."
    kubectl apply -f ml-job.yaml
else
    echo "GPU 노드가 준비되지 않았습니다. 작업을 중단합니다."
    exit 1
fi
```

### 2. 크론탭으로 주기적 모니터링

```bash
# 매 10분마다 GPU 노드 상태를 슬랙으로 알림 (에러 시에만)
*/10 * * * * cd /path/to/k8s-gpu-node-checker && uv run python check-gpu-node.py --slack-only-on-error
```

## 🤝 기여

버그 리포트나 기능 요청은 이슈로 등록해 주세요.

## 📄 라이선스

MIT License
