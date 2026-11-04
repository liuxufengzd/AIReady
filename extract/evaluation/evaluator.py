import asyncio
import json
import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

import schemas
from prompts import SYSTEM_PROMPT
from schemas import Flag

logger = logging.getLogger(__name__)

GROUND_TRUTH_FOLDER = Path(__file__).parent / "ground_truth"
OUTPUT_FOLDER = Path(__file__).parent / "output"
MAX_CONCURRENT_JUDGES = 10
MAX_SCORE = 5
MIN_SCORE = 1


class JudgeResult(BaseModel):
    score: int = Field(
        ge=MIN_SCORE, le=MAX_SCORE, description="The score of the evaluation"
    )
    reason: str = Field(description="Brief explanation of giving the score")


@dataclass
class FieldSpec:
    name: str
    llm_as_a_judge: bool = False
    children: list["FieldSpec"] | None = None


@dataclass
class Comparison:
    field: str
    expected_empty: bool
    actual_empty: bool
    correct: bool


def _resolve_schema(name: str) -> type[BaseModel]:
    schema_cls = getattr(schemas, name, None)
    if not (isinstance(schema_cls, type) and issubclass(schema_cls, BaseModel)):
        raise ValueError(f"Unknown schema: {name}")
    return schema_cls


def _flag_in(metadata) -> bool:
    return any(
        (m is Flag or isinstance(m, Flag)) and m.llm_as_a_judge for m in metadata
    )


def _annotation_has_flag(annotation) -> bool:
    if get_origin(annotation) is Annotated:
        _, *metadata = get_args(annotation)
        if _flag_in(metadata):
            return True
    return any(_annotation_has_flag(arg) for arg in get_args(annotation))


def _list_item_model(annotation) -> type[BaseModel] | None:
    if get_origin(annotation) is list:
        (item,) = get_args(annotation)
        if isinstance(item, type) and issubclass(item, BaseModel):
            return item
    return None


def _build_specs(model: type[BaseModel]) -> list[FieldSpec]:
    specs = []
    for name, info in model.model_fields.items():
        alias = info.alias or name
        item_model = _list_item_model(info.annotation)
        if item_model is not None:
            specs.append(FieldSpec(alias, children=_build_specs(item_model)))
        else:
            judge = _flag_in(info.metadata) or _annotation_has_flag(info.annotation)
            specs.append(FieldSpec(alias, llm_as_a_judge=judge))
    return specs


def _flatten_names(specs: list[FieldSpec], prefix: str = "") -> list[str]:
    names = []
    for spec in specs:
        if spec.children is not None:
            names.extend(_flatten_names(spec.children, f"{prefix}{spec.name}."))
        else:
            names.append(f"{prefix}{spec.name}")
    return names


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return " ".join(text.split())


def _safe_div(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None and recall is None:
        return None
    precision, recall = precision or 0.0, recall or 0.0
    return _safe_div(2 * precision * recall, precision + recall) or 0.0


def _metrics(field: str, comparisons: list[Comparison]) -> dict:
    tp = fp = fn = omissions = hallucinations = 0
    for c in comparisons:
        if c.expected_empty and c.actual_empty:
            continue
        if c.expected_empty:
            fp += 1
            hallucinations += 1
        elif c.actual_empty:
            fn += 1
            omissions += 1
        elif c.correct:
            tp += 1
        else:
            fp += 1
            fn += 1

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    expected_count = sum(not c.expected_empty for c in comparisons)
    actual_count = sum(not c.actual_empty for c in comparisons)
    return {
        "field": field,
        "precision": precision,
        "recall": recall,
        "f1_score": _f1(precision, recall),
        "accuracy": _safe_div(sum(c.correct for c in comparisons), len(comparisons)),
        "omission_rate": _safe_div(omissions, expected_count),
        "hallucination_rate": _safe_div(hallucinations, actual_count),
    }


class Evaluator:
    def __init__(self, configuration: dict):
        test = configuration["test"]
        self.cases = test["cases"]

        judge = test["llm_as_a_judge"]
        self.threshold_score = judge["threshold_score"]
        self.judge_llm = ChatGoogleGenerativeAI(
            model=judge["model"]
        ).with_structured_output(JudgeResult)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JUDGES)

    async def evaluate(self) -> None:
        ground_truths = sorted(GROUND_TRUTH_FOLDER.glob("*.json"))
        if not ground_truths:
            logger.warning("No ground truth files in %s", GROUND_TRUTH_FOLDER)
            return

        for case in self.cases:
            await self._evaluate_case(case, ground_truths)

    async def _evaluate_case(self, case: dict, ground_truths: list[Path]) -> None:
        parser = case["parser"]
        specs = _build_specs(_resolve_schema(parser["schema"]))
        prediction_folder = Path(case["path"])
        output_folder = (
            OUTPUT_FOLDER / parser["type"] / parser["model"].replace("-", "_")
        )
        logger.info("Evaluating %s / %s", parser["type"], parser["model"])

        documents = []
        for ground_truth in ground_truths:
            prediction = prediction_folder / ground_truth.name
            if not prediction.exists():
                logger.warning("Missing prediction %s", prediction)
                continue
            documents.append((ground_truth, prediction))

        results = await asyncio.gather(
            *(self._evaluate_document(specs, gt, pred) for gt, pred in documents)
        )

        details = []
        by_field: dict[str, list[Comparison]] = defaultdict(list)
        for detail, comparisons in results:
            details.append(detail)
            for c in comparisons:
                by_field[c.field].append(c)

        metrics = [
            _metrics(name, by_field[name])
            for name in _flatten_names(specs)
            if by_field[name]
        ]
        metrics.append(_metrics("overall", [c for cs in by_field.values() for c in cs]))

        output_folder.mkdir(parents=True, exist_ok=True)
        for file_name, data in (("details.json", details), ("metrics.json", metrics)):
            (output_folder / file_name).write_text(
                json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
            )
        logger.info("Saved results to %s", output_folder)

    async def _evaluate_document(
        self, specs: list[FieldSpec], ground_truth: Path, prediction: Path
    ) -> tuple[dict, list[Comparison]]:
        logger.info("Comparing %s", ground_truth.stem)
        expected = json.loads(ground_truth.read_text(encoding="utf-8"))
        actual = json.loads(prediction.read_text(encoding="utf-8"))

        comparisons: list[Comparison] = []
        detail = await self._compare_object(specs, expected, actual, "", comparisons)
        return {"id": ground_truth.stem, **detail}, comparisons

    async def _compare_object(
        self,
        specs: list[FieldSpec],
        expected: dict,
        actual: dict,
        prefix: str,
        comparisons: list[Comparison],
    ) -> dict:
        async def compare(spec: FieldSpec):
            path = f"{prefix}{spec.name}"
            expected_value = expected.get(spec.name)
            actual_value = actual.get(spec.name)
            if spec.children is None:
                return await self._compare_value(
                    spec, expected_value, actual_value, path, comparisons
                )

            expected_items = expected_value or []
            actual_items = actual_value or []
            count = max(len(expected_items), len(actual_items))
            return await asyncio.gather(
                *(
                    self._compare_object(
                        spec.children,
                        expected_items[i] if i < len(expected_items) else {},
                        actual_items[i] if i < len(actual_items) else {},
                        f"{path}.",
                        comparisons,
                    )
                    for i in range(count)
                )
            )

        values = await asyncio.gather(*(compare(spec) for spec in specs))
        return {spec.name: value for spec, value in zip(specs, values)}

    async def _compare_value(
        self,
        spec: FieldSpec,
        expected: Any,
        actual: Any,
        path: str,
        comparisons: list[Comparison],
    ) -> bool | dict:
        expected_text = _normalize(expected)
        actual_text = _normalize(actual)

        if spec.llm_as_a_judge:
            if expected_text == actual_text:
                judged = JudgeResult(score=MAX_SCORE, reason="")
            elif not expected_text:
                judged = JudgeResult(score=MIN_SCORE, reason="base is empty")
            elif not actual_text:
                judged = JudgeResult(score=MIN_SCORE, reason="test is empty")
            else:
                judged = await self._judge(str(expected), str(actual))
            correct = judged.score >= self.threshold_score
            result = {"score": judged.score}
            if judged.score < MAX_SCORE:
                result["reason"] = judged.reason
        else:
            result = correct = expected_text == actual_text

        comparisons.append(
            Comparison(path, not expected_text, not actual_text, correct)
        )
        return result

    async def _judge(self, base: str, test: str) -> JudgeResult:
        async with self._semaphore:
            result: JudgeResult = await self.judge_llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=f"<base>{base}</base>\n<test>{test}</test>"),
                ]
            )
        return result
