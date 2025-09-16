#!/usr/bin/env python3
"""
Kubernetes 클러스터에서 GPU 노드 존재/상태(Ready)를 점검하는 스크립트.
- GPU 판별: node.status.capacity 에 다음 키들 중 하나가 있고 값 > 0
    - 'nvidia.com/gpu', 'amd.com/gpu', 'gpu.intel.com/i915', 'intel.com/gpu'
- Ready 판별: NodeCondition(type='Ready', status='True')
- 실행 환경:
    - 기본: 로컬 kubeconfig(기본 경로 자동 탐지)
    - --kubeconfig 경로 직접 지정
- 출력:
    - 기본: 요약 로그 + 표 형태 텍스트
    - --json: 기계가 읽기 쉬운 JSON
- 슬랙 알림:
    - --slack-webhook: 슬랙 웹훅 URL (환경변수 SLACK_WEBHOOK_URL로도 설정 가능)
    - --slack-username: 슬랙 봇 사용자명 (기본: GPU Checker)
    - --slack-only-on-error: GPU 노드가 없거나 Ready 상태가 아닐 때만 슬랙 메시지 전송
Exit Codes:
    0: Ready GPU 노드 ≥ 1
    2: GPU 노드 0
    3: GPU 노드는 있으나 Ready GPU 노드 0
    1: 기타 예외
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import requests
from requests.exceptions import ConnectionError, Timeout, RequestException
from kubernetes import client, config
from kubernetes.client import V1Node, V1NodeCondition
from dotenv import load_dotenv


GPU_RESOURCE_KEYS = [
    "nvidia.com/gpu",
    "amd.com/gpu",
    "gpu.intel.com/i915",
    "intel.com/gpu",
]


def send_slack_message(webhook_url: str, message: str, username: str = "k8s-gpu-checker", 
                      max_retries: int = 3, retry_delay: int = 30) -> bool:
    """
    슬랙 웹훅을 통해 메시지를 전송합니다. 네트워크 오류시 재시도합니다.
    
    Args:
        webhook_url: 슬랙 웹훅 URL
        message: 전송할 메시지
        username: 봇 사용자명
        max_retries: 최대 재시도 횟수 (기본: 3)
        retry_delay: 재시도 간격(초) (기본: 30)
    
    Returns:
        bool: 전송 성공 여부
    """
    if not webhook_url:
        return False
    
    payload = {
        "text": message,
        "username": username,
        "icon_emoji": ":robot_face:"
    }
    
    for attempt in range(max_retries + 1):  # 0, 1, 2, 3 (총 4번 시도)
        try:
            response = requests.post(
                webhook_url, 
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                if attempt > 0:
                    print(f"✅ 슬랙 메시지를 {attempt + 1}번째 시도에서 성공적으로 전송했습니다.", file=sys.stderr)
                return True
            else:
                print(f"슬랙 메시지 전송 실패 (HTTP {response.status_code}): {response.text}", file=sys.stderr)
                
        except (ConnectionError, Timeout) as e:
            # 네트워크 연결 오류나 타임아웃의 경우 재시도
            if "Connection reset by peer" in str(e) or "Connection aborted" in str(e):
                if attempt < max_retries:
                    print(f"슬랙 메시지 전송 실패 ({attempt + 1}/{max_retries + 1}회 시도): {e}", file=sys.stderr)
                    print(f"⏳ {retry_delay}초 후 재시도합니다...", file=sys.stderr)
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"슬랙 메시지 전송 최종 실패: {e}", file=sys.stderr)
                    return False
            else:
                print(f"슬랙 메시지 전송 실패: {e}", file=sys.stderr)
                return False
                
        except RequestException as e:
            # 기타 requests 관련 예외
            print(f"슬랙 메시지 전송 실패: {e}", file=sys.stderr)
            return False
            
        except Exception as e:
            # 기타 예외
            print(f"슬랙 메시지 전송 실패: {e}", file=sys.stderr)
            return False
    
    return False


def format_slack_message(gpu_nodes: List[Dict], ready_gpu_nodes: List[Dict]) -> str:
    """GPU 노드 상태를 슬랙 메시지 형식으로 포맷합니다."""
    if ready_gpu_nodes:
        status_emoji = "✅"
        status_text = f"Ready 상태의 GPU 노드: {len(ready_gpu_nodes)}개 / 전체 GPU 노드: {len(gpu_nodes)}개"
    elif gpu_nodes:
        status_emoji = "⚠️"
        status_text = f"GPU 노드는 {len(gpu_nodes)}개 있으나, Ready 상태 노드는 없습니다."
    else:
        status_emoji = "❌"
        status_text = "GPU 노드가 없습니다."
    
    message = f"{status_emoji} *K8s GPU 노드 상태*\n{status_text}"
    
    if gpu_nodes:
        message += "\n\n*노드 상세 정보:*"
        for node in gpu_nodes:
            ready_status = "✅ Ready" if node["ready"] else "❌ Not Ready"
            gpu_info = f"GPU: {node['gpus']}"
            if node["gpu_breakdown"]:
                gpu_details = ", ".join([f"{k}:{v}" for k, v in node["gpu_breakdown"].items()])
                gpu_info += f" ({gpu_details})"
            
            message += f"\n• `{node['name']}`: {ready_status}, {gpu_info}"
    
    return message


def get_slack_webhook_url(args: argparse.Namespace) -> Optional[str]:
    """슬랙 웹훅 URL을 가져옵니다 (인자 또는 환경변수에서)."""
    return args.slack_webhook or os.environ.get("SLACK_WEBHOOK_URL")


def should_send_slack_message(args: argparse.Namespace, gpu_nodes: List[Dict], ready_gpu_nodes: List[Dict]) -> bool:
    """슬랙 메시지를 전송해야 하는지 판단합니다."""
    webhook_url = get_slack_webhook_url(args)
    if not webhook_url:
        return False
    
    # --slack-only-on-error 옵션이 설정된 경우
    if args.slack_only_on_error:
        return len(ready_gpu_nodes) == 0  # Ready GPU 노드가 없을 때만 전송
    
    return True  # 기본적으로 항상 전송


def load_kube_config(args: argparse.Namespace) -> None:
    if args.kubeconfig:
        config.load_kube_config(config_file=args.kubeconfig)
        return
    # 기본 동작: 환경변수 KUBECONFIG 또는 ~/.kube/config 탐색
    kubeconfig = os.environ.get("KUBECONFIG")
    if kubeconfig and os.path.exists(kubeconfig):
        config.load_kube_config(config_file=kubeconfig)
    else:
        config.load_kube_config()


def is_ready(node: V1Node) -> bool:
    if not node.status or not node.status.conditions:
        return False
    for cond in node.status.conditions:
        if isinstance(cond, V1NodeCondition) and cond.type == "Ready" and cond.status == "True":
            return True
    return False


def gpu_capacity(node: V1Node) -> Dict[str, int]:
    caps = {}
    status = node.status or None
    if not status or not status.capacity:
        return caps
    for key in GPU_RESOURCE_KEYS:
        val = status.capacity.get(key)
        if not val:
            continue
        # Kubernetes resource quantities: 정수 문자열로 들어오는 경우가 일반적
        try:
            caps[key] = int(str(val))
        except Exception:
            # 혹시나 정수가 아닌 포맷이면 best-effort 무시
            pass
    return caps


def extract_node_info(node: V1Node) -> Dict:
    caps = gpu_capacity(node)
    total_gpus = sum(caps.values()) if caps else 0
    return {
        "name": node.metadata.name if node.metadata else "",
        "ready": is_ready(node),
        "gpus": total_gpus,
        "gpu_breakdown": caps,
        "labels": node.metadata.labels if node.metadata and node.metadata.labels else {},
        "taints": [
            {"key": t.key, "value": t.value, "effect": t.effect}
            for t in (node.spec.taints or [])
        ] if node.spec and getattr(node.spec, "taints", None) else [],
    }


def list_gpu_nodes(api: client.CoreV1Api) -> Tuple[List[Dict], List[Dict]]:
    """Returns (gpu_nodes, ready_gpu_nodes) as list of dicts."""
    nodes = api.list_node().items or []
    gpu_nodes = []
    ready_gpu_nodes = []
    for n in nodes:
        info = extract_node_info(n)
        if info["gpus"] > 0:
            gpu_nodes.append(info)
            if info["ready"]:
                ready_gpu_nodes.append(info)
    return gpu_nodes, ready_gpu_nodes


def print_table(gpu_nodes: List[Dict]) -> None:
    if not gpu_nodes:
        print("GPU 노드가 존재하지 않습니다.")
        return

    # 동적 폭 계산
    w_name = max(len("NAME"), max(len(node["name"]) for node in gpu_nodes))
    w_ready = len("READY")
    w_total = len("GPU(TOTAL)")
    w_keys = len("GPU(KEYS)")

    print(f"{'NAME'.ljust(w_name)}  {'READY'.ljust(w_ready)}  {'GPU(TOTAL)'.ljust(w_total)}  {'GPU(KEYS)'}")
    print(f"{'-'*w_name}  {'-'*w_ready}  {'-'*w_total}  {'-'*w_keys}")
    for node in gpu_nodes:
        keys_str = ",".join([f"{k}:{v}" for k, v in node["gpu_breakdown"].items()]) if node["gpu_breakdown"] else "-"
        print(
            f"{node['name'].ljust(w_name)}  "
            f"{str(node['ready']).ljust(w_ready)}  "
            f"{str(node['gpus']).ljust(w_total)}  "
            f"{keys_str}"
        )


def one_shot(args: argparse.Namespace) -> int:
    api = client.CoreV1Api()
    gpu_nodes, ready_gpu_nodes = list_gpu_nodes(api)

    # 슬랙 메시지 전송
    if should_send_slack_message(args, gpu_nodes, ready_gpu_nodes):
        webhook_url = get_slack_webhook_url(args)
        if webhook_url:
            slack_message = format_slack_message(gpu_nodes, ready_gpu_nodes)
            success = send_slack_message(
                webhook_url, 
                slack_message, 
                args.slack_username,
                max_retries=args.slack_retry_count,
                retry_delay=args.slack_retry_delay
            )
            if success and not args.json:
                print("✅ 슬랙 메시지를 성공적으로 전송했습니다.")
            elif not success and not args.json:
                print("❌ 슬랙 메시지 전송에 실패했습니다.", file=sys.stderr)

    if args.json:
        payload = {
            "total_nodes": len(gpu_nodes),
            "ready_nodes": len(ready_gpu_nodes),
            "nodes": gpu_nodes,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if ready_gpu_nodes:
            print(f"✅ Ready 상태의 GPU 노드: {len(ready_gpu_nodes)}개 / 전체 GPU 노드: {len(gpu_nodes)}개")
        elif gpu_nodes:
            print(f"⚠️ GPU 노드는 {len(gpu_nodes)}개 있으나, Ready 상태 노드는 없습니다.")
        else:
            print("❌ GPU 노드가 없습니다.")
        print_table(gpu_nodes)

    if ready_gpu_nodes:
        return 0
    if gpu_nodes and not ready_gpu_nodes:
        return 3
    return 2  # gpu_nodes == 0




def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kubernetes GPU 노드 점검 스크립트")
    p.add_argument("--kubeconfig", help="kubeconfig 경로 직접 지정")
    p.add_argument("--json", action="store_true", help="JSON 형태로만 출력(머신 판독용)")

    # 슬랙 관련 옵션들
    slack_group = p.add_argument_group("슬랙 알림", "슬랙으로 메시지를 전송하는 옵션들")
    slack_group.add_argument("--slack-webhook", help="슬랙 웹훅 URL (환경변수 SLACK_WEBHOOK_URL로도 설정 가능)")
    slack_group.add_argument("--slack-username", default="k8s-gpu-checker", help="슬랙 봇 사용자명 (기본: k8s-gpu-checker)")
    slack_group.add_argument("--slack-only-on-error", action="store_true", help="GPU 노드가 없거나 Ready 상태가 아닐 때만 슬랙 메시지 전송")
    slack_group.add_argument("--slack-retry-count", type=int, default=3, help="슬랙 메시지 전송 실패시 최대 재시도 횟수 (기본: 3)")
    slack_group.add_argument("--slack-retry-delay", type=int, default=30, help="슬랙 메시지 재시도 간격(초) (기본: 30)")

    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        load_kube_config(args)
        return one_shot(args)
    except Exception as e:
        # 오류 상황에서도 JSON 모드일 경우 기계가 읽기 쉬운 에러 출력
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            import traceback
            print(f"에러: {e}", file=sys.stderr)
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    load_dotenv()
    sys.exit(main())
