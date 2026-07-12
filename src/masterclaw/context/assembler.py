from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from masterclaw.context.manifests import ContextManifest


class ContextAssemblyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssembledContext:
    text: str
    fragment_versions: tuple[tuple[str, int], ...]
    estimated_tokens: int


class ContextAssembler:
    def __init__(self, prompt_root: str | Path) -> None:
        self.root = Path(prompt_root).resolve()
        manifest_path = self.root / "manifest.json"
        self._registry = json.loads(manifest_path.read_text(encoding="utf-8"))["fragments"]

    def assemble(
        self, manifest: ContextManifest, projections: Mapping[str, object]
    ) -> AssembledContext:
        missing = set(manifest.state_projections) - projections.keys()
        if missing:
            raise ContextAssemblyError(f"missing state projections: {sorted(missing)}")

        sections: list[str] = []
        versions: list[tuple[str, int]] = []
        for fragment_id in manifest.rule_fragments:
            try:
                metadata = self._registry[fragment_id]
            except KeyError as error:
                raise ContextAssemblyError(f"unknown prompt fragment: {fragment_id}") from error
            path = (self.root / metadata["path"]).resolve()
            if self.root not in path.parents:
                raise ContextAssemblyError(f"fragment escapes prompt root: {fragment_id}")
            sections.append(f"## RULE {fragment_id}\n{path.read_text(encoding='utf-8').strip()}")
            versions.append((fragment_id, int(metadata["version"])))

        for projection_id in manifest.state_projections:
            payload = json.dumps(
                projections[projection_id],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            sections.append(f"## STATE {projection_id}\n{payload}")

        text = "\n\n".join(sections)
        estimated_tokens = max(1, (len(text) + 3) // 4)
        if estimated_tokens > manifest.input_token_budget:
            raise ContextAssemblyError(
                f"context budget exceeded: {estimated_tokens} > {manifest.input_token_budget}"
            )
        return AssembledContext(text, tuple(versions), estimated_tokens)
