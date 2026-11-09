#!/usr/bin/env bash

# Review text is evidence, never an instruction or permission to spend.
# Only explicit priority labels classify inline defects automatically.
github_review_classify_item() {
  jq -c '
    . as $item |
    (.detail | split("\n") | map(select(test("\\S"))) | .[0] // "") as $title |
    ([.detail | match("\\bP[0-9]+\\b"; "g") | .string] | unique) as $priorities |
    if ($title | test("^([*#[:space:]]|<sub>)*(\\[P[0-9]+\\]|!\\[P[0-9]+ Badge\\]|P[0-9]+\\b)")) and
       ($priorities | length) == 1 and ($priorities[0] | test("^P[0-4]$")) then
      . + {classification:(if ($priorities[0] | IN("P0","P1","P2")) then "blocking" else "advisory" end),
           priority:$priorities[0],reason:"explicit-priority"}
    else . + {classification:"assessment",priority:null,reason:"unclassified-or-conflicting-priority"} end
  ' <<<"$1"
}

# The legacy numeric API remains useful for detecting a positive clean verdict.
# A non-clean summary is NOT, on its own, evidence of a blocking defect.
github_review_summary_item() {
  local kind="$1" body="$2" state="$3" inline_count="$4" verdict="$5" generated suppressed result='[]' item standard=false
  if [[ "$kind" == codex && "$body" == '<!-- codex-review-receipt:v1 -->'* ]]; then
    body=${body#'<!-- codex-review-receipt:v1 -->'}
  fi
  suppressed=$(sed -nE 's/^### Suppressed comments \(([1-9][0-9]*)\).*$/\1/p' <<<"$body" | awk '{sum+=$1} END {print sum+0}')
  generated=$(sed -nE 's/^- \*\*Comments generated:\*\* ([0-9]+)( new)?$/\1/p' <<<"$body" | tail -1)
  # Only known framing is discarded. A familiar header never hides extra prose,
  # contradictory priorities, or an additional concern in the summary.
  if jq -e -R -s --arg kind "$kind" '
    gsub("\\s+"; " ") | sub("^ +"; "") | sub(" +$"; "") |
    if $kind=="codex" then
      test("^### (💡 )?Codex Review( Here are some automated review suggestions for this pull request\\. \\*\\*Reviewed commit:\\*\\* `[0-9a-f]{10,40}`)?( <details> <summary>ℹ️ About Codex in GitHub</summary> <br/> \\[Your team has set up Codex to review pull requests in this repo\\]\\(https://chatgpt.com/codex/cloud/settings/general\\)\\. Reviews are triggered when you - Open a pull request for review - Mark a draft as ready - Comment \\\"@codex review\\\"\\. If Codex has suggestions, it will comment; otherwise it will react with 👍\\. Codex can also answer questions or update the PR\\. Try commenting \\\"@codex address that feedback\\\"\\. </details>)?$") or
      test("^(### )?(✅ |🟢 )?(Review completed|No changes requested|Approval recommended)[.!]?$") or
      .=="Codex Review: Didn\u0027t find any major issues."
    else
      test("^### (🟡 |✅ |🟢 )?(Changes recommended|Review completed|No changes requested|Approval recommended)( - \\*\\*Comments generated:\\*\\* [0-9]+( new)?)?$")
    end
  ' >/dev/null <<<"$body"; then standard=true; fi
  if [[ "$state" == CHANGES_REQUESTED ]]; then
    item=$(jq -nc --arg reviewer "$kind" --arg detail "${body:0:12000}" \
      '{source:"review-summary",reviewer:$reviewer,detail:$detail,classification:"blocking",priority:null,reason:"formal-changes-requested"}')
    result=$(jq -c --argjson item "$item" '.+[$item]' <<<"$result")
  fi
  if (( suppressed > 0 )) || [[ -n "$generated" && "$generated" -gt "$inline_count" ]]; then
    item=$(jq -nc --arg reviewer "$kind" --arg detail "${body:0:12000}" \
      '{source:"review-summary",reviewer:$reviewer,detail:$detail,classification:"assessment",priority:null,reason:"unavailable-findings"}')
    result=$(jq -c --argjson item "$item" '.+[$item]' <<<"$result")
  elif [[ "$state" != CHANGES_REQUESTED ]] && { [[ "$standard" != true ]] || (( inline_count == 0 && verdict > 0 )); }; then
    item=$(github_review_classify_item "$(jq -nc --arg reviewer "$kind" --arg detail "${body:0:12000}" \
      '{source:"review-summary",reviewer:$reviewer,detail:$detail}')")
    result=$(jq -c --argjson item "$item" '.+[$item]' <<<"$result")
  fi
  printf '%s\n' "$result"
}

# Legacy bundles retain their former blocking meaning. New policy bundles carry
# a complete partition, so unknown evidence cannot be mistaken for zero defects.
github_review_clean_comment() {
  local framed items
  framed=$(jq -e -R -s '
    capture("^Codex Review: Didn\u0027t find any major issues\\.( (Nice work!|Can\u0027t wait for the next one!|Already looking forward to the next diff\\.|More of your lovely PRs please\\.|:\\+1:|👍))?\\s+\\*\\*Reviewed commit:\\*\\* `(?<commit>[0-9a-f]{10,40})`(?<tail>.*)$"; "s") |
    "### 💡 Codex Review\n\nHere are some automated review suggestions for this pull request.\n\n**Reviewed commit:** `"+.commit+"`"+.tail
  ' <<<"$1") || return 1
  # Reuse the same complete boilerplate allowlist; appended concerns are data.
  framed=$(jq -r . <<<"$framed") || return 1
  items=$(github_review_summary_item codex "$framed" COMMENTED 1 1) || return 1
  [[ "$items" == '[]' ]]
}

github_review_bundle_status() {
  local bundle="$1"
  jq -r '
    if .ciConclusion=="pending" or
       ((.completedReviewers | index("codex"))==null) or
       ((.completedReviewers | index("copilot"))==null) then "pending"
    elif (.assessmentRequired // 0)>0 then "assessment_required"
    elif .ciConclusion!="success" or .findings>0 then "findings"
    else "ready" end
  ' <<<"$bundle"
}

github_review_bundle_actionable() {
  github_correction_bundle_valid "$1" &&
    jq -e '.reviewPolicyVersion=="1.0" and .findings>0 and .assessmentRequired==0' >/dev/null <<<"$1"
}
