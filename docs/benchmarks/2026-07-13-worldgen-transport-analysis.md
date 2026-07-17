# Worldgen model/transport benchmark analysis

Source: [`2026-07-13-worldgen-transport-benchmark.json`](2026-07-13-worldgen-transport-benchmark.json).
The run executed six models against four worldgen stages on both output transports: 48 scenario
attempts, 49 provider calls, 24m 32s aggregate measured latency and $0.288119 provider cost.

## Mechanical results

| Model | Prompt JSON | Native tool | Prompt time | Prompt cost | Prompt repairs |
|---|---:|---:|---:|---:|---:|
| Aion 3.0 | 3/4 | 2/4 | 145.3s | $0.050898 | 1 |
| Aion 3.0 Mini | 3/4 | 3/4 | 175.2s | $0.014289 | 0 |
| DeepSeek V4 Pro | 2/4 | 3/4 | 203.1s | $0.040583 | 1 |
| Gemini 3 Flash Preview | 4/4 | 2/4 | 41.1s | $0.016235 | 0 |
| GPT-5.6 Luna | 4/4 | 3/4 | 22.1s | $0.016315 | 0 |
| Qwen 3.7 Plus | 4/4 | 0/4 | 285.2s | $0.014585 | 0 |

`prompt_json` is the correct default for this role. Qwen's provider rejected every native request
because thinking mode does not support forced `tool_choice`. Gemini returned `null` nested objects on
two native scenarios even after repair. Aion 3.0 exceeded the 4000-character secret limit after a
native repair. Luna's native failure was semantic rather than a malformed tool call.

## Human audit of prompt-JSON output

### Aion 3.0

The richest campaign scaffold in this sample. It produced a coherent city economy, seven motivated
factions, ten useful locations, several simultaneous crises, concrete clues and multiple costly
resolution paths. Its best quality is systemic causality: pumps, permits, light, salvage, food and
historical control reinforce one another instead of appearing as independent grimdark decorations.

The weaknesses are verbosity and contract discipline. It expanded faction names into descriptive
paragraphs, so exact identity validation failed, and its secret needed repair. It is a strong
creative-outline candidate, but not yet a safe single-model owner of all four stages.

### Aion 3.0 Mini

Broad and productive, but less selective. It generated ten locations, eight factions and twelve
tensions, including a fairly generic `Deep-Cult` despite the prompt's warning against interchangeable
cults. In the public stage it invented three extra locations and factions instead of expanding only
the approved outline. The secret was usable, but more conventional than full Aion's.

It offers no clear quality/latency advantage over the leading models in this run.

### DeepSeek V4 Pro

The strongest atmospheric grimdark voice. Distinctive ideas included addictive memory-echo currency,
salt-echoes extracted from pre-Fall remains, a slow groundwater blight and factions whose survival
functions conflict with their politics. Its darkness felt culturally embedded rather than added as
gore.

Operationally it was unreliable: it expanded two approved locations into eight and two factions
into seven, then returned an empty secret response after repair in prompt-JSON mode. Native tool mode
improved schema completion to 3/4 but was the slowest native candidate and still failed identity
preservation. DeepSeek merits another creative-stage test, not production routing of the complete
workflow yet.

### Gemini 3 Flash Preview

Fast, structurally reliable on prompt JSON, and the most conceptually surprising secret: the surface
as a privatized domain, atmospheric debt as an external lease, and the city as a collection mechanism.
This creates clear clues and two campaign-scale escalation paths. It can, however, drift from flooded
underground fantasy into corporate science fiction; that is valuable when requested and a tone risk
otherwise. Its outline was compact rather than deeply textured.

Gemini is a strong finalist if genre adherence is tested more explicitly.

### GPT-5.6 Luna

The best operational baseline: 4/4 without repair, lowest latency, and almost the same cost as Gemini.
It preserved ids and factions exactly, wrote concise public locations, provided discoverable clues,
and kept every secret resolution costly. The output was coherent and immediately playable.

Its ideas were less linguistically distinctive than DeepSeek and less surprising than Gemini, but
they were better edited. Luna currently has the strongest balance of quality, speed and contract
discipline.

### Qwen 3.7 Plus

Prompt JSON completed 4/4 at the lowest successful cost, but took 285 seconds and emitted 8.8K
reasoning tokens. The outline was competent yet more generic: vertical class tiers, Hydro-Barons,
a sabotage cult and a failing pump. The secret had clear choices but fewer layered clues and faction
contradictions than Luna/Aion. Native tool calling is unusable with the tested thinking-mode provider
path.

Given earlier latency concerns, this run does not justify restoring Qwen to production routing.

## Schema finding

The faction-preservation failures expose a model-contract issue as well as model behavior.
`WorldPublicSections.factions` is `list[str]`, so a model cannot add a faction description without
changing the identity string. Full Aion mostly enriched the two approved factions; Mini and DeepSeek
also invented genuinely new identities and locations. A future schema should give factions stable
ids plus separate names/descriptions, matching the existing location structure. Deterministic checks
should compare ids, not prose strings.

## Recommendation

1. Keep `prompt_json` for worldgen.
2. Keep Luna as the production baseline for the complete four-stage workflow.
3. Run a three-repeat finalist round for Luna, Gemini, full Aion and DeepSeek, but split evaluation
   by stage: creative outline/secret quality versus structural public/critic reliability.
4. Exclude Aion Mini and Qwen from the finalist round unless a new scenario reveals a specific
   advantage.
5. Fix faction identity/description structure before treating public-section failures as a final
   model ranking.
