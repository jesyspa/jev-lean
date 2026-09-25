import JevLean

open Lean Elab Tactic Meta
open JevLean.Search

private def controlledActions : ActionSource := fun _ => do
  return (← catalogue { maxRetrievedNames := 0 }).filter fun action =>
    action.text.startsWith "intro " || action.text == "constructor" ||
      (action.text.startsWith "exact " && !(action.text.drop 6).contains ' ') ||
      (action.text.startsWith "apply " && !(action.text.drop 6).contains ' ')

private def identityRanker : ActionRanker := fun _ actions => pure actions

private def retrievedActions : ActionSource := fun config =>
  return (← globalActions config.maxRetrievedNames).filter fun action =>
    action.text.startsWith "exact " || action.text.startsWith "apply "

private def retrievalThenLocalActions : ActionSource := fun config => do
  let retrieved ← globalActions config.maxRetrievedNames
  let h := mkIdent `h
  return retrieved.filter (·.text == "apply retrievalTarget_from") ++ [
    { tacticSyntax := ← `(tactic| exact $h), text := "exact h" }
  ]

def RetrievalTarget (n : Nat) : Prop := n = n

lemma retrievalTarget_global (n : Nat) : RetrievalTarget n := rfl

lemma retrievalTarget_zero : RetrievalTarget 0 := rfl

lemma retrievalTarget_from (n : Nat) (h : n = 0) : RetrievalTarget n := by
  subst n
  rfl

def EqualityTarget (n : Nat) : Prop := n = 0

def NormalizationTarget (p : Prop) : Prop := p ∧ True

@[simp] lemma normalizationTarget_normalize (p : Prop) : NormalizationTarget p = p := by
  simp [NormalizationTarget]

private def rewriteThenCloseActions : ActionSource := fun config => do
  return (← rewriteActions config) ++ [
    { tacticSyntax := ← `(tactic| rfl), text := "rfl" }
  ]

private def unfoldOnlyActions : ActionSource := fun config => unfoldActions config

namespace Doubled

def length_R (xs : List Nat) : Prop := True ∧ xs.length = xs.length

end Doubled

inductive TestStep where
  | halt
  | run

def isHalting (step : TestStep) : Prop := step = .halt

lemma stepHalt_of_isHalting (step : TestStep) (h : isHalting step) : step = .halt := by
  unfold isHalting at h
  exact h

def irrelevantDefinition : Prop := True

inductive ParseTree where
  | leaf
  | branch : ParseTree → ParseTree → ParseTree

def ParseTree.height : ParseTree → Nat
  | .leaf => 1
  | .branch left right => max left.height right.height + 1

lemma ParseTree.one_le_height (tree : ParseTree) : 1 ≤ tree.height := by
  cases tree <;> simp [height]

private def dataCaseActions : ActionSource := fun config => do
  let actions ← catalogue { config with maxRetrievedNames := 0 }
  return actions.filter (·.text == "cases tree") ++ [
    { tacticSyntax := ← `(tactic| simp [ParseTree.height]), text := "simp [ParseTree.height]" }
  ]

private def generalizedInductionActions : ActionSource := fun config => do
  let actions ← catalogue { config with maxRetrievedNames := 0 }
  return actions.filter (·.text == "induction xs generalizing acc") ++ [
    { tacticSyntax := ← `(tactic| simp [JevLean.totalFrom, List.sum, *, Nat.add_assoc]),
      text := "simp [JevLean.totalFrom, List.sum, *, Nat.add_assoc]" }
  ]

private def aesopFirst : ActionRanker := fun _ actions =>
  pure <| actions.mergeSort fun left right =>
    left.text.startsWith "aesop" && !right.text.startsWith "aesop"

private def reverseRanker : ActionRanker := fun _ actions => pure actions.reverse

private def budgetedActions : ActionSource := fun config => do
  let actions ← catalogue { config with maxRetrievedNames := 0 }
  let some closing := actions.find? (·.text == "exact h") |
    throwError "missing local closing action"
  return [
    { tacticSyntax := ← `(tactic| skip), text := "skip one" },
    { tacticSyntax := ← `(tactic| skip), text := "skip two" },
    closing
  ]

private def disjunctionActions : ActionSource := fun _ => do
  return (← catalogue { maxRetrievedNames := 0 }).filter fun action =>
    action.text.startsWith "intro " || action.text.startsWith "cases " ||
      action.text == "left" || action.text == "right" || action.text == "assumption"

private def localTransformationActions : ActionSource := fun config => do
  return (← catalogue { config with maxRetrievedNames := 0 }).filter fun action =>
    action.text == "exact (h z).symm" || action.text == "apply (h z).mp" ||
      action.text == "exact (h z).1" || action.text == "exact hp"

private def localApplicationText (text : String) : Bool :=
  text.startsWith "exact h " || text.startsWith "apply h " ||
    text.startsWith "exact (h " || text.startsWith "apply (h "

private def inductionActions : ActionSource := fun _ => do
  let xs := mkIdent `xs
  return [
    { tacticSyntax := ← `(tactic| induction $xs:ident), text := "induction xs" },
    { tacticSyntax := ← `(tactic| simp [JevLean.tally, *]), text := "simp [JevLean.tally, *]" },
    { tacticSyntax := ← `(tactic| omega), text := "omega" }
  ]

private def orderedSiblingActions : ActionSource := fun _ => do
  let hP := mkIdent `hP
  let hQ := mkIdent `hQ
  return [
    { tacticSyntax := ← `(tactic| constructor), text := "constructor" },
    { tacticSyntax := ← `(tactic| exact $hP), text := "exact hP" },
    { tacticSyntax := ← `(tactic| exact $hQ), text := "exact hQ" }
  ]

private def duplicateOutcomeActions : ActionSource := fun _ => do
  let hP := mkIdent `hP
  let hQ := mkIdent `hQ
  return [
    { tacticSyntax := ← `(tactic| constructor), text := "constructor", family := "split" },
    { tacticSyntax := ← `(tactic| apply And.intro), text := "apply And.intro", family := "apply" },
    { tacticSyntax := ← `(tactic| skip), text := "skip", family := "noop" },
    { tacticSyntax := ← `(tactic| exact $hP), text := "exact hP", family := "local" },
    { tacticSyntax := ← `(tactic| exact $hQ), text := "exact hQ", family := "local" }
  ]

elab "jev_test_helper_validation" : tactic => withMainContext do
  let cuts ← helperCutActions [("P", "available cut"), ("P → P", "unchanged"),
    ("not valid Lean (", "malformed")]
  unless cuts.length == 1 && cuts.head?.any (·.text.startsWith "helper cut (P)") do
    throwError "helper validation admitted malformed, circular, unchanged, or unavailable proposals: {cuts.map (·.text)}"
  let root ← initialNode
  let successors ← expand root cuts
  match successors with
  | [successor] => unless successor.goals.length == 2 do
      throwError "a helper cut did not create the helper proof and continuation obligations"
  | _ => throwError "a helper cut did not produce exactly one successor"
  evalTactic (← `(tactic| exact fun h => h))

elab "jev_test_helper_replay_source" : tactic => withMainContext do
  let cuts ← helperCutActions [("P", "available cut")]
  let [cut] := cuts | throwError "helper validation did not admit P"
  let assumption : Action := { tacticSyntax := ← `(tactic| assumption), text := "assumption" }
  let root ← initialNode
  let [afterCut] ← expand root [cut] |
    throwError "helper cut did not create a unique successor"
  afterCut.restore
  let [afterFirstAssumption] ← expand afterCut [assumption] |
    throwError "helper proof obligation did not close"
  afterFirstAssumption.restore
  let [closed] ← expand afterFirstAssumption [assumption] |
    throwError "helper continuation obligation did not close"
  unless closed.goals.isEmpty do
    throwError "helper path did not close"
  root.restore
  let suggestion ← replaySuggestion closed.path 2
  unless suggestion.startsWith "refine (let jev_h1 : P := ?_; ?_)" &&
      suggestion.contains "\n  · assumption\n  · assumption" do
    throwError "helper replay did not use fresh replayable source:\n{suggestion}"
  let source := "import JevLean\n\nexample (P Q : Prop) (hp : P) (hq : Q) : Q := by\n  have jev_h : P := hp\n  " ++ suggestion ++ "\n"
  let output ← IO.FS.withTempDir fun directory => do
    let file := directory / "Replay.lean"
    IO.FS.writeFile file source
    IO.Process.output {
      cmd := "lake"
      args := #["env", "lean", file.toString]
      cwd := some "."
    }
  unless output.exitCode == 0 do
    throwError "fresh Lean process rejected helper replay source:\n{output.stderr}"

elab "jev_test_restoration" : tactic => withMainContext do
  let root ← initialNode
  let _ ← expand root [{ tacticSyntax := ← `(tactic| skip), text := "skip" }]
  evalTactic (← `(tactic| assumption))

elab "jev_test_structural_path" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 4, maxCost := 4 } controlledActions identityRanker
    | throwError "controlled search found no path"
  let texts := node.path.map (·.text)
  unless texts == ["intro jev_h", "constructor", "exact jev_h", "exact jev_h"] do
    throwError "unexpected controlled path: {texts}"
  unless node.goals.isEmpty && node.depth == 4 && node.cost == 4 do
    throwError "closing node did not retain its goals, depth, and cost"
  replay node.path

elab "jev_test_apply_path" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 2, maxCost := 2 } controlledActions identityRanker
    | throwError "controlled apply search found no path"
  let texts := node.path.map (·.text)
  unless texts == ["apply h", "exact hp"] do
    throwError "unexpected apply path: {texts}"
  replay node.path

elab "jev_test_global_retrieval" : tactic => withMainContext do
  let config := { maxRetrievedNames := 64 }
  let actions ← retrievedActions config
  let repeated ← retrievedActions config
  unless actions.map (·.text) == repeated.map (·.text) && actions.length <= 2 * config.maxRetrievedNames do
    throwError "global retrieval exceeded its bound or changed order"
  let root ← initialNode
  unless (← expand root actions).length == actions.length do
    throwError "global retrieval admitted a candidate that does not elaborate in the focused state"
  unless actions.any fun action => action.text == "exact retrievalTarget_zero" do
    throwError "type-correct global exact candidate was not retrieved"
  unless actions.all fun action => action.text != "exact definitely_missing_lemma" do
    throwError "retrieval invented an unavailable name"
  let some node ← searchWith { maxDepth := 1, maxCost := 1, maxRetrievedNames := 64 }
      retrievedActions identityRanker
    | throwError "retrieval search found no path"
  unless node.path.map (·.text) == ["exact retrievalTarget_zero"] do
    throwError "retrieval did not select the global exact candidate"
  replay node.path

elab "jev_test_global_apply_retrieval" : tactic => withMainContext do
  let actions ← retrievedActions { maxRetrievedNames := 64 }
  unless actions.any fun action => action.text == "apply retrievalTarget_from" do
    throwError "type-correct global apply candidate was not retrieved"
  let some node ← searchWith { maxDepth := 2, maxCost := 2, maxRetrievedNames := 64 }
      retrievalThenLocalActions identityRanker
    | throwError "retrieval apply search found no path"
  unless node.path.map (·.text) == ["apply retrievalTarget_from", "exact h"] do
    throwError "retrieval did not retain a type-correct global apply candidate"
  replay node.path

elab "jev_test_rewrite_acceptance" : tactic => withMainContext do
  let config := {
    maxDepth := 2, maxCost := 2, maxRewriteCandidates := 2,
    maxRewriteSimpNames := 100_000, maxRewriteMs := 10_000
  }
  let actions ← rewriteActions config
  unless actions.map (·.text) == ["rw [h]", "rw [← h]"] do
    throwError "acceptance equality did not produce deterministic rewrites: {actions.map (·.text)}"
  let some node ← searchWith config rewriteThenCloseActions identityRanker
    | throwError "acceptance rewrite search found no path"
  unless node.path.map (·.text) == ["rw [h]"] do
    throwError "acceptance rewrite path is not replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_rewrite_closure" : tactic => withMainContext do
  let config := {
    maxDepth := 3, maxCost := 3, maxWallMs := 120_000, maxRewriteCandidates := 12,
    maxRewriteSimpLemmas := 4, maxRewriteSimpNames := 100_000, maxRewriteMs := 120_000
  }
  let actions ← rewriteActions config
  unless actions.any fun action => action.text == "rw [h]" do
    throwError "closure equality did not produce a forward rewrite"
  unless actions.any fun action => action.text == "simp only [normalizationTarget_normalize]" do
    throwError "closure normalization did not produce a matched simp-only action: {actions.map (·.text)}"
  let some node ← searchWith config rewriteThenCloseActions identityRanker
    | throwError "closure rewrite search found no path"
  unless node.path.map (·.text) ==
      ["rw [h]", "simp only [normalizationTarget_normalize]"] do
    throwError "closure normalization path is not replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_local_symmetry" : tactic => withMainContext do
  let actions ← catalogue { maxRetrievedNames := 0 }
  unless actions.any (·.text == "exact (h z).symm") do
    throwError "local equality symmetry application was not generated: {actions.map (·.text)}"
  let some node ← searchWith { maxDepth := 1, maxCost := 1, maxRetrievedNames := 0 }
      localTransformationActions identityRanker
    | throwError "local equality symmetry search found no path"
  unless node.path.map (·.text) == ["exact (h z).symm"] do
    throwError "local equality symmetry path is not readable and replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_local_equivalence" : tactic => withMainContext do
  let actions ← catalogue { maxRetrievedNames := 0 }
  unless actions.any (·.text == "apply (h z).mp") do
    throwError "local equivalence transformation was not generated: {actions.map (·.text)}"
  let some node ← searchWith { maxDepth := 2, maxCost := 2, maxRetrievedNames := 0 }
      localTransformationActions identityRanker
    | throwError "local equivalence transformation search found no path"
  unless node.path.map (·.text) == ["apply (h z).mp", "exact hp"] do
    throwError "local equivalence path is not readable and replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_local_projection" : tactic => withMainContext do
  let actions ← catalogue { maxRetrievedNames := 0 }
  unless actions.any (·.text == "exact (h z).1") do
    throwError "local conjunction projection was not generated: {actions.map (·.text)}"
  let some node ← searchWith { maxDepth := 1, maxCost := 1, maxRetrievedNames := 0 }
      localTransformationActions identityRanker
    | throwError "local conjunction projection search found no path"
  unless node.path.map (·.text) == ["exact (h z).1"] do
    throwError "local conjunction projection path is not readable and replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_structural_catalogue_bounds" : tactic => withMainContext do
  let config := {
    maxRetrievedNames := 0, maxDataCases := 1, maxPropCases := 1, maxInductions := 1,
    maxGeneralizingInductions := 1, maxWitnesses := 1, maxDestructures := 1
  }
  let actions ← catalogue config
  let structural := actions.filter fun action =>
    action.text.startsWith "cases " || action.text.startsWith "induction " ||
      action.text.startsWith "refine ⟨" || action.text.startsWith "rcases "
  unless structural.length <= 6 do
    throwError "structural catalogue exceeded its independent bounds: {structural.map (·.text)}"
  unless actions.any (·.text == "rcases h with ⟨jev_h, jev_h1⟩") &&
      actions.any (·.text == "refine ⟨x, ?_⟩") do
    throwError "destructuring or witness action was not type-checked: {actions.map (·.text)}"
  evalTactic (← `(tactic| exact ⟨_, rfl⟩))

elab "jev_test_data_case_replay" : tactic => withMainContext do
  let config : JevLean.Search.Config := { maxDepth := 3, maxCost := 3, maxDataCases := 1, maxInductions := 0 }
  let some node ← searchWith config dataCaseActions identityRanker
    | throwError "data-case search found no path"
  unless node.path.head?.map (·.text) == some "cases tree" do
    throwError "data case was not selected before branch closers: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_generalizing_induction" : tactic => withMainContext do
  let catalogueConfig : JevLean.Search.Config := {
    maxRetrievedNames := 0, maxDataCases := 0, maxInductions := 0,
    maxGeneralizingInductions := 1
  }
  let actions ← catalogue catalogueConfig
  unless actions.any (·.text == "induction xs generalizing acc") do
    throwError "accumulator-generalizing induction was not generated: {actions.map (·.text)}"
  let config : JevLean.Search.Config := {
    maxDepth := 3, maxCost := 3, maxDataCases := 0,
    maxInductions := 0, maxGeneralizingInductions := 1
  }
  let some node ← searchWith config generalizedInductionActions identityRanker
    | throwError "generalizing induction search found no path"
  unless node.path.head?.map (·.text) == some "induction xs generalizing acc" do
    throwError "generalizing induction was not replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_local_application_bound" : tactic => withMainContext do
  let config := {
    maxRetrievedNames := 0, maxLocalApplications := 3,
    maxLocalApplicationTerms := 3, maxLocalApplicationArity := 2
  }
  let actions ← catalogue config
  let specialized := actions.filter fun action => localApplicationText action.text
  unless specialized.length <= config.maxLocalApplications do
    throwError "local application catalogue exceeded its bound: {specialized.map (·.text)}"
  let repeated ← catalogue config
  unless specialized.map (·.text) == (repeated.filter fun action => localApplicationText action.text).map (·.text) do
    throwError "local application catalogue was not deterministic"
  evalTactic (← `(tactic| exact rfl))

elab "jev_test_unfold_goal_head" : tactic => withMainContext do
  let config := { maxDepth := 2, maxCost := 2, maxUnfoldCandidates := 1 }
  let actions ← unfoldActions config
  let repeated ← unfoldActions config
  unless actions.map (·.text) == ["unfold Doubled.length_R"] &&
      actions.map (·.text) == repeated.map (·.text) do
    throwError "goal-head unfolding was not bounded and deterministic: {actions.map (·.text)}"
  let root ← initialNode
  let successors ← expand root actions
  unless successors.map (fun node => node.path.map (·.text)) == [["unfold Doubled.length_R"]] do
    throwError "goal-head unfolding action did not replay: {successors.map (fun node => node.path.map (·.text))}"
  let some action := actions.head? | throwError "missing goal-head unfolding action"
  replay [action]
  evalTactic (← `(tactic| exact ⟨True.intro, rfl⟩))

elab "jev_test_unfold_hypothesis_head" : tactic => withMainContext do
  let config := { maxDepth := 2, maxCost := 2, maxUnfoldCandidates := 1 }
  let actions ← unfoldActions config
  unless actions.map (·.text) == ["unfold isHalting at h"] do
    throwError "hypothesis-head unfolding was not targeted: {actions.map (·.text)}"
  let root ← initialNode
  let successors ← expand root actions
  unless successors.map (fun node => node.path.map (·.text)) == [["unfold isHalting at h"]] do
    throwError "hypothesis-head unfolding action did not replay: {successors.map (fun node => node.path.map (·.text))}"
  let some action := actions.head? | throwError "missing hypothesis-head unfolding action"
  replay [action]
  evalTactic (← `(tactic| assumption))

elab "jev_test_unfold_path_guard" : tactic => withMainContext do
  let actions ← unfoldOnlyActions { maxUnfoldCandidates := 1 }
  let some action := actions.head? | throwError "missing path-guard unfolding action"
  unless (withoutRepeatedUnfolds [] actions).map (·.text) == actions.map (·.text) &&
      (withoutRepeatedUnfolds [action] actions).isEmpty do
    throwError "the same definition was not excluded along one search path"
  evalTactic (← `(tactic| unfold Doubled.length_R; exact ⟨True.intro, rfl⟩))

elab "jev_test_unfold_avoids_irrelevant_definitions" : tactic => withMainContext do
  let actions ← unfoldActions { maxUnfoldCandidates := 4 }
  unless actions.any (·.text == "unfold Doubled.length_R") &&
      actions.all (·.text != "unfold irrelevantDefinition") do
    throwError "unfolding catalogued an irrelevant definition: {actions.map (·.text)}"
  evalTactic (← `(tactic| unfold Doubled.length_R; exact ⟨True.intro, rfl⟩))

elab "jev_test_aesop_rank_seam" : tactic => withMainContext do
  let source : ActionSource := fun config =>
    return (← catalogue config).map fun action => { action with family := "ranked" }
  let some node ← searchWith { maxDepth := 2, maxCost := 2 } source aesopFirst
    | throwError "aesop-first search found no path"
  unless node.path.head?.map (·.text.startsWith "aesop") == some true do
    throwError "injected rank order was not respected"
  replay node.path

elab "jev_test_direct_closure" : tactic => withMainContext do
  let unavailableRanker : ActionRanker := fun _ _ => throwError "direct closure called ranker"
  let (some node, metrics) ← searchWithMetrics {} catalogue unavailableRanker
    | throwError "direct closure did not succeed"
  unless node.path.map (·.text) == ["rfl"] && metrics.jevCalls == 0 &&
      metrics.attemptedTransitions == 1 do
    throwError "direct closure did not prefer rfl without ranking: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_custom_disjunction" : tactic => withMainContext do
  let some node ← searchWith {} disjunctionActions identityRanker
    | throwError "custom disjunction search found no path"
  unless node.path.map (·.text) ==
      ["intro jev_h", "cases jev_h", "right", "assumption", "left", "assumption"] do
    throwError "unexpected custom disjunction path: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_nonclosing_retained" : tactic => withMainContext do
  let root ← initialNode
  let successors ← expand root (← controlledActions {})
  unless successors.any fun node => node.path.map (·.text) == ["intro jev_h"] && !node.goals.isEmpty do
    throwError "a non-closing intro transition was discarded"
  evalTactic (← `(tactic| intro h; exact h))

elab "jev_test_budget_exhaustion" : tactic => withMainContext do
  unless (← searchWith { maxNodes := 0 } controlledActions identityRanker).isNone do
    throwError "node budget did not stop search cleanly"
  unless (← searchWith { maxNodes := 1 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide node budget was reset between branches"
  unless (← searchWith { maxHeartbeats := 1 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide transition budget was not enforced"
  unless (← searchWith { maxWallMs := 0 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide wall budget was not enforced"
  let some fallback ← searchWith { maxJevCalls := 0, maxDepth := 2, maxCost := 2 }
      controlledActions reverseRanker
    | throwError "deterministic fallback found no path"
  unless fallback.path.map (·.text) == ["intro jev_h", "exact jev_h"] do
    throwError "rank-call exhaustion did not retain catalogue order"
  evalTactic (← `(tactic| intro h; exact h))

elab "jev_test_transition_budget" : tactic => withMainContext do
  unless (← searchWith { maxDepth := 1, maxCost := 1, maxHeartbeats := 2 }
      budgetedActions identityRanker).isNone do
    throwError "the scheduler exceeded the transition budget before the closing action"
  let some budgeted ← searchWith { maxDepth := 1, maxCost := 1, maxHeartbeats := 3 }
      budgetedActions identityRanker
    | throwError "the scheduler did not admit the closing action at its transition budget"
  unless budgeted.path.map (·.text) == ["exact h"] do
    throwError "the scheduler did not preserve candidate order under its transition budget"
  replay budgeted.path

elab "jev_test_transpositions" : tactic => withMainContext do
  let (baseline, baselineMetrics) ← searchWithMetrics { maxDepth := 3, maxCost := 3, maxTranspositions := 0 }
    duplicateOutcomeActions identityRanker
  unless baseline.isSome do throwError "baseline search found no path"
  let (first, firstMetrics) ← searchWithMetrics { maxDepth := 3, maxCost := 3, maxTranspositions := 2 }
    duplicateOutcomeActions identityRanker
  logInfo m!"search-diversity baseline nodes={baselineMetrics.expandedNodes} transitions={baselineMetrics.attemptedTransitions} duplicates={baselineMetrics.duplicateSuccessors} wall_ms={baselineMetrics.elapsedMs}; deduplicated nodes={firstMetrics.expandedNodes} transitions={firstMetrics.attemptedTransitions} duplicates={firstMetrics.duplicateSuccessors} wall_ms={firstMetrics.elapsedMs}"
  let some first := first | throwError "deduplicated search found no path"
  unless first.path.map (·.text) == ["constructor", "exact hP", "exact hQ"] do
    throwError "duplicate outcome changed deterministic path: {first.path.map (·.text)}"
  unless firstMetrics.duplicateSuccessors >= 2 && firstMetrics.repeatedActionFamilies > 0 &&
      firstMetrics.transpositionEntries <= 2 do
    throwError "transposition metrics did not record duplicate actions, cycles, and bounded memory: {repr firstMetrics}"
  let (second, _) ← searchWithMetrics { maxDepth := 3, maxCost := 3, maxTranspositions := 2 }
    duplicateOutcomeActions identityRanker
  unless second.map (fun node => node.path.map (·.text)) == some (first.path.map (·.text)) do
    throwError "canonical-state scheduling is not deterministic"
  replay first.path

elab "jev_test_failed_metavariable_branch" : tactic => withMainContext do
  let root ← initialNode
  let actions : List Action := [
    { tacticSyntax := ← `(tactic| refine ⟨(0 : Nat), ?_⟩ <;> fail), text := "failing refine" },
    { tacticSyntax := ← `(tactic| exact ⟨1, rfl⟩), text := "exact ⟨1, rfl⟩" }
  ]
  let successors ← expand root actions
  match successors with
  | [successor] =>
    unless successor.path.map (·.text) == ["exact ⟨1, rfl⟩"] do
      throwError "the successful branch path was displaced"
  | _ => throwError "a failed branch leaked or removed a successful sibling branch"
  evalTactic (← `(tactic| exact ⟨1, rfl⟩))

elab "jev_test_ordered_siblings" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 3, maxCost := 3 }
      orderedSiblingActions identityRanker
    | throwError "ordered sibling search found no path"
  unless node.path.map (·.text) == ["constructor", "exact hP", "exact hQ"] do
    throwError "sibling goal order changed: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_induction_replay" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 4, maxCost := 4 }
      inductionActions identityRanker
    | throwError "induction search found no path"
  unless node.path.map (·.text) ==
      ["induction xs", "simp [JevLean.tally, *]", "simp [JevLean.tally, *]", "omega"] do
    throwError "unexpected induction path: {node.path.map (·.text)}"
  let suggestion ← replaySuggestion node.path 2
  unless suggestion ==
      "induction xs\n  · simp [JevLean.tally, *]\n  · simp [JevLean.tally, *]\n    omega" do
    throwError "unexpected formatted suggestion:\n{suggestion}"

/-- Helper propositions become verified cuts rather than trusted declarations. -/
example (P : Prop) : P → P := by
  jev_test_helper_validation

/-- Helper-cut ranking prose stays separate from replayable source. -/
example (P Q : Prop) (hp : P) (hq : Q) : Q := by
  have jev_h : P := hp
  jev_test_helper_replay_source

/-- Failed candidates leave the original proof state available to later candidates. -/
example (P : Prop) (h : P) : P := by
  jev_test_restoration

/-- Exact duplicates, equivalent constructors, cycles, family telemetry, and bounded tables. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  jev_test_transpositions

/-- Intro, constructor, and exact form a genuine four-step path with sibling goals. -/
example (P : Prop) : P → P ∧ P := by
  jev_test_structural_path

private inductive TestDisj (P Q : Prop) : Prop where
  | inl (p : P)
  | inr (q : Q)

/-- Constructor case binders replay through stable tactics. -/
example : TestDisj P Q → TestDisj Q P := by
  jev_test_custom_disjunction

/-- A successful structural transition is retained even though it leaves a goal. -/
example (P : Prop) : P → P := by
  jev_test_nonclosing_retained

/-- Exhausted node and rank-call budgets fail without mutating the caller state. -/
example (P : Prop) : P → P := by
  jev_test_budget_exhaustion

/-- The scheduler counts every attempted transition before admitting later candidates. -/
example (P : Prop) (h : P) : P := by
  jev_test_transition_budget

/-- Failed refinements cannot leak assigned metavariables into later candidates. -/
example : ∃ n : Nat, n = 1 := by
  jev_test_failed_metavariable_branch

/-- Descendants stay ahead of untouched siblings and retain their local names. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  jev_test_ordered_siblings

/-- Search-generated induction branches retain and replay their induction hypotheses. -/
example (xs : List Nat) : JevLean.tally xs = xs.length := by
  jev_test_induction_replay

/-- Locally generated apply can open a successor that a later exact closes. -/
example (P Q : Prop) (h : P → Q) (hp : P) : Q := by
  jev_test_apply_path

/-- A fixture target is solved by a named global lemma absent from the local catalogue. -/
example : RetrievalTarget 0 := by
  jev_test_global_retrieval

/-- Retrieved global applications open ordinary local proof obligations. -/
example (n : Nat) (h : n = 0) : RetrievalTarget n := by
  jev_test_global_apply_retrieval

/-- Generated equality rewrites solve the acceptance shape and replay from the root state. -/
example (n : Nat) (h : n = 0) : EqualityTarget n = EqualityTarget 0 := by
  jev_test_rewrite_acceptance

/-- Generated simp-only normalization follows an equality rewrite and replays from the root state. -/
example (n : Nat) (h : n = 0) : NormalizationTarget (n = 0) := by
  jev_test_rewrite_closure

/-- Universally quantified local equalities expose a readable symmetric application. -/
example (f : Nat → Nat) (h : ∀ z, z = f z) (z : Nat) : f z = z := by
  jev_test_local_symmetry

/-- Locally supplied equivalences expose their forward transformation. -/
example (P Q : Nat → Prop) (h : ∀ z, P z ↔ Q z) (z : Nat) (hp : P z) : Q z := by
  jev_test_local_equivalence

/-- Local applications expose common projections. -/
example (P Q : Nat → Prop) (h : ∀ z, P z ∧ Q z) (z : Nat) : P z := by
  jev_test_local_projection

/-- Destructuring and existential witnesses use checked, fresh names under independent bounds. -/
example (P Q : Prop) (h : P ∧ Q) (x : Nat) : ∃ n, n = x := by
  have _ := h
  jev_test_structural_catalogue_bounds

/-- ParseTree.one_le_height-style data cases remain replayable before ordered branch closers. -/
example (tree : ParseTree) : 1 ≤ tree.height := by
  jev_test_data_case_replay

/-- exactLengthDFA_evalFrom_val-style accumulator induction generalizes the preceding state. -/
example (acc : Nat) (xs : List Nat) : JevLean.totalFrom acc xs = acc + xs.sum := by
  jev_test_generalizing_induction

/-- Local application generation is bounded despite multiple terms and binders. -/
example (a _b _c _d : Nat) (h : ∀ x y : Nat, x = x → y = y → y = y) :
    h a a rfl rfl = h a a rfl rfl := by
  jev_test_local_application_bound

/-- Goal-head definitions receive bounded replayable unfolding candidates. -/
example (xs : List Nat) : Doubled.length_R xs := by
  jev_test_unfold_goal_head

/-- `stepHalt_of_isHalting`-shaped hypotheses receive targeted replayable unfolding candidates. -/
example (step : TestStep) (h : isHalting step) : step = .halt := by
  jev_test_unfold_hypothesis_head

/-- Repeated definition unfolding is excluded from a search path. -/
example (xs : List Nat) : Doubled.length_R xs := by
  jev_test_unfold_path_guard

/-- Definitions outside the focused goal and hypothesis heads never enter the unfolding catalogue. -/
example (xs : List Nat) : Doubled.length_R xs := by
  jev_test_unfold_avoids_irrelevant_definitions

/-- Tests can force aesop first when actions are passed to the ranker. -/
example (P : Prop) : P → P := by
  jev_test_aesop_rank_seam

example (n : Nat) : n = n := by
  jev_test_direct_closure

/-- The formatted multi-line suggestion replays with explicit branches. -/
example (P : Prop) : P → P ∧ P := by
  intro h
  constructor
  · exact h
  · exact h

/-- Nested continuation lines remain inside the appropriate induction branch. -/
example (xs : List Nat) : JevLean.tally xs = xs.length := by
  induction xs
  · simp [JevLean.tally]
  · simp [JevLean.tally, *]
    omega

/-- Bounded aesop configuration remains valid ordinary tactic source. -/
example (P Q : Prop) : P ∧ Q → Q ∧ P := by
  aesop (config := { terminal := true, maxRuleApplications := 32 })
