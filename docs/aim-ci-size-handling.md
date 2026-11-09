# Project size handling

AIM Flow does not reject reviewed project source because of a fixed source-file byte limit, selected-file count, total source byte limit, request byte limit, patch replacement size/count, or default changed-line ceiling. Project discovery also reads complete committed definitions, selected documents, workflow files and repository inventories instead of truncating them.

Selected content is read to the size measured from its opened regular-file descriptor or pinned Git object. Length, identity, ownership where required, modes, links, hashes, exact path selection, source freshness and consent checks still apply. An explicitly selected changed-line policy remains enforceable; registered defaults do not impose one.

Hash-pinned local tasks, project plans, receipts and source-containing retained records are also read completely. Large-input regressions generate representative content locally. Customer inputs belong only in private verification evidence, not release fixtures.

## Model requests and money

A no-key preview does not know the token count or final cost. Its byte-derived cost estimate is informational and cannot reject a source file. Generation requires token preflight using the same model, input, instructions and output schema.

A new project grant holds its already approved operation cap before credentials or preflight are used. The actual input count and selected output allowance must fit that hold before generation. Unknown outcomes keep their reservation and are not retried automatically. Existing ledger prefixes, financial ceilings and approved call slots are not expanded by these changes.

The default output allowance is 8,192 tokens, including reasoning and formatting. It is not a universal product or provider maximum: the request-building API accepts an explicit output_tokens allowance, bound into the exact request hash and checked against approved money and actual provider constraints. This change does not add a CLI or profile selection for that parameter. Historical experiment grants retain their previously authorized financial reservation envelopes.

## Controls that remain

- Reviewed per-run artifact maximumBytes, container memory/process/time limits and network policy remain explicit operation constraints. Global artifact byte/count ceilings are not substituted for them.
- Selected source archive readback uses the reviewed inventory's exact paths, sizes, modes and hashes to derive its resource envelope. Legacy plans without that inventory retain a bounded unreviewed archive fallback.
- Untrusted archive expansion, dependency downloads, protocol responses, streamed build reports and logs retain resource protections. These protect the computer or protocol parser, rather than defining supported CSS size.
- Supported path grammars, protected paths, parser-qualified names/versions, Git identity/version requirements and UTF-8 requirements remain separate format and authorization checks. Local Git and subprocess operations still have deadlines.

The removed 50KB formatter output limit also acted as an RLIMIT_FSIZE disk-write guard. Formatter stdout and stderr no longer have that fixed file-size guard. The locked formatter still runs with its sandbox, protected input paths, frozen-source checks and 180-second deadline, but independent formatter disk-space enforcement is unqualified. This is not a claim that every previous resource protection is unchanged or that hostile formatter code is supported.

Artifact report transport derives its byte allowance from the reviewed artifact maximumBytes plus a separate protocol metadata/log envelope. Base64 encoding of approved artifact content does not consume an unrelated fixed total-report cap. The metadata envelope can still reject an exceptionally large inventory, including many zero-byte entries; it remains a protocol resource defense. JavaScript-backed byte quantities must be exactly representable safe integers.
