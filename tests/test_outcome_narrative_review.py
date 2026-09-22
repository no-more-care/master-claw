import ast
import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest
from test_consequence_flow import Completion, setup

from masterclaw.app.action_service import ActionService
from masterclaw.app.decision_checkpoints import decision_output_type_name, run_checkpointed_decision
from masterclaw.app.i18n import tr
from masterclaw.app.legacy_outcome_narrative_review import (
    LegacyAlwaysReviewDecider,
    LegacyNarrativeDraftGenerator,
    LegacyNarrativeTextEditor,
)
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.outcome_narrative_review import (
    NarrativeReviewAssessment,
    NarrativeReviewReason,
    NarrativeReviewVerdict,
    OutcomeNarrativePipeline,
)
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.mechanics import PoolProposal
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.narrative import (
    NarrativeResult,
    create_narrative_editor_pipeline,
    create_narrative_pipeline,
)
from masterclaw.pipelines.state_decision import StateDecisionRouter

ROOT = Path(__file__).parents[1]
SOURCE = AssembledContext(
    "Static public rules",
    '## STATE roll_result\n{"hits":0,"difficulty":2}\n\n'
    '## STATE current_scene\n{"facts":["Дверь закрыта"]}',
    (),
    20,
)
REVIEW_TASK = (
    "Return publication-ready prose. Preserve every immutable mechanical outcome and "
    "established fact. Fix only contradictions, accidental state invention, viewpoint "
    "violations, internal terminology, and weak or confusing phrasing."
)


class RecordingPipeline:
    output_type = NarrativeResult

    def __init__(self, label, calls, *, error=None, hook=None):
        self.label, self.calls, self.error, self.hook = label, calls, error, hook

    async def run(self, **kwargs):
        self.calls.append((self.label, kwargs))
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        return NarrativeResult(narrative=self.label)


class Decider(LegacyAlwaysReviewDecider):
    def __init__(self, calls):
        self.calls = calls

    async def assess(self, snapshot):
        self.calls.append(("assess", snapshot))
        return await super().assess(snapshot)


def facade(calls, *, assembler=None, primary_error=None, fallback_error=None, editor_hook=None):
    return OutcomeNarrativePipeline(
        draft=LegacyNarrativeDraftGenerator(RecordingPipeline("raw draft", calls)),
        decider=Decider(calls),
        editor=LegacyNarrativeTextEditor(
            context=assembler or ContextAssembler(ROOT / "prompts"),
            reviewer=RecordingPipeline(
                "edited prose", calls, error=primary_error, hook=editor_hook
            ),
            reviewer_fallback=RecordingPipeline("fallback prose", calls, error=fallback_error),
        ),
    )


@pytest.mark.parametrize("fallback", [False, True])
def test_exact_order_review_context_task_and_fallback(fallback):
    calls = []
    assembler = ContextAssembler(ROOT / "prompts")
    pipeline = facade(
        calls, assembler=assembler, primary_error=RuntimeError("retry") if fallback else None
    )
    result = asyncio.run(pipeline.run(task="Narrate exactly", context=SOURCE))
    assert [label for label, _ in calls] == ["raw draft", "assess", "edited prose"] + (
        ["fallback prose"] if fallback else []
    )
    assert calls[0][1] == {"task": "Narrate exactly", "context": SOURCE}
    expected = assembler.assemble(
        manifest_for(PipelineName.OUTCOME_NARRATION_REVIEW),
        {
            "immutable_roll_result": {"hits": 0, "difficulty": 2},
            "source_context": SOURCE.dynamic_context,
            "raw_narrative": "raw draft",
        },
    )
    assert calls[2][1] == {"task": REVIEW_TASK, "context": expected}
    if fallback:
        assert calls[3][1] == calls[2][1]
    assert result.narrative == ("fallback prose" if fallback else "edited prose")
    snapshot = calls[1][1]
    detached = snapshot.immutable_outcome
    detached["hits"] = 99
    assert snapshot.immutable_outcome == {"hits": 0, "difficulty": 2}
    with pytest.raises(FrozenInstanceError):
        snapshot.raw_narrative = "replacement"
    assessment = asyncio.run(LegacyAlwaysReviewDecider().assess(snapshot))
    assert assessment == NarrativeReviewAssessment(
        NarrativeReviewVerdict.REPAIR, NarrativeReviewReason.LEGACY_ALWAYS_REVIEW
    )
    assert {field.name for field in fields(assessment)} == {"verdict", "reason"}


def test_low_level_narrator_editor_system_and_complete_call_bytes():
    class Capture:
        def __init__(self, text):
            self.text, self.calls = text, []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return CompletionResult(json.dumps({"narrative": self.text}), used_tool=True)

    narrator, primary, fallback = Capture("Raw prose"), Capture("Final prose"), Capture("Unused")
    draft = create_narrative_pipeline(narrator)
    editor = create_narrative_editor_pipeline(primary)
    assert hashlib.sha256(draft._static_system.encode()).hexdigest() == (
        "ac38c5a3cd7651ec5be3031f32679412707f15fc8dc2533a26ceabd0fa418ab2"
    )
    assert hashlib.sha256(editor._static_system.encode()).hexdigest() == (
        "8c91602226724b27a8aff6ecbe0fe58088c6f45d59c521fc53e5bcac7e15bcfd"
    )
    assembler = ContextAssembler(ROOT / "prompts")
    result = asyncio.run(
        OutcomeNarrativePipeline(
            draft=LegacyNarrativeDraftGenerator(draft),
            decider=LegacyAlwaysReviewDecider(),
            editor=LegacyNarrativeTextEditor(
                context=assembler,
                reviewer=editor,
                reviewer_fallback=create_narrative_editor_pipeline(fallback),
            ),
        ).run(task="Narrate", context=SOURCE)
    )
    expected_context = assembler.assemble(
        manifest_for(PipelineName.OUTCOME_NARRATION_REVIEW),
        {
            "immutable_roll_result": {"hits": 0, "difficulty": 2},
            "source_context": SOURCE.dynamic_context,
            "raw_narrative": "Raw prose",
        },
    )
    # Direct legacy low-level invocations with the exact old task/projections.
    asyncio.run(draft.run(task="Narrate", context=SOURCE))
    asyncio.run(editor.run(task=REVIEW_TASK, context=expected_context))
    assert narrator.calls[0] == narrator.calls[1]
    assert primary.calls[0] == primary.calls[1]
    assert not fallback.calls and result.narrative == "Final prose"


@pytest.mark.parametrize(
    "kind", ["missing", "malformed", "nonobject", "assemble", "degraded", "both"]
)
def test_review_failures_never_return_raw_or_run_forbidden_editors(kind):
    calls = []

    class Context:
        def assemble(self, *args):
            if kind == "assemble":
                raise ValueError("assembly failure")
            return replace(SOURCE, degradations=("lost facts",) if kind == "degraded" else ())

    source = replace(
        SOURCE,
        dynamic_context={
            "missing": "{}",
            "malformed": "## STATE roll_result\ninvalid",
            "nonobject": "## STATE roll_result\n[]",
        }.get(kind, SOURCE.dynamic_context),
    )
    expected = {
        "missing": "narrative review lacks immutable roll context",
        "malformed": "narrative review lacks immutable roll context",
        "nonobject": "narrative review lacks immutable roll context",
        "assemble": "narrative review context is unavailable",
        "degraded": "narrative review context was degraded",
        "both": "narrative review is unavailable",
    }[kind]
    pipeline = facade(
        calls,
        assembler=Context(),
        primary_error=RuntimeError("primary"),
        fallback_error=RuntimeError("fallback"),
    )
    with pytest.raises(PipelineValidationError, match=f"^{expected}$"):
        asyncio.run(pipeline.run(task="Narrate", context=source))
    assert [label for label, _ in calls] == (
        ["raw draft", "assess", "edited prose", "fallback prose"]
        if kind == "both"
        else ["raw draft"]
        if kind in {"missing", "malformed", "nonobject"}
        else ["raw draft", "assess"]
    )


def test_cancellation_does_not_run_fallback():
    calls = []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            facade(calls, primary_error=asyncio.CancelledError()).run(
                task="Narrate", context=SOURCE
            )
        )
    assert [label for label, _ in calls] == ["raw draft", "assess", "edited prose"]


def test_legacy_null_outer_checkpoint_replay_skips_all_ports(tmp_path):
    store = setup(tmp_path)
    calls = []
    pipeline = facade(calls)
    identity = decision_output_type_name(NarrativeResult)
    assert identity == "masterclaw.pipelines.narrative.NarrativeResult:v1:4d85796a91601dec"
    store.checkpoint_decision(
        event_id="legacy",
        pipeline_key="outcome_narration",
        game_id="game",
        output_type=identity,
        payload={"narrative": "Old edited prose"},
    )
    result = asyncio.run(
        run_checkpointed_decision(
            store=store,
            event_id="legacy",
            pipeline_key="outcome_narration",
            pipeline=pipeline,
            game_id="game",
            task="Narrate",
            context=SOURCE,
        )
    )
    assert result.narrative == "Old edited prose" and not calls


@pytest.mark.parametrize("kind", ["automatic", "roll"])
@pytest.mark.parametrize("guard", [None, "stale", "secret"])
def test_handler_outer_checkpoint_replay_payload_fingerprint_and_publication_guards(
    tmp_path, kind, guard
):
    store = setup(tmp_path)
    calls = []
    secret = "The bell keeper is the storm's forgotten name."
    if guard == "secret":
        store.update_world_content(
            world_id="world", expected_revision=0, content={"secret_plot": secret}
        )

    def mutate_scene():
        if guard == "stale":
            scene = store.scene_by_id(game_id="game", scene_id="room")
            store.apply_scene_patch(
                game_id="game",
                scene_id="room",
                expected_revision=scene["scene_revision"],
                causation_id="external:review",
                summary="A bell rings",
                add_facts=["A bell rings"],
                remove_facts=[],
            )

    pipeline = facade(calls, editor_hook=mutate_scene)
    if guard == "secret":
        pipeline._editor._reviewer.label = secret
    context = ContextAssembler(ROOT / "prompts")
    app = MessageApplication(
        store=store,
        context=context,
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,"evidence":"opens"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
                '"difficulty":null,"evidence":["unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion('{"summary":"The attempt resolves","add_facts":[],"remove_facts":[]}')
        ),
        narrative_pipeline=pipeline,
    )
    incoming = IncomingMessage.now(
        event_id=f"event-{kind}", channel_id="game", author_id="alice", content="I open it."
    )
    if kind == "roll":
        pending = ActionService(store).propose_roll(
            game_id="game",
            player_id="alice",
            scene_id="room",
            proposal=PoolProposal(trait_names=("T0",), aspect_names=("A0.0",), difficulty=2),
            prompt="Confirm",
            declaration="I open it.",
        )
        roll = ActionService(store).confirm_roll(
            interaction_id=pending.interaction_id,
            player_id="alice",
            confirmation_event_id=incoming.event_id,
            die=iter((1, 1)).__next__,
        )

        def run():
            return app._render_roll_outcome(message=incoming, roll=roll)

        key = f"outcome_narration:roll:{roll.roll_id}"
    else:

        def run():
            return app(incoming)

        key = "outcome_narration:automatic"
    first = asyncio.run(run())
    assert [label for label, _ in calls] == [
        "raw draft",
        "assess",
        secret if guard == "secret" else "edited prose",
    ]
    assert first.deliveries[0].content == (
        tr("ru", "narrative_fallback") if guard else "edited prose"
    )
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM decision_checkpoints WHERE pipeline_key LIKE 'outcome_narration%'"
        ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == incoming.event_id and row["pipeline_key"] == key
    assert row["output_type"] == decision_output_type_name(NarrativeResult)
    assert len(row["input_fingerprint"]) == 64
    assert json.loads(row["payload_json"]) == {
        "narrative": secret if guard == "secret" else "edited prose"
    }
    # Stale guards may skip replayed prose entirely, but never rerun any review stage.
    asyncio.run(run())
    assert len(calls) == 3


def test_contract_imports_are_not_handler_cli_or_classifier_coupled():
    for filename in ("outcome_narrative_review.py", "legacy_outcome_narrative_review.py"):
        tree = ast.parse((ROOT / "src/masterclaw/app" / filename).read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom)
            and any(part in node.module for part in ("handlers", ".cli", "classifiers", "adapters"))
            for node in ast.walk(tree)
        )
