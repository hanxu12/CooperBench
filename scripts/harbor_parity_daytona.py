"""Run the ORIGINAL CooperBench harness (coop, mini_swe_agent_v2, docker backend) on Daytona.

Each shard is one Daytona sandbox from docker:28.3.3-dind (the same DinD image Harbor uses):
dockerd + redis-server + the original repo at the fork's harbor-parity branch, then
`cooperbench run` (auto-eval on, the original default) on that shard's pairs. Logs come back as a
tarball; the sandbox is deleted.

Keys (OPENAI_API_KEY, OPENAI_BASE_URL) are read from this process's environment and handed to the
sandbox through the SDK's env_vars; they are never printed or written to a file.

    python orig_runner.py --run-name NAME --pairs pairs.json --shards 4 --concurrency 6 --out DIR
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shlex
import tarfile
import threading
import time
from pathlib import Path

from daytona import CreateSandboxFromImageParams, Daytona, Image, Resources

OWNER_LABEL = {"autoadapter_owner": "hanxu12"}
SETUP = r"""
set -e
# Docker Hub limits anonymous pulls to 100/hour per egress IP, which the shared Daytona egress hits;
# pull through Google's public Docker Hub mirror (same digests; dockerd falls back to Docker Hub).
(dockerd-entrypoint.sh dockerd --registry-mirror=https://mirror.gcr.io > /var/log/dockerd.log 2>&1 &)
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker info >/dev/null
apk add --no-cache python3 git bash redis curl >/dev/null
command -v uv >/dev/null || (apk add --no-cache uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null)
export PATH=/root/.local/bin:$PATH
(redis-server --daemonize yes >/dev/null)
rm -rf /root/CooperBench
git clone --quiet --branch {branch} {repo_url} /root/CooperBench
cd /root/CooperBench
{checkout}
git log -1 --format='commit %H'
uv sync --python 3.12 --frozen >/root/uv-sync.log 2>&1
uv pip install --python .venv/bin/python {extra_pins} >/root/uv-pins.log 2>&1
.venv/bin/python -c "import litellm, mcp, importlib.metadata as m; print('litellm', m.version('litellm'), 'mcp', m.version('mcp'))"
echo SETUP_OK
"""


def shard_pairs(pairs: list[dict], n: int) -> list[list[dict]]:
    """Group by (repo, task_id) so each image is pulled in one sandbox; balance pair counts."""
    groups: dict[tuple, list[dict]] = {}
    for p in pairs:
        groups.setdefault((p["repo"], p["task_id"]), []).append(p)
    shards: list[list[dict]] = [[] for _ in range(n)]
    for _, g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        min(shards, key=len).extend(g)
    return [s for s in shards if s]


def subset_json(name: str, pairs: list[dict]) -> str:
    tasks: dict[tuple, list] = {}
    for p in pairs:
        tasks.setdefault((p["repo"], p["task_id"]), []).append(sorted(p["features"]))
    return json.dumps(
        {
            "name": name,
            "description": "harbor parity shard",
            "tasks": [
                {"repo": r, "task_id": t, "pairs": ps}
                for (r, t), ps in sorted(tasks.items())
            ],
        },
        indent=1,
    )


def run_shard(args, k: int, pairs: list[dict], out: Path, lock: threading.Lock) -> None:
    def log(msg: str) -> None:
        with lock:
            print(f"[shard {k}] {msg}", flush=True)

    d = Daytona()
    labels = {**OWNER_LABEL, "autoadapter_job": args.run_name}
    env_vars = {
        k_: os.environ[k_]
        for k_ in ("OPENAI_API_KEY", "OPENAI_BASE_URL")
        if k_ in os.environ
    }
    env_vars.update(
        {"MSWEA_COST_TRACKING": "ignore_errors"} if args.ignore_cost else {}
    )
    sb = d.create(
        CreateSandboxFromImageParams(
            image=Image.base("docker:28.3.3-dind"),
            labels=labels,
            env_vars=env_vars,
            resources=Resources(cpu=args.cpu, memory=args.memory, disk=args.disk),
            auto_stop_interval=60,
            auto_delete_interval=0,
            network_block_all=False,
        ),
        timeout=1800,
        on_snapshot_create_logs=lambda s: None,
    )
    try:
        checkout = (
            f"git checkout --quiet {shlex.quote(args.commit)}" if args.commit else ""
        )
        setup = SETUP.format(
            branch=args.branch,
            repo_url=args.repo_url,
            extra_pins=args.pins,
            checkout=checkout,
        )
        r = sb.process.exec(f"sh -c {shlex.quote(setup)}", timeout=3600)
        log(f"setup rc={r.exit_code}: {r.result.strip()[-400:]}")
        if "SETUP_OK" not in r.result:
            r2 = sb.process.exec(
                "tail -30 /root/uv-sync.log /root/uv-pins.log", timeout=60
            )
            raise RuntimeError(f"setup failed: {r2.result[-2000:]}")
        sub = f"{args.run_name.replace('-', '_')}_s{k}"
        enc = base64.b64encode(subset_json(sub, pairs).encode()).decode()
        sb.process.exec(
            f"sh -c 'echo {enc} | base64 -d > /root/CooperBench/dataset/subsets/{sub}.json'",
            timeout=60,
        )
        cmd = (
            "cd /root/CooperBench && export PATH=/root/.local/bin:$PATH && "
            f"uv run --frozen --no-sync cooperbench run --setting coop -a mini_swe_agent_v2 -m {args.model} "
            f"--subset {sub} --backend docker --concurrency {args.concurrency} --eval-concurrency {args.concurrency} "
            f"-n {args.run_name}; echo $? > /root/done"
        )
        sb.process.exec(
            "sh -c "
            + shlex.quote(
                "nohup sh -c " + shlex.quote(cmd) + " > /root/run.log 2>&1 &"
            ),
            timeout=60,
        )
        t0 = time.time()
        last = ""
        while True:
            time.sleep(60)
            base = f"/root/CooperBench/logs/{args.run_name}/coop"
            probe = (
                f'printf "%s|%s|%s" "$(cat /root/done 2>/dev/null)" '
                f'"$(ls {base}/*/*/*/result.json 2>/dev/null | wc -l)" "$(ls {base}/*/*/*/eval.json 2>/dev/null | wc -l)"'
            )
            try:
                r = sb.process.exec("sh -c " + shlex.quote(probe), timeout=120)
                done, n_res, n_eval = (r.result.strip().split("|") + ["", "", ""])[:3]
            except Exception as e:  # noqa: BLE001 - transient API error: keep polling
                log(f"poll error: {e}")
                continue
            status = f"results={n_res}/{len(pairs)} evals={n_eval} elapsed={int(time.time() - t0)}s"
            if status.split(" elapsed")[0] != last:
                log(status)
                last = status.split(" elapsed")[0]
            if done:
                log(f"finished rc={done} {status}")
                break
            if time.time() - t0 > args.max_seconds:
                log("max time reached; collecting what exists")
                break
        # The harness pulls images by tag; check that each one is the pinned digest.
        pinned = json.loads(args.digests.read_text())
        check = {}
        for repo, tid in sorted({(p["repo"], p["task_id"]) for p in pairs}):
            want = pinned[f"{repo}/task{tid}"]
            r = sb.process.exec(
                f"docker image inspect --format '{{{{json .RepoDigests}}}}' {shlex.quote(want['image'])}",
                timeout=120,
            )
            got = r.result.strip()
            check[want["image"]] = {
                "pinned": want["digest"],
                "repo_digests": got,
                "ok": want["digest"] in got,
            }
        bad = [k_ for k_, v in check.items() if not v["ok"]]
        log(
            f"image digests: {len(check) - len(bad)}/{len(check)} match the pins"
            + (f"; MISMATCH {bad}" if bad else "")
        )
        sb.process.exec(
            f"sh -c 'mkdir -p /root/CooperBench/logs/{args.run_name} && cp /root/run.log /root/CooperBench/logs/{args.run_name}/run.log; "
            f"cd /root/CooperBench && tar czf /root/out.tgz logs/{args.run_name}'",
            timeout=600,
        )
        data = sb.fs.download_file("/root/out.tgz")
        shard_dir = out / f"shard{k}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(shard_dir, filter="data")
        (shard_dir / "pairs.json").write_text(json.dumps(pairs, indent=1))
        (shard_dir / "image_digests_check.json").write_text(json.dumps(check, indent=1))
        if bad:
            raise RuntimeError(f"images differ from the pinned digests: {bad}")
        log(f"downloaded to {shard_dir}")
    finally:
        d.delete(sb)
        log("sandbox deleted")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--cpu", type=int, default=16)
    ap.add_argument("--memory", type=int, default=32)
    ap.add_argument("--disk", type=int, default=30)
    ap.add_argument("--model", default="openai/deepseek-v4.1-flash")
    ap.add_argument("--repo-url", default="https://github.com/hanxu12/CooperBench.git")
    ap.add_argument("--branch", default="harbor-parity")
    ap.add_argument(
        "--commit", default=None, help="exact fork commit to check out (recommended)"
    )
    ap.add_argument(
        "--digests",
        type=Path,
        default=Path(__file__).with_name("harbor_image_digests.json"),
        help="pinned task image digests (same file as the Harbor adapter's image_digests.json)",
    )
    ap.add_argument("--pins", default="litellm==1.108.3 mcp==2.3.0")
    ap.add_argument("--ignore-cost", action="store_true")
    ap.add_argument("--max-seconds", type=int, default=4 * 3600)
    args = ap.parse_args()

    pairs = json.loads(args.pairs.read_text())
    shards = shard_pairs(pairs, args.shards)
    args.out.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    threads = []
    for k, sp in enumerate(shards):
        t = threading.Thread(
            target=run_shard, args=(args, k, sp, args.out, lock), daemon=False
        )
        t.start()
        threads.append(t)
        time.sleep(5)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
