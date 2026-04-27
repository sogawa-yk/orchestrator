"""Eval ループ runner (本実装)。

Langfuse v3+ の `run_experiment` API を使い、Dataset の各 item に対して:
  1. task 関数で Orchestrator Agent を実行 (trace は自動で Langfuse に作成される)
  2. evaluator で 3 種の Judge を OCI Enterprise AI 経由で実行
  3. スコアは Langfuse 側で trace に紐付く

使い方:
  uv run python -m orchestrator.eval.runner --check-only
  uv run python -m orchestrator.eval.runner --upsert-dataset
  uv run python -m orchestrator.eval.runner --iter 1
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any

from agents import Runner
from openai import AsyncOpenAI

from ..agent import build_agent, build_context
from ..config import Settings, get_settings
from ..observability import init_otel
from ..observability.langfuse_setup import get_langfuse_client
from .dataset import DATASET_NAME, GOLDEN_ITEMS, upsert_to_langfuse
from .judges import ALL_JUDGES, JudgePrompt

logger = logging.getLogger(__name__)


def check_only() -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {
        "langfuse_host": s.langfuse_host,
        "langfuse_keys_set": bool(s.langfuse_public_key and s.langfuse_secret_key),
        "openai_base_url": s.openai_base_url,
        "openai_key_set": bool(s.openai_api_key),
        "model": s.orch_model,
        "registry_path": str(s.orch_agents_path),
        "dataset_name": DATASET_NAME,
        "golden_items": len(GOLDEN_ITEMS),
    }
    client = get_langfuse_client(s)
    if client is None:
        out["langfuse_client"] = "unavailable"
        return out
    out["langfuse_client"] = "ok"
    try:
        ds = client.get_dataset(DATASET_NAME)
        out["dataset_exists"] = True
        out["dataset_items_count"] = len(list(ds.items)) if hasattr(ds, "items") else None
    except Exception as e:  # noqa: BLE001
        out["dataset_exists"] = False
        out["dataset_lookup_error"] = str(e)[:200]
    return out


def _filter_test_hints(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dataset の `[テスト前提]` system メッセージは Agent には渡さない (人間用ヒント)。"""
    out: list[dict[str, str]] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system" and content.strip().startswith("[テスト前提]"):
            continue
        if role in ("user", "assistant"):
            out.append({"role": role, "content": content})
    return out


def _extract_tool_calls(input_list: list[Any]) -> list[dict[str, Any]]:
    """Runner.run の to_input_list から tool 呼出シーケンスを抜き出す。"""
    calls: list[dict[str, Any]] = []
    for item in input_list or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "function_call":
            calls.append({
                "name": item.get("name"),
                "arguments": item.get("arguments"),
            })
    return calls


def _build_task(settings: Settings):
    """Langfuse `run_experiment` 用の task 関数を構築する。"""
    ctx = build_context(settings)
    agent = build_agent(ctx)

    async def task(*, item: Any, **_kwargs: Any) -> dict[str, Any]:
        # item.input は {"messages": [...], "category": ..., "id": ...}
        try:
            input_payload = item.input if hasattr(item, "input") else item.get("input")
        except Exception:
            input_payload = {}
        messages = (input_payload or {}).get("messages") or []
        agent_input = _filter_test_hints(messages)
        if not agent_input:
            return {"final_output": "", "tool_calls": [], "error": "no usable input"}

        # 各 item でセッションを切り直すため context を都度新規生成
        per_item_ctx = build_context(settings)
        try:
            result = await Runner.run(
                starting_agent=agent, input=agent_input, context=per_item_ctx, max_turns=20
            )
            final_output = getattr(result, "final_output", "") or ""
            if not isinstance(final_output, str):
                final_output = str(final_output)
            full = result.to_input_list() if hasattr(result, "to_input_list") else []
            tool_calls = _extract_tool_calls(full)
            return {"final_output": final_output, "tool_calls": tool_calls}
        except Exception as e:  # noqa: BLE001
            logger.exception("task error")
            return {"final_output": "", "tool_calls": [], "error": f"{type(e).__name__}: {e}"}

    return task


def _build_evaluator(settings: Settings, judge: JudgePrompt):
    """Langfuse evaluator を 1 個生成する。OCI Enterprise AI を直接叩いて採点する。"""
    oai = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    async def evaluator(
        *,
        input: Any = None,
        output: Any = None,
        expected_output: Any = None,
        metadata: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        out = output or {}
        in_payload = input or {}
        msgs = (in_payload.get("messages") if isinstance(in_payload, dict) else None) or []
        tool_calls = out.get("tool_calls") if isinstance(out, dict) else []
        final_answer = out.get("final_output") if isinstance(out, dict) else str(out)

        user = judge.user_template.format(
            input_messages=json.dumps(msgs, ensure_ascii=False),
            trace_summary=json.dumps(tool_calls, ensure_ascii=False),
            expected=json.dumps(expected_output, ensure_ascii=False),
            final_answer=final_answer or "",
            metadata=json.dumps(metadata or {}, ensure_ascii=False),
        )
        try:
            resp = await oai.chat.completions.create(
                model=settings.orch_model,
                messages=[
                    {"role": "system", "content": judge.system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=60,
            )
            text = resp.choices[0].message.content or "{}"
            d = json.loads(text)
            score = float(d.get("score", 0))
            reason = str(d.get("reason", ""))[:500]
        except Exception as e:  # noqa: BLE001
            logger.exception("judge %s failed", judge.name)
            return {"name": judge.name, "value": 0.0, "comment": f"judge error: {e}"}

        # スコア範囲にクリップ
        lo, hi = judge.score_range
        score = max(lo, min(hi, score))
        return {"name": judge.name, "value": score, "comment": reason}

    return evaluator


def _aggregate(item_results: list[Any]) -> dict[str, dict[str, Any]]:
    by_judge: dict[str, list[tuple[str, float, str]]] = {}
    for ir in item_results:
        evals = getattr(ir, "evaluations", []) or []
        item_id = "?"
        try:
            item_obj = getattr(ir, "item", None)
            if item_obj is not None:
                inp = getattr(item_obj, "input", None) or (item_obj.get("input") if isinstance(item_obj, dict) else None)
                if isinstance(inp, dict):
                    item_id = str(inp.get("id") or "?")
        except Exception:  # noqa: BLE001
            pass
        for e in evals:
            name = e.get("name") if isinstance(e, dict) else getattr(e, "name", "?")
            value = e.get("value") if isinstance(e, dict) else getattr(e, "value", None)
            comment = e.get("comment") if isinstance(e, dict) else getattr(e, "comment", "") or ""
            try:
                v = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                v = 0.0
            by_judge.setdefault(name, []).append((item_id, v, comment))

    summary: dict[str, dict[str, Any]] = {}
    for name, vals in by_judge.items():
        scores = [v[1] for v in vals]
        if not scores:
            continue
        summary[name] = {
            "n": len(scores),
            "avg": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores),
            "items": vals,
        }
    return summary


def _write_report(out_dir: Path, iter_num: int, summary: dict[str, dict[str, Any]], dataset_run_url: str | None) -> Path:
    today = _dt.date.today().isoformat()
    lines: list[str] = [f"# Eval Iteration {iter_num} ({today})\n"]
    if dataset_run_url:
        lines.append(f"Langfuse run: {dataset_run_url}\n")

    lines.append("## サマリ\n")
    lines.append("| Judge | 件数 | 平均 | min | max |")
    lines.append("|---|---|---|---|---|")
    for name, s in summary.items():
        lines.append(f"| {name} | {s['n']} | {s['avg']:.2f} | {s['min']:.2f} | {s['max']:.2f} |")

    lines.append("\n## 詳細\n")
    for name, s in summary.items():
        lines.append(f"### {name}\n")
        lines.append("| item_id | score | comment |")
        lines.append("|---|---|---|")
        for item_id, v, comment in s["items"]:
            comment_short = (comment or "").replace("|", "\\|").replace("\n", " ")[:140]
            lines.append(f"| {item_id} | {v} | {comment_short} |")
        lines.append("")

    lines.append("## 改善メモ (要編集)\n")
    lines.append("- 低スコア項目を見て、`prompts/system.ja.md` / `tool_descriptions.yaml` / `few_shots.yaml` を更新する")
    lines.append("- 次の iteration で同じ Dataset を再実行しスコアの変化を比較\n")

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


async def _run_iter_async(iter_num: int) -> dict[str, Any]:
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return {"ok": False, "reason": "Langfuse keys 未設定"}
    if not settings.openai_api_key:
        return {"ok": False, "reason": "OPENAI_API_KEY 未設定"}

    # OTel と Langfuse openai-wrapper を起動 (LLM 呼出が trace される)
    init_otel(settings)

    client = get_langfuse_client(settings)
    if client is None:
        return {"ok": False, "reason": "Langfuse client unavailable"}

    try:
        dataset = client.get_dataset(DATASET_NAME)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"dataset 取得失敗: {e}"}

    items = list(dataset.items)
    if not items:
        return {"ok": False, "reason": "dataset に item がない (--upsert-dataset を先に)"}

    task = _build_task(settings)
    evaluators = [_build_evaluator(settings, j) for j in ALL_JUDGES]

    run_name = f"iter{iter_num}-{_dt.datetime.now().strftime('%Y%m%dT%H%M%S')}"
    logger.info("starting experiment name=%s items=%d", run_name, len(items))

    result = client.run_experiment(
        name=f"orchestrator-iter{iter_num}",
        run_name=run_name,
        description=f"Orchestrator P3 改善ループ iter {iter_num}",
        data=items,
        task=task,
        evaluators=evaluators,
        max_concurrency=3,  # OCI 連打を避ける
    )
    # client.flush() を念の為呼ぶ
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        pass

    # 集計
    item_results = list(getattr(result, "item_results", []) or [])
    summary = _aggregate(item_results)

    today = _dt.date.today().isoformat()
    out_dir = Path("eval/runs") / f"{today}-iter{iter_num}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = _write_report(
        out_dir,
        iter_num,
        summary,
        getattr(result, "dataset_run_url", None),
    )

    return {
        "ok": True,
        "iter": iter_num,
        "report": str(report),
        "items": len(item_results),
        "dataset_run_url": getattr(result, "dataset_run_url", None),
        "summary": {k: {"n": v["n"], "avg": v["avg"]} for k, v in summary.items()},
    }


def run_iteration(iter_num: int) -> dict[str, Any]:
    return asyncio.run(_run_iter_async(iter_num))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Orchestrator eval runner")
    p.add_argument("--check-only", action="store_true", help="依存の状態確認のみ")
    p.add_argument("--upsert-dataset", action="store_true", help="Dataset を upsert")
    p.add_argument("--iter", type=int, default=0, help="改善ループの周回番号")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.check_only:
        print(json.dumps(check_only(), ensure_ascii=False, indent=2))
        return 0
    if args.upsert_dataset:
        print(json.dumps(upsert_to_langfuse(), ensure_ascii=False, indent=2))
        return 0
    if args.iter > 0:
        print(json.dumps(run_iteration(args.iter), ensure_ascii=False, indent=2))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
